"""Spreadsheet-safe CSV cells -- the same rule as the Web Map's csvCell() (web/evidence.js).

Excel evaluates a cell that starts with = + - @ even inside CSV quotes. A cell that does is written
with a leading apostrophe so it stays text; plain numbers and 'number unit' text ("-210.00 m") are
left alone. Link ids come from the customer's own file, so they are treated as untrusted.
"""

from __future__ import annotations

import csv
import io
import re

_PLAIN_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_NUMBER_WITH_UNIT = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?: [A-Za-z°µ%/]+)+$")
_FORMULA_START = re.compile(r"^[=+\-@]")


def formula_risk(text: str) -> bool:
    t = re.sub(r"^[\s\u0000-\u001f]+", "", text)
    return bool(_FORMULA_START.match(t)) and not _PLAIN_NUMBER.match(t) and not _NUMBER_WITH_UNIT.match(t)


def cell(value):
    if value is None:
        return ""
    if isinstance(value, str) and formula_risk(value):
        return "'" + value
    return value


def comment_line(text) -> str:
    line = "# " + re.sub(r"[\r\n]+", " ", str(text if text is not None else ""))
    risky = '"' in line or any(formula_risk(p) for p in line.split(",")[1:])
    return '"' + line.replace('"', '""') + '"' if risky else line


def rows_to_csv(preamble_lines, header, rows) -> str:
    buf = io.StringIO()
    for line in preamble_lines:
        buf.write(comment_line(line) + "\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for row in rows:
        w.writerow([cell(v) for v in row])
    return buf.getvalue()
