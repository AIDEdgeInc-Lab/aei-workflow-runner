"""Versioned document contracts shared by Velorona QGIS and Velorona Map.

Both products read and write the same two JSON documents:

    velorona.workflow / 1   a saved, reusable analysis configuration
    velorona.run / 1        the immutable record of one execution of it

The version is an integer in the document itself. A reader accepts any version
it knows and refuses (never rewrites) a newer one, so an older install cannot
damage a record written by a newer one. Pure stdlib -- no QGIS import.
"""

from __future__ import annotations

import math
import re

WORKFLOW_SCHEMA = "velorona.workflow"
WORKFLOW_SCHEMA_VERSION = 1
RUN_SCHEMA = "velorona.run"
RUN_SCHEMA_VERSION = 1

ENGINE_TERRESTRIAL = "terrestrial-clearance"
SUPPORTED_ENGINES = (ENGINE_TERRESTRIAL,)

# Run states. "running" only ever exists on disk while a run is in flight (or after a crash, where
# it is reported as "interrupted" -- see store.recover_interrupted).
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"   # every input row analysed successfully
STATUS_PARTIAL = "partial"       # some links analysed, some rejected/failed
STATUS_FAILED = "failed"         # no link produced a result
STATUS_CANCELED = "canceled"     # user stopped it; finished links are kept
STATUS_INTERRUPTED = "interrupted"
STATUS_MISSED = "missed"         # a scheduled occurrence that did not run (machine off, scheduler down, run overlap); has no links

TRIGGER_MANUAL = "manual"
TRIGGER_SCHEDULED = "scheduled"

LINK_OK = "ok"
LINK_FAILED = "failed"
LINK_NOT_RUN = "not_run"

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SchemaError(ValueError):
    """A document is malformed, or written by a newer schema than this install understands."""


def valid_id(value) -> bool:
    """Ids become file names, and can arrive from an imported backup, so they are restricted to a
    conservative character set (no separators, no dots)."""
    return isinstance(value, str) and _ID_RE.match(value) is not None


def check_document(doc, schema: str, known_version: int):
    if not isinstance(doc, dict):
        raise SchemaError(f"not a {schema} document")
    if doc.get("schema") != schema:
        raise SchemaError(f"expected schema '{schema}', found '{doc.get('schema')}'")
    version = doc.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SchemaError(f"{schema}: missing or invalid schema_version")
    if version > known_version:
        raise SchemaError(
            f"{schema} version {version} was written by a newer Velorona than this one "
            f"(understands up to {known_version}). Update Velorona; the file was not changed."
        )
    return version


def json_safe(value):
    """Recursively replace non-finite floats (JSON has no NaN/Infinity) with None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value
