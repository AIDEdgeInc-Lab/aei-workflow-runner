"""Headless orchestration: run a workflow to a stored, evidenced run; evaluate schedules; report status.

This is what `velorona-run` calls. Everything is a plain function over a store folder, so tests (and any other front end) can
call it directly. No GUI, no QGIS, no network access of its own: the only network use is the analyzer's terrain lookup.

Guarantees, each covered by tests/test_service.py:
  * at most one live run per workflow per store (lock); a dead holder is recovered, an uncertain one is never stolen
  * the run record is written before the first link and after every link (throttled), so a crash keeps finished links
  * an occurrence that could not run is recorded as a 'missed' run with the reason -- never skipped silently, never "completed"
  * schedule state is written BEFORE an occurrence is acted on: a crash yields an interrupted run, not a duplicate one
  * retention only ever moves runs aside; nothing here deletes a run
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from . import engine, report
from .inputs import InputFileError, read_links_file, validate_links_csv
from .locking import LockHeld, WorkflowLock, read_lock
from .logs import log_event
from .runner import execute
from .schedule import next_occurrence, occurrences
from .schema import (
    RUN_SCHEMA, RUN_SCHEMA_VERSION, STATUS_FAILED, STATUS_MISSED, STATUS_RUNNING, TRIGGER_MANUAL, TRIGGER_SCHEDULED,
    SchemaError, valid_id,
)
from .store import RunStore, StoreError, _atomic_write_json, _read_json
from .workflow import DEFAULT_GRACE_MINUTES, utc_now, validate_workflow

MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_WORKFLOW_BYTES = 1024 * 1024
CHECKPOINT_INTERVAL_S = 2.0
MAX_MISSED_RECORDS_PER_TICK = 50

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_INPUT = 0, 1, 2, 3
EXIT_PARTIAL, EXIT_FAILED, EXIT_CANCELED, EXIT_INTERRUPTED, EXIT_MISSED = 10, 11, 12, 13, 14
EXIT_LOCKED = 75
EXIT_BY_STATUS = {"completed": EXIT_OK, "partial": EXIT_PARTIAL, "failed": EXIT_FAILED, "canceled": EXIT_CANCELED,
                  "interrupted": EXIT_INTERRUPTED, "missed": EXIT_MISSED}


class ServiceError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_ERROR):
        super().__init__(message)
        self.exit_code = exit_code


def parse_time(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ServiceError("times must include a UTC offset, e.g. 2026-09-29T02:00:00+00:00", EXIT_USAGE)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# -- store + files ---------------------------------------------------------------------------------------------------

def open_store(root: str, create: bool = True) -> RunStore:
    if not root:
        raise ServiceError("no store folder given: pass --store DIR (or set VELORONA_STORE)", EXIT_USAGE)
    root = os.path.abspath(os.path.expanduser(root))
    if os.path.exists(root) and not os.path.isdir(root):
        raise ServiceError(f"{root} exists and is not a folder", EXIT_USAGE)
    if create:
        for sub in ("workflows", "runs"):
            os.makedirs(os.path.join(root, sub), exist_ok=True)
    return RunStore(root)


def load_workflow_file(path: str, base_dir: str = None) -> dict:
    """Read and validate a workflow document. The file is data: nothing in it is executed, and its input path is only ever
    opened as a .csv file (see resolve_input)."""
    try:
        if os.path.getsize(path) > MAX_WORKFLOW_BYTES:
            raise ServiceError("workflow file is larger than 1 MB; refusing to read it", EXIT_INPUT)
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except OSError as exc:
        raise ServiceError(f"cannot read workflow file: {exc.strerror or exc}", EXIT_INPUT) from exc
    except ValueError as exc:
        raise ServiceError(f"workflow file is not valid JSON: {exc}", EXIT_INPUT) from exc
    try:
        return validate_workflow(doc)
    except SchemaError as exc:
        raise ServiceError(f"invalid workflow: {exc}", EXIT_INPUT) from exc


def absolutize_input(workflow: dict, base_dir: str) -> dict:
    """A relative input path is resolved against the folder of the workflow file, once, at install time. A scheduled job's
    working directory is not something to guess at run time."""
    path = workflow.get("input", {}).get("path")
    if path and not os.path.isabs(path):
        workflow = json.loads(json.dumps(workflow))
        workflow["input"]["path"] = os.path.normpath(os.path.join(base_dir, path))
    return workflow


def resolve_input(path: str) -> tuple:
    """(text, sha256, file name) for a customer-owned CSV file, or for the newest .csv in a customer-owned folder.

    Only regular .csv files up to 50 MB are read, whatever a workflow file claims: a workflow that points at some other file
    is refused rather than parsed."""
    if not path:
        raise InputFileError("the workflow names no input file and none was given with --links")
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(path):
        candidates = [e for e in os.scandir(path) if e.is_file() and e.name.lower().endswith(".csv")]
        if not candidates:
            raise InputFileError("the input folder contains no .csv files")
        newest = max(candidates, key=lambda e: (e.stat().st_mtime, e.name))
        path = newest.path
    if not path.lower().endswith(".csv"):
        raise InputFileError("only .csv input files are accepted")
    if not os.path.isfile(path):
        raise InputFileError("the input file does not exist or is not a regular file")
    if os.path.getsize(path) > MAX_INPUT_BYTES:
        raise InputFileError("the input file is larger than 50 MB")
    return read_links_file(path)


def install_workflow(store: RunStore, workflow: dict, replace: bool = False) -> dict:
    """Put a workflow in the store. Never overwrites silently: an existing id needs replace=True AND a higher workflow_version,
    and the previous definition is kept under workflows/_history/. Returns {'installed', 'replaced_version'}."""
    wid = workflow["workflow_id"]
    try:
        existing = store.load_workflow(wid)
    except StoreError:
        existing = None
    if existing is None:
        store.save_workflow(workflow)
        return {"installed": True, "replaced_version": None}
    old_v, new_v = existing.get("workflow_version", 1), workflow.get("workflow_version", 1)
    if not replace:
        raise ServiceError(f"workflow {wid} is already installed (version {old_v}); use --replace with a higher workflow_version to update it", EXIT_USAGE)
    if new_v <= old_v:
        raise ServiceError(f"workflow {wid}: the new definition is version {new_v} but version {old_v} is installed; raise workflow_version to replace it", EXIT_USAGE)
    hist = os.path.join(store.root, "workflows", "_history")
    os.makedirs(hist, exist_ok=True)
    dest = os.path.join(hist, f"{wid}-v{old_v}-{uuid.uuid4().hex[:6]}.json")
    with open(dest, "x", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
    store.save_workflow(workflow)
    return {"installed": True, "replaced_version": old_v}


# -- records that are not a normal run ------------------------------------------------------------------------------------

def synthetic_run(workflow: dict, status: str, error: str, *, trigger: str, scheduled_for: str = None, source: dict = None,
                  versions: dict = None, run_id: str = None) -> dict:
    """A complete run document with no links: a failed attempt before analysis began, or a missed occurrence."""
    now = utc_now()
    return {
        "schema": RUN_SCHEMA, "schema_version": RUN_SCHEMA_VERSION, "run_id": run_id or uuid.uuid4().hex[:16],
        "workflow_id": workflow["workflow_id"], "workflow_name": workflow["name"], "workflow_version": workflow.get("workflow_version", 1),
        "schedule": {"trigger": trigger, "scheduled_for": scheduled_for, "timezone": workflow.get("timezone", "UTC")},
        "workflow_snapshot": json.loads(json.dumps(workflow)), "status": status, "started_at": now, "finished_at": now,
        "input": {"source_name": (source or {}).get("name"), "sha256": (source or {}).get("sha256"), "columns": None},
        "rejected_rows": [], "links": [],
        "counts": {"input_rows": 0, "rejected": 0, "ok": 0, "failed": 0, "not_run": 0},
        "provenance": engine.collect_provenance(), "assumptions_and_limitations": list(engine.ASSUMPTIONS_AND_LIMITATIONS),
        "versions": versions or {}, "error": error,
    }


# -- one run ------------------------------------------------------------------------------------------------------------------

def run_workflow(store: RunStore, workflow: dict, *, links: str = None, trigger: str = TRIGGER_MANUAL, scheduled_for: str = None,
                 is_canceled=None, analyzer=None, versions: dict = None, sleep=time.sleep, now=None) -> dict:
    """Run one workflow to completion (or failure/cancellation) and return its final run record. Raises ServiceError(EXIT_LOCKED)
    if another run of the same workflow is live."""
    wid = workflow["workflow_id"]
    versions = versions if versions is not None else engine.collect_versions(implementation="velorona-run")
    analyzer = analyzer or engine.analyze_link_row
    is_canceled = is_canceled or (lambda: False)
    try:
        lock = WorkflowLock(store.root, wid).acquire()
    except LockHeld as exc:
        raise ServiceError(f"workflow {wid} is already running or locked: {exc.reason}", EXIT_LOCKED) from exc
    try:
        if lock.recovered_from:
            log_event(store.root, wid, "lock_recovered", reason="recovered_stale_lock")
        # Holding the lock proves no other run of this workflow is alive, so any 'running' record is a dead one.
        for rid in store.recover_interrupted(workflow_id=wid):
            log_event(store.root, wid, "run_interrupted_recovered", run_id=rid, status="interrupted")

        source, validation, problem = None, None, None
        try:
            text, sha, name = resolve_input(links or workflow.get("input", {}).get("path"))
            source = {"name": name, "sha256": sha}
            validation = validate_links_csv(text)
        except InputFileError as exc:
            problem = f"The input could not be used: {exc}"
        if problem:
            run = synthetic_run(workflow, STATUS_FAILED, problem, trigger=trigger, scheduled_for=scheduled_for, source=source, versions=versions)
            store.save_run(run)
            log_event(store.root, wid, "run_finished", run_id=run["run_id"], status=run["status"], trigger=trigger, reason="input_rejected")
            return run

        state = {"last": 0.0}
        log_event(store.root, wid, "run_started", trigger=trigger, scheduled_for=scheduled_for, workflow_version=workflow.get("workflow_version", 1))

        def checkpoint(run):  # every record that reaches disk is complete and valid; throttled while running, always at the end
            t = time.monotonic()
            if run["status"] == STATUS_RUNNING and state["last"] and t - state["last"] < CHECKPOINT_INTERVAL_S:
                return
            store.save_run(run)
            state["last"] = t

        run = execute(workflow, validation, analyzer, source=source, versions=versions, on_checkpoint=checkpoint,
                      is_canceled=is_canceled, sleep=sleep, trigger=trigger, scheduled_for=scheduled_for)
        _finish(store, workflow, run, now)
        return run
    finally:
        lock.release()


def _finish(store: RunStore, workflow: dict, run: dict, now) -> None:
    if workflow["output"].get("write_evidence", True):
        try:
            report.write_package(run, os.path.join(store.root, "runs", run["run_id"], "evidence"))
        except (OSError, ValueError) as exc:
            run["error"] = ((run["error"] + "; ") if run.get("error") else "") + f"evidence files not written: {type(exc).__name__}"
            store.save_run(run)
    try:
        store.apply_retention(workflow, now=now)
    except OSError:
        pass  # housekeeping only; the run is already safely stored
    log_event(store.root, workflow["workflow_id"], "run_finished", run_id=run["run_id"], status=run["status"], counts=run["counts"],
              trigger=run["schedule"]["trigger"], scheduled_for=run["schedule"]["scheduled_for"],
              **({"error_type": "RunError"} if run.get("error") else {}))


# -- schedule evaluation --------------------------------------------------------------------------------------------------------

def _state_path(store: RunStore, wid: str) -> str:
    return os.path.join(store.root, "schedule", f"{wid}.json")


def _read_state(store: RunStore, wid: str):
    try:
        return _read_json(_state_path(store, wid))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise ServiceError(f"schedule state for {wid} is unreadable ({_state_path(store, wid)}); it was left untouched. "
                           "Fix or move that file to continue.", EXIT_ERROR)


def _write_state(store: RunStore, wid: str, state: dict) -> None:
    _atomic_write_json(_state_path(store, wid), {"schema": "velorona.schedule-state", "schema_version": 1, "workflow_id": wid, **state})


def _record_missed(store: RunStore, workflow: dict, when: datetime, reason_code: str, message: str, versions: dict) -> dict:
    run = synthetic_run(workflow, STATUS_MISSED, message, trigger=TRIGGER_SCHEDULED, scheduled_for=iso(when), versions=versions)
    store.save_run(run)
    log_event(store.root, workflow["workflow_id"], "occurrence_missed", run_id=run["run_id"], status=STATUS_MISSED,
              scheduled_for=run["schedule"]["scheduled_for"], reason=reason_code)
    return run


def tick(store: RunStore, now: datetime, *, workflow_ids=None, is_canceled=None, analyzer=None, versions: dict = None,
         sleep=time.sleep) -> list:
    """Evaluate every installed scheduled workflow at `now` (an aware datetime). For each: run the latest due occurrence if it is
    within its grace period, and record every other due occurrence as missed. Returns one result dict per workflow evaluated."""
    now = now.astimezone(timezone.utc)
    versions = versions if versions is not None else engine.collect_versions(implementation="velorona-run")
    workflows, problems = store.list_workflows()
    results = [{"workflow_id": None, "outcome": "unreadable_workflow", "detail": f"{p['file']}: {p['problem']}", "exit_code": EXIT_ERROR} for p in problems]
    for wf in workflows:
        wid = wf["workflow_id"]
        if workflow_ids and wid not in workflow_ids:
            continue
        sched, tzname = wf.get("schedule", {"kind": "manual"}), wf.get("timezone", "UTC")
        if sched["kind"] == "manual":
            continue
        results.append(_tick_one(store, wf, sched, tzname, now, is_canceled, analyzer, versions, sleep))
    return results


def _tick_one(store, wf, sched, tzname, now, is_canceled, analyzer, versions, sleep) -> dict:
    wid = wf["workflow_id"]
    grace = timedelta(minutes=sched.get("grace_minutes", DEFAULT_GRACE_MINUTES))
    state = _read_state(store, wid)
    if state is None:
        # First time this workflow is seen by the scheduler: start counting from now. Occurrences before installation are
        # not "missed" -- nothing had asked for them yet.
        _write_state(store, wid, {"anchor": iso(now), "last_evaluated_at": iso(now), "last_scheduled_for": None, "processed": []})
        return {"workflow_id": wid, "outcome": "scheduling_started", "next_due": _next(sched, tzname, now, now), "exit_code": EXIT_OK}
    last_eval, anchor = parse_time(state["last_evaluated_at"]), parse_time(state["anchor"])
    if now < last_eval:
        return {"workflow_id": wid, "outcome": "clock_went_backwards", "detail": "the system clock is earlier than the last evaluation; nothing was run",
                "exit_code": EXIT_ERROR}
    due, truncated = occurrences(sched, tzname, last_eval, now, anchor)
    if not due:
        _write_state(store, wid, {**state, "last_evaluated_at": iso(now)})
        return {"workflow_id": wid, "outcome": "not_due", "next_due": _next(sched, tzname, now, anchor), "exit_code": EXIT_OK}

    processed = [p for p in state.get("processed", [])]
    latest, earlier = due[-1], due[:-1]
    run_latest = now - latest <= grace
    to_miss = earlier + ([] if run_latest else [latest])
    # At-most-once: persist that these occurrences are being handled BEFORE acting on them.
    processed = (processed + [iso(t) for t in due])[-200:]
    _write_state(store, wid, {**state, "last_evaluated_at": iso(now), "last_scheduled_for": iso(latest), "processed": processed})

    out = {"workflow_id": wid, "outcome": "ran" if run_latest else "missed", "missed": 0, "exit_code": EXIT_OK}
    note = f" {len(due) - MAX_MISSED_RECORDS_PER_TICK} older occurrences were not individually recorded." if len(to_miss) > MAX_MISSED_RECORDS_PER_TICK else ""
    for t in to_miss[-MAX_MISSED_RECORDS_PER_TICK:]:
        late = now - t
        if t is latest:
            code, msg = "beyond_grace", (f"Not run: the scheduler first saw this occurrence {int(late.total_seconds() // 60)} minutes late, "
                                        f"past its {int(grace.total_seconds() // 60)}-minute grace period (computer off or asleep, or the scheduler was not invoked).")
        else:
            code, msg = "machine_unavailable", "Not run: a later occurrence was already due when the scheduler next ran (computer off or asleep, or the scheduler was not invoked)."
        _record_missed(store, wf, t, code, msg + note + (" More occurrences than the recording limit existed." if truncated else ""), versions)
        out["missed"] += 1
    if out["missed"]:
        out["exit_code"] = EXIT_MISSED
    if run_latest:
        try:
            run = run_workflow(store, wf, trigger=TRIGGER_SCHEDULED, scheduled_for=iso(latest), is_canceled=is_canceled,
                               analyzer=analyzer, versions=versions, sleep=sleep, now=now)
            out.update(run_id=run["run_id"], status=run["status"], exit_code=max(out["exit_code"], EXIT_BY_STATUS.get(run["status"], EXIT_ERROR)))
        except ServiceError as exc:
            if exc.exit_code != EXIT_LOCKED:
                raise
            _record_missed(store, wf, latest, "previous_run_in_progress", "Not run: the previous run of this workflow was still in progress at the scheduled time.", versions)
            out.update(outcome="missed", missed=out["missed"] + 1, exit_code=EXIT_MISSED)
    out["next_due"] = _next(sched, tzname, now, anchor)
    return out


def _next(sched, tzname, after, anchor):
    n = next_occurrence(sched, tzname, after, anchor)
    return iso(n) if n else None


# -- status -----------------------------------------------------------------------------------------------------------------------------

def status(store: RunStore, workflow_id: str = None, now: datetime = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    workflows, problems = store.list_workflows()
    rows = []
    for wf in workflows:
        wid = wf["workflow_id"]
        if workflow_id and wid != workflow_id:
            continue
        summaries, _ = store.list_runs(wid)
        last = summaries[0] if summaries else None
        detail = None
        if last:
            full = store.load_run(last["run_id"])
            detail = {"run_id": last["run_id"], "status": last["status"], "started_at": last["started_at"], "finished_at": last["finished_at"],
                      "scheduled_for": full.get("schedule", {}).get("scheduled_for"), "trigger": full.get("schedule", {}).get("trigger"),
                      "counts": last["counts"], "error": last["error"], "input_sha256": full["input"].get("sha256"),
                      "link_errors": sum(1 for l in full["links"] if l["status"] == "failed")}
        last_ok = next((s for s in summaries if s["status"] == "completed"), None)
        state = None
        try:
            state = _read_state(store, wid)
        except ServiceError as exc:
            state = {"unreadable": str(exc)}
        sched, tzname = wf.get("schedule", {"kind": "manual"}), wf.get("timezone", "UTC")
        anchor = parse_time(state["anchor"]) if state and "anchor" in state else now
        rows.append({
            "workflow_id": wid, "name": wf["name"], "workflow_version": wf.get("workflow_version", 1), "schedule": sched, "timezone": tzname,
            "next_due": _next(sched, tzname, now, anchor) if state and "anchor" in state else None,
            "scheduling_started": bool(state and "anchor" in state),
            "running": read_lock(store.root, wid), "last_run": detail,
            "last_completed_run": {"run_id": last_ok["run_id"], "finished_at": last_ok["finished_at"]} if last_ok else None,
            "missed_in_history": sum(1 for s in summaries if s["status"] == STATUS_MISSED),
        })
    return {"store": store.root, "as_of": iso(now), "workflows": rows, "problems": problems}
