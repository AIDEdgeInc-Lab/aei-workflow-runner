"""Structured run log: one JSON object per line in <store>/logs/<workflow_id>.jsonl.

Privacy by construction: only the whitelisted fields below can be written, and none of them is free text taken from the
customer's data. There are no link ids, no CSV rows, no coordinates, no file paths and no exception messages (a network error
message can embed the request URL, which carries coordinates). Error TYPES are logged; the full error text stays in the run
record, which is the customer's own data in the customer's own folder.
"""

from __future__ import annotations

import json
import os

from .schema import valid_id
from .workflow import utc_now

ALLOWED = {"run_id", "status", "trigger", "scheduled_for", "counts", "exit_code", "error_type", "reason", "workflow_version", "engine"}
REASONS = {"machine_unavailable", "beyond_grace", "previous_run_in_progress", "not_due", "recovered_stale_lock", "input_rejected"}


def log_event(store_root: str, workflow_id: str, event: str, **fields) -> None:
    if not valid_id(workflow_id):
        return
    unknown = set(fields) - ALLOWED
    if unknown:
        raise ValueError(f"log fields not allowed (privacy whitelist): {sorted(unknown)}")
    if "reason" in fields and fields["reason"] not in REASONS:
        raise ValueError(f"log reason must be one of {sorted(REASONS)}")
    line = json.dumps({"ts": utc_now(), "event": event, **fields}, separators=(",", ":"), sort_keys=True)
    path = os.path.join(store_root, "logs", f"{workflow_id}.jsonl")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # logging must never break a run; the run record is the source of truth
