"""Import and validation of a links CSV.

The schema and the row parser are aei_link_clearance.batch's (parse_links_csv, REQUIRED_COLUMNS) --
called, not copied. This module adds only checks the library does not make, each of which the Web
Map's importer also makes:

  * non-finite numbers (float() accepts "nan"/"inf")
  * antenna height / frequency bounds -- the shared terrestrial bounds (aei_workflow.bounds, which the
    plugin's Analyze dialog is also built from), so a value that dialog refuses is refused here too
  * Site A and Site B at the same point
  * a repeated link_id (ids are how runs are compared, so they must be unique within a file)

A rejected row is reported with its reason and never analysed, guessed, or clamped.
"""

from __future__ import annotations

import hashlib
import math
import re

_ROW_RE = re.compile(r"^Row (\d+)")


class InputFileError(ValueError):
    """The whole file is unusable (unreadable, empty, or missing required columns)."""


def default_bounds() -> dict:
    """The shared terrestrial bounds (aei_workflow.bounds). No QGIS, no browser."""
    from .bounds import bounds
    return bounds()


def read_links_file(path: str) -> tuple:
    """(text, sha256 of the raw bytes, file name). utf-8-sig drops the BOM Excel's 'CSV UTF-8' adds."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputFileError(f"The file is not UTF-8 text ({exc.reason}). Save it as CSV UTF-8.") from exc
    return text, hashlib.sha256(raw).hexdigest(), path.replace("\\", "/").rsplit("/", 1)[-1]


def validate_links_csv(text: str, bounds: dict = None) -> dict:
    """{'columns': [...], 'accepted': [row + 'source_row'], 'rejected': [{'source_row','link_id','reason'}]}.

    Raises InputFileError for a whole-file problem. source_row counts records the way the library
    does (header = 1)."""
    from aei_link_clearance.batch import REQUIRED_COLUMNS, parse_links_csv
    bounds = default_bounds() if bounds is None else bounds

    text = text.lstrip("﻿")
    try:
        rows, errors = parse_links_csv(text)
    except ValueError as exc:
        raise InputFileError(str(exc)) from exc

    skipped = {}
    for message in errors:
        m = _ROW_RE.match(message)
        if m:
            skipped[int(m.group(1))] = message
    total_records = len(rows) + len(errors)
    kept_rows_numbers = [n for n in range(2, total_records + 2) if n not in skipped]

    rejected = []
    for n in sorted(skipped):
        link_id = re.match(r"^Row \d+ \(([^)]*)\)", skipped[n])
        rejected.append({"source_row": n, "link_id": link_id.group(1) if link_id else "", "reason": skipped[n]})

    accepted, seen = [], {}
    for source_row, row in zip(kept_rows_numbers, rows):
        problems = _extra_problems(row, bounds)
        if row["link_id"] in seen:
            problems.append(f"link_id '{row['link_id']}' already used on row {seen[row['link_id']]}; "
                            "link_ids must be unique so runs can be compared")
        if problems:
            rejected.append({"source_row": source_row, "link_id": row["link_id"],
                             "reason": f"Row {source_row} ({row['link_id']}): {'; '.join(problems)}. Skipped."})
            continue
        seen[row["link_id"]] = source_row
        accepted.append({**row, "source_row": source_row})
    rejected.sort(key=lambda r: r["source_row"])
    return {"columns": list(REQUIRED_COLUMNS), "accepted": accepted, "rejected": rejected}


def _extra_problems(row: dict, bounds: dict) -> list:
    problems = []
    numeric = [k for k, v in row.items() if isinstance(v, float)]
    bad = [k for k in numeric if not math.isfinite(row[k])]
    if bad:
        return [f"not a finite number: {', '.join(bad)}"]
    if row["site_a_lat"] == row["site_b_lat"] and row["site_a_lon"] == row["site_b_lon"]:
        problems.append("Site A and Site B are the same point")
    try:
        from aei_geo_features.errors import InvalidCoordinateError
        from aei_geo_features.geo import validate_coordinate
        for side in ("a", "b"):
            validate_coordinate(row[f"site_{side}_lat"], row[f"site_{side}_lon"])
    except InvalidCoordinateError as exc:
        problems.append(f"coordinate out of range ({exc}); latitude comes before longitude")
    for key, (lo, hi) in bounds.items():
        if key in row and not lo <= row[key] <= hi:
            problems.append(f"{key} {row[key]:g} is outside the accepted range {lo:g} to {hi:g}")
    return problems
