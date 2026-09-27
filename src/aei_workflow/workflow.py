"""A saved, reusable analysis configuration (velorona.workflow/1).

The first engine is Terrestrial Path Clearance over a CSV of links. Its input schema is not defined
here: it is aei_link_clearance.batch.REQUIRED_COLUMNS, read from the library at validation time.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import datetime, timezone

from .schema import (
    ENGINE_TERRESTRIAL, SUPPORTED_ENGINES, WORKFLOW_SCHEMA, WORKFLOW_SCHEMA_VERSION,
    SchemaError, check_document, valid_id,
)

# aei_link_clearance.terrain.DEFAULT_K_FACTOR / DEFAULT_SAMPLE_COUNT, mirrored only as fallbacks for
# a workflow created without them; new_workflow() reads the library's own values when it can.
_FALLBACK_K = 4.0 / 3.0
_FALLBACK_SAMPLES = 50

PARAM_UNITS = {
    "k_factor": "dimensionless (effective earth radius factor)",
    "n_samples": "count (elevation samples along the path)",
    "site_a_height_m": "m above ground", "site_b_height_m": "m above ground",
    "frequency_ghz": "GHz",
}

DEFAULT_EXECUTION = {
    "max_attempts": 2,             # 1 = never retry; a failed elevation request is retried once
    "retry_delay_s": 2.0,
    "pause_between_links_s": 0.0,  # raise to stay under an external service's rate limit
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _library_defaults():
    try:
        from aei_link_clearance.terrain import DEFAULT_K_FACTOR, DEFAULT_SAMPLE_COUNT
        return DEFAULT_K_FACTOR, DEFAULT_SAMPLE_COUNT
    except ImportError:
        return _FALLBACK_K, _FALLBACK_SAMPLES


def new_workflow(name: str, *, input_path: str = None, k_factor: float = None, n_samples: int = None,
                 execution: dict = None, retain_runs: int = None, workflow_id: str = None,
                 workflow_version: int = 1, schedule: dict = None, tz: str = None, retain_hours: float = None,
                 write_evidence: bool = True) -> dict:
    default_k, default_n = _library_defaults()
    now = utc_now()
    doc = {
        "schema": WORKFLOW_SCHEMA,
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "workflow_id": workflow_id or uuid.uuid4().hex[:16],
        "name": name,
        "engine": ENGINE_TERRESTRIAL,
        "created_at": now,
        "updated_at": now,
        # Where the links come from. The path is a convenience for re-running; every run records the
        # file's hash, so a changed file is visible in history rather than silently re-labelled.
        "input": {"kind": "csv", "path": input_path},
        "params": {"k_factor": default_k if k_factor is None else k_factor,
                   "n_samples": default_n if n_samples is None else n_samples},
        "param_units": dict(PARAM_UNITS),
        "execution": {**DEFAULT_EXECUTION, **(execution or {})},
        # None = keep every run. An integer moves older runs to the store's _pruned/ folder
        # (recoverable); nothing is ever deleted by retention.
        # retain_runs / retain_hours: None = no limit on that axis. When either is set, older runs are MOVED to the
        # store's _pruned/ folder; nothing is deleted. write_evidence: the runner writes each run's evidence files
        # (report.md, results.csv, links.geojson, ...) next to run.json.
        "output": {"retain_runs": retain_runs, "retain_hours": retain_hours, "write_evidence": write_evidence},
        # Optional additions (schema stays v1; readers that do not know them ignore them):
        "workflow_version": workflow_version,       # the customer's own revision number of this definition
        "schedule": schedule or {"kind": "manual"},  # only the CLI's `tick` acts on this; Web and QGIS store it untouched
        "timezone": tz or "UTC",                    # IANA name; schedule times are wall-clock in this zone
    }
    validate_workflow(doc)
    return doc


def validate_workflow(doc) -> dict:
    check_document(doc, WORKFLOW_SCHEMA, WORKFLOW_SCHEMA_VERSION)
    if not valid_id(doc.get("workflow_id")):
        raise SchemaError("workflow_id must be 1-64 characters of letters, digits, '_' or '-'")
    if not isinstance(doc.get("name"), str) or not doc["name"].strip():
        raise SchemaError("a workflow needs a name")
    if doc.get("engine") not in SUPPORTED_ENGINES:
        raise SchemaError(f"unsupported engine '{doc.get('engine')}' (supported: {', '.join(SUPPORTED_ENGINES)})")
    params = doc.get("params")
    if not isinstance(params, dict):
        raise SchemaError("params missing")
    k, n = params.get("k_factor"), params.get("n_samples")
    if not isinstance(k, (int, float)) or isinstance(k, bool) or not k > 0:
        raise SchemaError("k_factor must be a positive number")
    if not isinstance(n, int) or isinstance(n, bool) or not 2 <= n <= 100:
        # 100 = Open-Meteo's per-request coordinate limit; the library batches beyond it, but a
        # single request per link is what the rate-limit assumptions in this product are based on.
        raise SchemaError("n_samples must be a whole number from 2 to 100")
    ex = doc.get("execution")
    if not isinstance(ex, dict):
        raise SchemaError("execution missing")
    if not isinstance(ex.get("max_attempts"), int) or not 1 <= ex["max_attempts"] <= 5:
        raise SchemaError("max_attempts must be a whole number from 1 to 5")
    for key in ("retry_delay_s", "pause_between_links_s"):
        v = ex.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            raise SchemaError(f"{key} must be zero or more seconds")
    out = doc.get("output", {})
    retain = out.get("retain_runs")
    if retain is not None and (not isinstance(retain, int) or isinstance(retain, bool) or retain < 1):
        raise SchemaError("retain_runs must be empty (keep all) or a whole number of 1 or more")
    hours = out.get("retain_hours")
    if hours is not None and (not isinstance(hours, (int, float)) or isinstance(hours, bool) or not 0 < hours <= 24 * 3650):
        raise SchemaError("retain_hours must be empty (keep all) or a number of hours greater than 0")
    if "write_evidence" in out and not isinstance(out["write_evidence"], bool):
        raise SchemaError("write_evidence must be true or false")
    version = doc.get("workflow_version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SchemaError("workflow_version must be a whole number of 1 or more")
    path = doc.get("input", {}).get("path")
    if path is not None and (not isinstance(path, str) or "\x00" in path):
        raise SchemaError("input.path must be text")
    validate_timezone(doc.get("timezone", "UTC"))
    validate_schedule(doc.get("schedule", {"kind": "manual"}))
    return doc


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DEFAULT_GRACE_MINUTES = 15


def validate_timezone(name) -> None:
    if not isinstance(name, str) or not name:
        raise SchemaError("timezone must be an IANA time zone name such as 'America/Toronto', or 'UTC'")
    if name == "UTC":
        return
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
    except Exception as exc:  # ZoneInfoNotFoundError, ValueError (bad key), ImportError (no zoneinfo/tzdata)
        raise SchemaError(f"unknown time zone '{name}' ({type(exc).__name__}); use an IANA name such as 'America/Toronto'") from exc


def validate_schedule(s) -> None:
    """A deliberately small vocabulary: manual, every N minutes, daily at a time, or chosen weekdays at a time."""
    if not isinstance(s, dict) or s.get("kind") not in ("manual", "interval", "daily", "weekly"):
        raise SchemaError("schedule.kind must be one of: manual, interval, daily, weekly")
    kind = s["kind"]
    allowed = {"manual": {"kind"}, "interval": {"kind", "every_minutes", "grace_minutes"},
               "daily": {"kind", "at", "grace_minutes"}, "weekly": {"kind", "days", "at", "grace_minutes"}}[kind]
    extra = set(s) - allowed
    if extra:
        raise SchemaError(f"schedule has unsupported field(s): {', '.join(sorted(extra))}")
    if kind == "interval":
        n = s.get("every_minutes")
        if not isinstance(n, int) or isinstance(n, bool) or not 5 <= n <= 7 * 24 * 60:
            raise SchemaError("schedule.every_minutes must be a whole number from 5 to 10080")
    if kind in ("daily", "weekly") and not (isinstance(s.get("at"), str) and _HHMM.match(s["at"])):
        raise SchemaError("schedule.at must be a 24-hour time written HH:MM, for example 02:30")
    if kind == "weekly":
        days = s.get("days")
        if not isinstance(days, list) or not days or len(set(days)) != len(days) or any(d not in DAYS for d in days):
            raise SchemaError("schedule.days must be a non-empty list of distinct days from: " + ", ".join(DAYS))
    g = s.get("grace_minutes", DEFAULT_GRACE_MINUTES)
    if not isinstance(g, int) or isinstance(g, bool) or not 0 <= g <= 24 * 60:
        raise SchemaError("schedule.grace_minutes must be a whole number from 0 to 1440")


def touch(doc: dict) -> dict:
    doc = copy.deepcopy(doc)
    doc["updated_at"] = utc_now()
    return doc
