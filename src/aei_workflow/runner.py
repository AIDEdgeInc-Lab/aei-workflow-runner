"""Executes a workflow over validated links and produces a velorona.run/1 record.

Behaviour the record depends on:
  * each link succeeds or fails on its own; a failure never discards results already produced
  * an error is never turned into a result: a failed link has result=None and an error, nothing else
  * a run is 'completed' only if every input row was analysed successfully
  * cancellation is checked between links and between retries; a request already in flight cannot be
    interrupted, so cancel takes effect when it returns (bounded by the library's 15 s HTTP timeout)
  * on_checkpoint is called after every link with the in-progress record, so a crash loses at most
    the link that was running
"""

from __future__ import annotations

import copy
import time
import uuid

from .engine import ASSUMPTIONS_AND_LIMITATIONS, collect_provenance
from .schema import (
    LINK_FAILED, LINK_NOT_RUN, LINK_OK, RUN_SCHEMA, RUN_SCHEMA_VERSION, STATUS_CANCELED, STATUS_COMPLETED,
    STATUS_FAILED, STATUS_PARTIAL, STATUS_RUNNING, json_safe,
)
from .workflow import utc_now, validate_workflow

INPUT_KEYS = ("site_a_lat", "site_a_lon", "site_a_height_m", "site_b_lat", "site_b_lon", "site_b_height_m",
              "frequency_ghz")


def _counts(links: list, rejected: list) -> dict:
    return {
        "input_rows": len(links) + len(rejected),
        "rejected": len(rejected),
        "ok": sum(1 for l in links if l["status"] == LINK_OK),
        "failed": sum(1 for l in links if l["status"] == LINK_FAILED),
        "not_run": sum(1 for l in links if l["status"] == LINK_NOT_RUN),
    }


def final_status(counts: dict, canceled: bool) -> str:
    if canceled:
        return STATUS_CANCELED
    if counts["ok"] == 0:
        return STATUS_FAILED
    if counts["ok"] == counts["input_rows"]:
        return STATUS_COMPLETED
    return STATUS_PARTIAL


def _link_warnings(result: dict) -> list:
    warnings = []
    if result.get("near_threshold"):
        warnings.append("Near threshold: the result is within the elevation-uncertainty band of a status "
                        "boundary, so a different valid terrain source could classify this link differently.")
    if result.get("clearance_ratio") is None:
        warnings.append("Clearance ratio could not be expressed as a finite number for this link.")
    return warnings


def execute(workflow: dict, validation: dict, analyzer, *, source: dict = None, versions: dict = None,
            on_progress=None, on_checkpoint=None, is_canceled=None, sleep=time.sleep) -> dict:
    """Run `workflow` over validation['accepted']; return the finished run record.

    source: {'name','sha256'} of the input file. versions: engine.collect_versions().
    on_progress(done, total, link_entry) fires after each link.
    """
    validate_workflow(workflow)
    accepted, rejected = validation["accepted"], validation["rejected"]
    params, ex = workflow["params"], workflow["execution"]
    is_canceled = is_canceled or (lambda: False)

    links = [{
        "link_id": row["link_id"], "source_row": row.get("source_row"), "status": LINK_NOT_RUN,
        "input": {k: row[k] for k in INPUT_KEYS}, "result": None, "error": None, "warnings": [],
        "analyzed_at": None, "elapsed_s": None,
    } for row in accepted]

    run = {
        "schema": RUN_SCHEMA, "schema_version": RUN_SCHEMA_VERSION,
        "run_id": uuid.uuid4().hex[:16],
        "workflow_id": workflow["workflow_id"], "workflow_name": workflow["name"],
        "workflow_snapshot": copy.deepcopy(workflow),
        "status": STATUS_RUNNING, "started_at": utc_now(), "finished_at": None,
        "input": {"source_name": (source or {}).get("name"), "sha256": (source or {}).get("sha256"),
                  "columns": validation.get("columns")},
        "rejected_rows": copy.deepcopy(rejected),
        "links": links,
        "counts": _counts(links, rejected),
        "provenance": collect_provenance(),
        "assumptions_and_limitations": list(ASSUMPTIONS_AND_LIMITATIONS),
        "versions": versions or {},
        "error": None,
    }
    if on_checkpoint:
        on_checkpoint(run)

    canceled = False
    total = len(links)
    try:
        for index, (link, row) in enumerate(zip(links, accepted)):
            if is_canceled():
                canceled = True
                break
            _run_one(link, row, params, ex, analyzer, is_canceled, sleep)
            run["counts"] = _counts(links, rejected)
            if on_progress:
                on_progress(index + 1, total, link)
            if on_checkpoint:
                on_checkpoint(run)
            if index + 1 < total and ex["pause_between_links_s"] and not is_canceled():
                sleep(ex["pause_between_links_s"])
        if not canceled and is_canceled():
            canceled = any(l["status"] == LINK_NOT_RUN for l in links)
    except Exception as exc:  # runner-level fault (e.g. a progress/checkpoint callback failed)
        run["error"] = f"{type(exc).__name__}: {exc}"

    run["counts"] = _counts(links, rejected)
    run["status"] = final_status(run["counts"], canceled)
    if run["error"] and run["status"] == STATUS_COMPLETED:
        run["status"] = STATUS_PARTIAL
    if run["counts"]["input_rows"] == 0 and not run["error"]:
        run["error"] = "The file contained no data rows."
    run["finished_at"] = utc_now()
    run = json_safe(run)
    if on_checkpoint:
        try:
            on_checkpoint(run)
        except Exception as exc:  # the record is still returned to the caller
            run["error"] = ((run["error"] + "; ") if run["error"] else "") + f"could not save run: {exc}"
    return run


def _run_one(link: dict, row: dict, params: dict, ex: dict, analyzer, is_canceled, sleep) -> None:
    started = time.monotonic()
    link["analyzed_at"] = utc_now()
    attempts = 0
    while True:
        attempts += 1
        try:
            result = analyzer(row, params)
        except ValueError as exc:
            # Deterministic input rejection by the engine: retrying cannot change it.
            link["status"], link["error"] = LINK_FAILED, {"type": type(exc).__name__, "message": str(exc), "attempts": attempts}
            break
        except Exception as exc:  # network/service failure: retried, then recorded -- never a result
            link["error"] = {"type": type(exc).__name__, "message": str(exc), "attempts": attempts}
            if attempts < ex["max_attempts"] and not is_canceled():
                sleep(ex["retry_delay_s"])
                continue
            link["status"] = LINK_FAILED
            break
        else:
            link["status"], link["error"] = LINK_OK, None
            link["result"] = json_safe(result)
            link["warnings"] = _link_warnings(link["result"])
            break
    link["elapsed_s"] = round(time.monotonic() - started, 3)
