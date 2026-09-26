"""CSV import + validation against the real aei_link_clearance schema (skipped only if the library is absent)."""

import os

import pytest

from automation_support import BOUNDS, HEADER

pytest.importorskip("aei_link_clearance")
from aei_workflow.inputs import InputFileError, read_links_file, validate_links_csv  # noqa: E402

GOOD = "L1,43.65,-79.38,30,43.76,-79.41,30,6.0"


def check(body, header=HEADER):
    return validate_links_csv(f"{header}\n{body}\n", BOUNDS)


def test_schema_comes_from_the_library():
    from aei_link_clearance.batch import REQUIRED_COLUMNS
    assert check(GOOD)["columns"] == REQUIRED_COLUMNS


def test_valid_rows_are_accepted_with_their_row_numbers():
    v = check(GOOD + "\nL2,43.7,-79.4,25,43.8,-79.3,25,11")
    assert [r["link_id"] for r in v["accepted"]] == ["L1", "L2"]
    assert [r["source_row"] for r in v["accepted"]] == [2, 3]
    assert v["rejected"] == []


def test_each_bad_row_is_rejected_with_a_reason_and_good_rows_survive():
    v = check("\n".join([
        GOOD,
        "L2,abc,-79.4,25,43.8,-79.3,25,11",            # library: non-numeric
        "L3,43.7,-79.4,25,43.8,-79.3,25,nan",          # float() accepts nan; we do not
        "L4,43.7,-79.4,25,43.8,-79.3,25,inf",
        "L5,43.7,-79.4,25,43.7,-79.4,25,11",           # same point
        "L6,143.7,-79.4,25,43.8,-79.3,25,11",          # latitude out of range
        "L7,43.7,-79.4,5000,43.8,-79.3,25,11",         # height above bound
        "L8,43.7,-79.4,25,43.8,-79.3,25,500",          # frequency above bound
        ",43.7,-79.4,25,43.8,-79.3,25,11",             # empty id (library)
        "L1,43.7,-79.4,25,43.8,-79.3,25,11",           # duplicate id
        "L10,43.7,-79.4,25,43.8,-79.3,25,11",
    ]))
    assert [r["link_id"] for r in v["accepted"]] == ["L1", "L10"]
    assert [r["source_row"] for r in v["accepted"]] == [2, 12]
    by_row = {r["source_row"]: r["reason"] for r in v["rejected"]}
    assert sorted(by_row) == [3, 4, 5, 6, 7, 8, 9, 10, 11]
    assert "finite" in by_row[4] and "finite" in by_row[5]
    assert "same point" in by_row[6]
    assert "out of range" in by_row[7]
    assert "site_a_height_m" in by_row[8]
    assert "frequency_ghz" in by_row[9]
    assert "empty link_id" in by_row[10]
    assert "already used on row 2" in by_row[11]


def test_duplicate_id_is_named():
    v = check(GOOD + "\n" + GOOD)
    assert len(v["accepted"]) == 1 and "already used on row 2" in v["rejected"][0]["reason"]


def test_missing_required_column_is_a_file_error_naming_it():
    with pytest.raises(InputFileError, match="site_a_lat"):
        validate_links_csv("link_id,site_a_lon\nL1,1\n", BOUNDS)


def test_a_file_in_another_schema_is_refused_not_guessed(tmp_path):
    # A valid point list (as used for QGIS layers), but not the link schema.
    here = os.path.dirname(os.path.abspath(__file__))
    text, _, _ = read_links_file(os.path.join(here, "fixtures", "point_list_other_schema.csv"))
    with pytest.raises(InputFileError, match="Missing required column"):
        validate_links_csv(text, BOUNDS)


def test_empty_file_is_refused():
    with pytest.raises(InputFileError):
        validate_links_csv("   \n", BOUNDS)


def test_utf8_bom_from_excel_does_not_hide_the_first_column(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes(("﻿" + HEADER + "\n" + GOOD + "\n").encode("utf-8"))
    text, sha, name = read_links_file(str(p))
    assert name == "bom.csv" and len(sha) == 64
    assert len(validate_links_csv(text, BOUNDS)["accepted"]) == 1


def test_non_utf8_file_is_refused_with_advice(tmp_path):
    p = tmp_path / "latin.csv"
    p.write_bytes(HEADER.encode() + b"\nL\xe9,1,1,1,1,1,1,1\n")
    with pytest.raises(InputFileError, match="UTF-8"):
        read_links_file(str(p))


def test_blank_lines_do_not_shift_row_numbers_differently_from_the_library():
    v = validate_links_csv(f"{HEADER}\n{GOOD}\n\nL2,x,1,1,1,1,1,1\nL3,43.7,-79.4,25,43.8,-79.3,25,11\n", BOUNDS)
    assert [(r["link_id"], r["source_row"]) for r in v["accepted"]] == [("L1", 2), ("L3", 4)]
    assert v["rejected"][0]["source_row"] == 3
