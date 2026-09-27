"""velorona-run: validate, run, schedule-tick, inspect and export Velorona workflows without any GUI.

    velorona-run init      --name N --links FILE_OR_DIR --out workflow.json [--daily 02:00 --timezone America/Toronto ...]
    velorona-run validate  workflow.json [--links FILE_OR_DIR]
    velorona-run install   workflow.json --store DIR [--replace]
    velorona-run run       --store DIR (--workflow-id ID | --workflow FILE) [--links FILE_OR_DIR]
    velorona-run tick      --store DIR              (what the OS scheduler runs, every few minutes)
    velorona-run status    --store DIR [--json]
    velorona-run runs | export | compare | backup | restore | import-doc | unlock | schedule-template

Exit codes (also what `tick` returns, worst first): 0 ok/nothing due; 10 partial; 11 failed; 12 canceled; 13 interrupted;
14 a scheduled occurrence was missed; 2 usage error; 3 input/workflow file problem; 75 workflow already running; 1 unexpected.

The store folder is chosen by --store or the VELORONA_STORE environment variable; there is deliberately no default location.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from datetime import datetime, timezone

from . import __version__, report
from .compare import compare_runs
from .inputs import InputFileError, read_links_file, validate_links_csv
from .locking import force_unlock
from .service import (
    EXIT_ERROR, EXIT_INPUT, EXIT_OK, EXIT_USAGE, ServiceError, absolutize_input, install_workflow, iso, load_workflow_file, open_store,
    parse_time, resolve_input, run_workflow, status as status_report, tick,
)
from .schema import SchemaError
from .store import StoreError
from .workflow import new_workflow


def _out(args, obj, text: str) -> None:
    print(json.dumps(obj, indent=2) if getattr(args, "json", False) else text)


def _store(args, create=True):
    return open_store(args.store or os.environ.get("VELORONA_STORE"), create=create)


def _load_workflow(args, store):
    if getattr(args, "workflow", None):
        wf = load_workflow_file(args.workflow)
        return absolutize_input(wf, os.path.dirname(os.path.abspath(args.workflow)))
    if getattr(args, "workflow_id", None):
        try:
            return store.load_workflow(args.workflow_id)
        except (StoreError, SchemaError) as exc:
            raise ServiceError(str(exc), EXIT_USAGE) from exc
    raise ServiceError("give --workflow FILE or --workflow-id ID", EXIT_USAGE)


# -- commands ----------------------------------------------------------------------------------------------------------------

def cmd_init(args):
    schedule = {"kind": "manual"}
    chosen = [x for x in (args.every_minutes, args.daily, args.weekly) if x]
    if len(chosen) > 1:
        raise ServiceError("choose only one of --every-minutes, --daily, --weekly", EXIT_USAGE)
    grace = {"grace_minutes": args.grace_minutes} if args.grace_minutes is not None else {}
    if args.every_minutes:
        schedule = {"kind": "interval", "every_minutes": args.every_minutes, **grace}
    elif args.daily:
        schedule = {"kind": "daily", "at": args.daily, **grace}
    elif args.weekly:
        days, _, at = args.weekly.partition("@")
        schedule = {"kind": "weekly", "days": [d.strip().lower() for d in days.split(",") if d.strip()], "at": at, **grace}
    if os.path.exists(args.out):
        raise ServiceError(f"{args.out} already exists; nothing was overwritten", EXIT_USAGE)
    try:
        wf = new_workflow(args.name, input_path=os.path.abspath(args.links), k_factor=args.k_factor, workflow_version=args.workflow_version,
                          schedule=schedule, tz=args.timezone, retain_runs=args.retain_runs, retain_hours=args.retain_hours,
                          write_evidence=not args.no_evidence, execution={"max_attempts": args.attempts, "pause_between_links_s": args.pause_seconds})
    except SchemaError as exc:
        raise ServiceError(f"invalid setting: {exc}", EXIT_USAGE) from exc
    with open(args.out, "x", encoding="utf-8") as f:
        json.dump(wf, f, indent=2)
    _out(args, wf, f"workflow written to {args.out}\n  id: {wf['workflow_id']}  version: {wf['workflow_version']}  schedule: {schedule}  time zone: {wf['timezone']}")
    return EXIT_OK


def cmd_validate(args):
    wf = load_workflow_file(args.workflow)
    wf = absolutize_input(wf, os.path.dirname(os.path.abspath(args.workflow)))
    info = {"workflow_id": wf["workflow_id"], "name": wf["name"], "workflow_version": wf.get("workflow_version", 1), "schedule": wf.get("schedule"),
            "timezone": wf.get("timezone", "UTC"), "workflow_valid": True}
    target = args.links or wf["input"].get("path")
    if target:
        try:
            text, sha, name = resolve_input(target)
            v = validate_links_csv(text)
            info.update(input_file=name, input_sha256=sha, links_ready=len(v["accepted"]), rows_rejected=len(v["rejected"]),
                        rejected=[r["reason"] for r in v["rejected"][:20]])
        except InputFileError as exc:
            info.update(input_error=str(exc))
            _out(args, info, f"workflow OK; input problem: {exc}")
            return EXIT_INPUT
    lines = [f"workflow OK: {info['name']} (id {info['workflow_id']}, version {info['workflow_version']})"]
    if "links_ready" in info:
        lines.append(f"input {info['input_file']}: {info['links_ready']} link(s) ready, {info['rows_rejected']} row(s) rejected")
        lines += [f"  {r}" for r in info["rejected"]]
    else:
        lines.append("no input file to check (none in the workflow and none given with --links)")
    _out(args, info, "\n".join(lines))
    return EXIT_INPUT if info.get("links_ready") == 0 else EXIT_OK


def cmd_install(args):
    store = _store(args)
    wf = absolutize_input(load_workflow_file(args.workflow), os.path.dirname(os.path.abspath(args.workflow)))
    res = install_workflow(store, wf, replace=args.replace)
    _out(args, {**res, "workflow_id": wf["workflow_id"]}, f"workflow {wf['workflow_id']} installed in {store.root}" +
         (f" (replaced version {res['replaced_version']}; the old definition is kept in workflows/_history)" if res["replaced_version"] else ""))
    return EXIT_OK


def cmd_run(args):
    store = _store(args)
    wf = _load_workflow(args, store)
    cancel = threading.Event()
    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, lambda *_: cancel.set())
    try:
        run = run_workflow(store, wf, links=args.links, is_canceled=cancel.is_set)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return _print_run(args, run)


def _print_run(args, run):
    from .service import EXIT_BY_STATUS
    c = run["counts"]
    _out(args, {"run_id": run["run_id"], "status": run["status"], "counts": c, "error": run.get("error")},
         f"run {run['run_id']}: {run['status'].upper()} -- {c['ok']} analysed, {c['failed']} failed, {c['rejected']} rejected, {c['not_run']} not run"
         + (f"\n  {run['error']}" if run.get("error") else ""))
    return EXIT_BY_STATUS.get(run["status"], EXIT_ERROR)


def cmd_tick(args):
    store = _store(args)
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    cancel = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: cancel.set())
    results = tick(store, now, workflow_ids=set(args.workflow_id) if args.workflow_id else None, is_canceled=cancel.is_set)
    lines = []
    for r in results:
        lines.append(f"{r.get('workflow_id') or '-'}: {r['outcome']}" + (f" ({r['status']})" if r.get("status") else "") + (f", {r['missed']} missed" if r.get("missed") else "")
                     + (f", next due {r['next_due']}" if r.get("next_due") else "") + (f" -- {r['detail']}" if r.get("detail") else ""))
    _out(args, results, "\n".join(lines) or "no scheduled workflows installed")
    return max([r["exit_code"] for r in results] or [EXIT_OK])


def cmd_status(args):
    store = _store(args, create=False)
    rep = status_report(store, args.workflow_id, parse_time(args.now) if args.now else None)
    lines = [f"store: {rep['store']}   as of {rep['as_of']}"]
    for w in rep["workflows"]:
        lines.append(f"\n{w['name']}  (id {w['workflow_id']}, v{w['workflow_version']})  schedule: {w['schedule']['kind']}"
                     + (f" {json.dumps({k: v for k, v in w['schedule'].items() if k != 'kind'})}" if len(w["schedule"]) > 1 else "") + f"  tz: {w['timezone']}")
        if w["schedule"]["kind"] != "manual":
            lines.append("  scheduler has not seen this workflow yet (waiting for the first `tick`)" if not w["scheduling_started"] else f"  next due: {w['next_due']}")
        if w["running"]:
            lines.append(f"  RUNNING now or lock held: {w['running']}")
        lr = w["last_run"]
        if lr:
            lines.append(f"  last run {lr['run_id']}: {lr['status'].upper()} ({lr['trigger']}, started {lr['started_at']}, finished {lr['finished_at']})")
            lines.append(f"    links: {lr['counts']['ok']} ok, {lr['counts']['failed']} failed, {lr['counts']['rejected']} rejected, {lr['counts']['not_run']} not run")
            if lr["error"]:
                lines.append(f"    error: {lr['error']}")
        else:
            lines.append("  no runs yet")
        if w["last_completed_run"]:
            lines.append(f"  last COMPLETED run: {w['last_completed_run']['run_id']} at {w['last_completed_run']['finished_at']}")
        if w["missed_in_history"]:
            lines.append(f"  missed occurrences in stored history: {w['missed_in_history']}")
    for p in rep["problems"]:
        lines.append(f"\nUNREADABLE (left untouched): {p['file']}: {p['problem']}")
    _out(args, rep, "\n".join(lines))
    return EXIT_OK


def cmd_runs(args):
    store = _store(args, create=False)
    runs, problems = store.list_runs(args.workflow_id)
    lines = [f"{r['run_id']}  {r['status']:<11} {r['started_at']}  {r['workflow_name']}" for r in runs]
    lines += [f"UNREADABLE (left untouched): {p['file']}: {p['problem']}" for p in problems]
    _out(args, {"runs": runs, "problems": problems}, "\n".join(lines) or "no runs")
    return EXIT_OK


def cmd_export(args):
    store = _store(args, create=False)
    try:
        folder = report.export_package(store.load_run(args.run), args.out)
    except (StoreError, SchemaError, FileExistsError, ValueError) as exc:
        raise ServiceError(str(exc), EXIT_USAGE) from exc
    _out(args, {"folder": folder}, f"result package written to {folder}")
    return EXIT_OK


def cmd_compare(args):
    store = _store(args, create=False)
    try:
        c = compare_runs(store.load_run(args.a), store.load_run(args.b))
    except (StoreError, SchemaError) as exc:
        raise ServiceError(str(exc), EXIT_USAGE) from exc
    s = c["summary"]
    text = [("LIKE-FOR-LIKE" if c["comparable"] else "NOT A VALID BEFORE/AFTER COMPARISON") + f": {s['compared']} compared, {s['results_changed']} changed, "
            f"{s['los_status_changed']} status changes, {s['only_in_first']} only in first, {s['only_in_second']} only in second"] + [f"  {r}" for r in c["reasons"]]
    text += [f"  {l['link_id']}: {l['note']}" for l in c["links"] if l.get("note") and l["note"] != "Unchanged."] + [c["note"]]
    _out(args, c, "\n".join(text))
    return EXIT_OK


def cmd_backup(args):
    store = _store(args, create=False)
    try:
        info = store.export_backup(args.out)
    except StoreError as exc:
        raise ServiceError(str(exc), EXIT_USAGE) from exc
    _out(args, info, f"backed up {info['files']} file(s) to {info['path']}")
    return EXIT_OK


def cmd_restore(args):
    store = _store(args)
    try:
        rep = store.import_backup(args.file)
    except StoreError as exc:
        raise ServiceError(str(exc), EXIT_USAGE) from exc
    _out(args, rep, f"restored {len(rep['added'])}; {len(rep['skipped_existing'])} already present (kept as they were); {len(rep['rejected'])} rejected"
         + "".join(f"\n  {r['file']}: {r['problem']}" for r in rep["rejected"]))
    return EXIT_OK if not rep["rejected"] else EXIT_INPUT


def cmd_import_doc(args):
    store = _store(args)
    try:
        with open(args.file, "r", encoding="utf-8") as f:
            res = store.import_document(f.read())
    except (OSError, StoreError, SchemaError) as exc:
        raise ServiceError(str(exc), EXIT_INPUT) from exc
    _out(args, res, f"{res['kind']} {res['id']}: " + ("imported" if res["added"] else "already present, left as it was"))
    return EXIT_OK


def cmd_unlock(args):
    store = _store(args, create=False)
    if not args.force:
        raise ServiceError("unlock removes a workflow's run lock even if a run is live; re-run with --force if you are sure no run is active", EXIT_USAGE)
    info = force_unlock(store.root, args.workflow_id)
    _out(args, {"removed": info}, f"lock removed ({info})" if info else "no lock was present")
    return EXIT_OK


def cmd_schedule_template(args):
    print(schedule_template(args.kind, os.path.abspath(args.store), args.python or sys.executable, args.every_minutes, args.label))
    return EXIT_OK


def schedule_template(kind: str, store: str, python: str, every_minutes: int, label: str) -> str:
    """Text for the customer's own OS scheduler. Nothing is installed by this program."""
    cmd = f'"{python}" -m aei_workflow tick --store "{store}"'
    log = os.path.join(store, "logs", "scheduler.out")
    if kind == "cron":
        return f"# crontab -e   (Linux, macOS). Runs `tick` every {every_minutes} minutes; tick decides what is due.\n*/{every_minutes} * * * * {cmd} >> \"{log}\" 2>&1"
    if kind == "launchd":
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>{python}</string><string>-m</string><string>aei_workflow</string><string>tick</string><string>--store</string><string>{store}</string>
  </array>
  <key>StartInterval</key><integer>{every_minutes * 60}</integer>
  <key>StandardOutPath</key><string>{log}</string><key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
<!-- save as ~/Library/LaunchAgents/{label}.plist, then: launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{label}.plist -->"""
    if kind == "systemd":
        return f"""# ~/.config/systemd/user/{label}.service
[Unit]
Description=Velorona scheduled workflows (tick)
[Service]
Type=oneshot
ExecStart={python} -m aei_workflow tick --store {store}

# ~/.config/systemd/user/{label}.timer
[Unit]
Description=Run Velorona tick every {every_minutes} minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec={every_minutes}min
Persistent=true
[Install]
WantedBy=timers.target
# then: systemctl --user daemon-reload && systemctl --user enable --now {label}.timer"""
    raise ServiceError("--kind must be cron, launchd or systemd", EXIT_USAGE)


# -- parser ------------------------------------------------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="velorona-run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"velorona-run (aei-workflow-runner) {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help_, store=True, json_=True):
        sp = sub.add_parser(name, help=help_)
        if store:
            sp.add_argument("--store", help="customer-owned folder for workflows, runs, evidence and logs (or set VELORONA_STORE)")
        if json_:
            sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init, "write a new workflow file", store=False)
    sp.add_argument("--name", required=True)
    sp.add_argument("--links", required=True, help="customer-owned CSV file, or a folder (its newest .csv is used each run)")
    sp.add_argument("--out", required=True, help="workflow file to create (never overwritten)")
    sp.add_argument("--k-factor", type=float, default=None)
    sp.add_argument("--workflow-version", type=int, default=1)
    sp.add_argument("--every-minutes", type=int, help="run every N minutes (5 to 10080)")
    sp.add_argument("--daily", metavar="HH:MM", help="run every day at this local time")
    sp.add_argument("--weekly", metavar="mon,thu@HH:MM", help="run on these weekdays at this local time")
    sp.add_argument("--timezone", default="UTC", help="IANA zone for schedule times, e.g. America/Toronto (default UTC)")
    sp.add_argument("--grace-minutes", type=int, default=None, help="how late an occurrence may still run (default 15)")
    sp.add_argument("--retain-runs", type=int, default=None, help="keep the newest N runs; older ones are MOVED to _pruned (default: keep all)")
    sp.add_argument("--retain-hours", type=float, default=None, help="keep runs finished within N hours; older ones are MOVED to _pruned (default: keep all)")
    sp.add_argument("--no-evidence", action="store_true", help="do not write evidence files next to each run.json")
    sp.add_argument("--attempts", type=int, default=2, help="attempts per link when the elevation service fails")
    sp.add_argument("--pause-seconds", type=float, default=0.0, help="wait between links (to stay under a service's rate limit)")

    sp = add("validate", cmd_validate, "check a workflow file and its input without running", store=False)
    sp.add_argument("workflow")
    sp.add_argument("--links")

    sp = add("install", cmd_install, "put a workflow into a store so `tick` can schedule it")
    sp.add_argument("workflow")
    sp.add_argument("--replace", action="store_true", help="replace an installed workflow with a higher workflow_version (old copy is kept)")

    sp = add("run", cmd_run, "run a workflow now (manual run)")
    sp.add_argument("--workflow")
    sp.add_argument("--workflow-id")
    sp.add_argument("--links", help="use this CSV file or folder instead of the workflow's input")

    sp = add("tick", cmd_tick, "evaluate schedules and run what is due (run this from cron/launchd/systemd)")
    sp.add_argument("--workflow-id", action="append")
    sp.add_argument("--now", help="ISO time with offset, for testing or auditing only")

    sp = add("status", cmd_status, "last run, errors, next due, lock state")
    sp.add_argument("--workflow-id")
    sp.add_argument("--now", help=argparse.SUPPRESS)

    sp = add("runs", cmd_runs, "list stored runs")
    sp.add_argument("--workflow-id")

    sp = add("export", cmd_export, "write a run's evidence package to a folder")
    sp.add_argument("--run", required=True)
    sp.add_argument("--out", required=True, help="existing folder to write velorona-run-<id>/ into")

    sp = add("compare", cmd_compare, "compare two runs")
    sp.add_argument("a")
    sp.add_argument("b")

    sp = add("backup", cmd_backup, "write every workflow and run to one zip (never overwrites)")
    sp.add_argument("--out", required=True)

    sp = add("restore", cmd_restore, "merge a backup zip into the store (never overwrites)")
    sp.add_argument("file")

    sp = add("import-doc", cmd_import_doc, "import one run.json or workflow file from Velorona Web or QGIS")
    sp.add_argument("file")

    sp = add("unlock", cmd_unlock, "remove a workflow's run lock (only if you are sure no run is active)")
    sp.add_argument("--workflow-id", required=True)
    sp.add_argument("--force", action="store_true")

    sp = add("schedule-template", cmd_schedule_template, "print cron / launchd / systemd text for the OS scheduler (installs nothing)", json_=False)
    sp.add_argument("--kind", required=True, choices=["cron", "launchd", "systemd"])
    sp.add_argument("--every-minutes", type=int, default=5)
    sp.add_argument("--python", help="python executable that has aei-workflow-runner installed (default: the current one)")
    sp.add_argument("--label", default="ai.aidedge.velorona.tick")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except ServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except (SchemaError, StoreError, InputFileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except OSError as exc:
        print(f"error: {exc.strerror or exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
