"""A saved, reusable analysis configuration (velorona.workflow/1).

The first engine is Terrestrial Path Clearance over a CSV of links. Its input schema is not defined
here: it is aei_link_clearance.batch.REQUIRED_COLUMNS, read from the library at validation time.
"""

from __future__ import annotations

import copy
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
                 execution: dict = None, retain_runs: int = None, workflow_id: str = None) -> dict:
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
        "output": {"retain_runs": retain_runs},
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
    retain = doc.get("output", {}).get("retain_runs")
    if retain is not None and (not isinstance(retain, int) or isinstance(retain, bool) or retain < 1):
        raise SchemaError("retain_runs must be empty (keep all) or a whole number of 1 or more")
    return doc


def touch(doc: dict) -> dict:
    doc = copy.deepcopy(doc)
    doc["updated_at"] = utc_now()
    return doc
