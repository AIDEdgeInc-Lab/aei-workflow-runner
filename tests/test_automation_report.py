"""Result package: contents, hashes, spreadsheet safety, re-importability of what is claimed re-importable."""

import csv
import hashlib
import io
import json
import os

import pytest

from automation_support import BOUNDS, fake_result, make_run, row
from aei_workflow import report
from aei_workflow.csvsafe import cell, comment_line
from aei_workflow.store import RunStore


def read_csv_body(text):
    return list(csv.reader(l for l in io.StringIO(text) if not l.startswith("#") and not l.startswith('"#')))


def test_package_has_every_file_and_the_manifest_hashes_match(tmp_path):
    run = make_run()
    folder = report.export_package(run, str(tmp_path))
    assert os.path.basename(folder) == f"velorona-run-{run['run_id']}"
    manifest = json.load(open(os.path.join(folder, "manifest.json")))
    assert sorted(manifest["files"]) == ["input_links.csv", "links.geojson", "report.md", "results.csv", "run.json"]
    for name, sha in manifest["files"].items():
        assert hashlib.sha256(open(os.path.join(folder, name), "rb").read()).hexdigest() == sha


def test_package_never_overwrites_and_refuses_a_run_in_progress(tmp_path):
    run = make_run()
    report.export_package(run, str(tmp_path))
    with pytest.raises(FileExistsError):
        report.export_package(run, str(tmp_path))
    running = dict(run, status="running")
    with pytest.raises(ValueError, match="in progress"):
        report.export_package(running, str(tmp_path / "x"))


def test_run_json_in_the_package_is_the_whole_record_and_restores_through_backup(tmp_path):
    run = make_run()
    folder = report.export_package(run, str(tmp_path))
    assert json.load(open(os.path.join(folder, "run.json"))) == run
    st = RunStore(str(tmp_path / "s"))
    st.save_run(run)
    z = str(tmp_path / "b.zip")
    st.export_backup(z)
    fresh = RunStore(str(tmp_path / "t"))
    fresh.import_backup(z)
    assert fresh.load_run(run["run_id"]) == run


def test_results_csv_has_one_row_per_input_row_including_failed_and_rejected():
    def analyzer(r, p):
        if r["link_id"] == "L2":
            raise RuntimeError("HTTP 429")
        return fake_result()
    rej = [{"source_row": 9, "link_id": "R", "reason": "Row 9 (R): not a number. Skipped."}]
    run = make_run(analyzer=analyzer, rejected=rej)
    rows = read_csv_body(report.results_csv(run))
    header, body = rows[0], rows[1:]
    assert header[:3] == ["link_id", "source_row", "status"]
    assert [(r[0], r[2]) for r in body] == [("L1", "ok"), ("L2", "failed"), ("L3", "ok"), ("R", "rejected")]
    assert "HTTP 429" in body[1][header.index("error")] and body[1][header.index("clearance_ratio")] == ""
    assert "not a number" in body[3][header.index("error")]


def test_results_csv_preamble_identifies_run_inputs_engine_and_limits():
    text = report.results_csv(make_run())
    head = "\n".join(l for l in text.splitlines() if l.startswith(("#", '"#')))
    for needle in ("Run:", "Status: completed", "sha256:", "aei-link-clearance 0.1.0", "Parameters:", "Terrain:",
                   "Not a reconstruction of past conditions"):
        assert needle in head


def test_formula_injection_in_a_link_id_is_neutralised_in_results_but_preserved_in_the_record():
    evil = '=HYPERLINK("http://x","click")'
    run = make_run(rows=[row(evil), row("+cmd|calc", 1), row("@SUM(A1)", 2), row("-2+3", 3), row("-210.00 m", 4)])
    body = read_csv_body(report.results_csv(run))[1:]
    ids = [r[0] for r in body]
    assert ids[0] == "'" + evil and ids[1].startswith("'+") and ids[2].startswith("'@") and ids[3].startswith("'-2+3")
    assert ids[4] == "-210.00 m"                     # number-with-unit stays text in Excel; left alone
    assert run["links"][0]["link_id"] == evil        # the record itself is untouched


def test_csvsafe_matches_the_web_maps_rule():
    assert cell("-12.5") == "-12.5" and cell("+3") == "+3" and cell(None) == "" and cell(5) == 5
    assert cell("  =1+1") == "'  =1+1" and cell("\t=1") == "'\t=1" and cell("plain") == "plain"
    assert comment_line("a\nb") == "# a b"
    assert comment_line('Acme, =HYPERLINK("x")').startswith('"# Acme')


def test_input_links_csv_reimports_to_the_same_links():
    pytest.importorskip("aei_link_clearance")
    from aei_workflow.inputs import validate_links_csv
    run = make_run(rows=[row("A", 0), row("B", 1, frequency_ghz=11.0, site_b_height_m=22.5)])
    v = validate_links_csv(report.input_links_csv(run), BOUNDS)
    assert v["rejected"] == []
    assert [{k: r[k] for k in r if k != "source_row"} for r in v["accepted"]] == \
           [{"link_id": l["link_id"], **l["input"]} for l in run["links"]]


def test_geojson_is_lon_lat_ordered_and_only_contains_analysed_links():
    def analyzer(r, p):
        if r["link_id"] == "L2":
            raise RuntimeError("x")
        return fake_result()
    gj = report.geojson(make_run(analyzer=analyzer))
    assert gj["type"] == "FeatureCollection" and [f["properties"]["link_id"] for f in gj["features"]] == ["L1", "L3"]
    assert gj["features"][0]["geometry"]["coordinates"][0] == [-79.38, 43.65]
    assert gj["features"][0]["properties"]["run_id"] and gj["velorona_run"]["status"] == "partial"
    json.dumps(gj, allow_nan=False)


def test_report_states_run_inputs_config_provenance_limits_versions_and_results():
    run = make_run()
    md = report.report_md(run)
    for needle in (run["run_id"], "COMPLETED", "links.csv", "sha256", "k_factor", "Data-source provenance",
                   "Historical replay: **not supported**", "observation time: none provided", "Assumptions and limitations",
                   "not an ITU-R compliance figure", "aei-link-clearance 0.1.0", "| L1 | ok |", "no network equipment was changed"):
        assert needle in md, needle


def test_report_for_a_partial_run_leads_with_what_did_not_happen_and_escapes_table_cells():
    def analyzer(r, p):
        if r["link_id"] == "L|2":
            raise RuntimeError("HTTP 429 | slow down")
        return fake_result()
    run = make_run(rows=[row("L1"), row("L|2", 1)], analyzer=analyzer,
                   rejected=[{"source_row": 5, "link_id": "R", "reason": "Row 5 (R): bad. Skipped."}])
    md = report.report_md(run)
    assert "This run is **partial**: 1 of 3 input rows were analysed (1 failed, 1 rejected, 0 not run)" in md
    assert "L\\|2" in md and "429 \\| slow down" in md and "Rejected input rows" in md


def test_report_does_not_claim_success_for_a_canceled_run():
    done = []
    run = make_run(is_canceled=lambda: bool(done), on_progress=lambda i, n, l: done.append(1))
    md = report.report_md(run)
    assert "CANCELED" in md and "not run (run stopped before this link)" in md and "COMPLETED" not in md
