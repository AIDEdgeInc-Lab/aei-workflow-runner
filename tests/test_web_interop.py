"""A backup written by Velorona Map (real Chromium, IndexedDB, browser-side zip writer) must restore in the plugin.

tests/fixtures/web_backup_v1.zip was produced by tests/js/automation_browser_check.js in the Velorona Map repo (branch
feat/map-automation). The elevation service was stubbed there (flat 100 m terrain), so the results are synthetic even though the
run records name the real terrain source. It holds one workflow and two runs of a 7-row file (5 analysable rows, one of them with a
formula-like link_id, and 2 rejected rows), and is committed as-is: a schema change that breaks reading it needs a migration.
"""

import os

from aei_workflow import report
from aei_workflow.compare import compare_runs
from aei_workflow.store import RunStore

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ZIP = os.path.join(FIX, "web_backup_v1.zip")


def restored(tmp_path):
    st = RunStore(str(tmp_path))
    rep = st.import_backup(ZIP)
    return st, rep


def test_the_plugin_restores_a_backup_made_by_the_web_map(tmp_path):
    st, rep = restored(tmp_path)
    assert rep["rejected"] == [] and rep["skipped_existing"] == []
    assert len([a for a in rep["added"] if a.startswith("run:")]) == 2
    assert len([a for a in rep["added"] if a.startswith("workflow:")]) == 1
    runs, problems = st.list_runs()
    assert not problems and [r["status"] for r in runs] == ["partial", "partial"]
    run = st.load_run(runs[0]["run_id"])
    assert run["versions"]["implementation"].startswith("velorona-map") and run["versions"]["qgis"] is None
    assert run["counts"] == {"input_rows": 7, "rejected": 2, "ok": 5, "failed": 0, "not_run": 0}


def test_web_runs_compare_and_export_in_the_plugin(tmp_path):
    st, _ = restored(tmp_path)
    a, b = (st.load_run(r["run_id"]) for r in reversed(st.list_runs()[0]))
    c = compare_runs(a, b)
    assert c["comparable"] and c["summary"]["compared"] == 5 and c["summary"]["results_changed"] == 0
    folder = report.export_package(a, str(tmp_path / "out"))
    text = open(os.path.join(folder, "results.csv"), encoding="utf-8").read()
    assert "'=HYPERLINK" in text                      # formula-like id from the web run is neutralised by the plugin's export too
    assert "velorona-map" in open(os.path.join(folder, "report.md"), encoding="utf-8").read()


def test_a_web_run_compared_with_a_plugin_run_discloses_the_implementation_difference(tmp_path):
    import json
    st, _ = restored(tmp_path)
    web = st.load_run(st.list_runs()[0][0]["run_id"])
    plugin = json.load(open(os.path.join(FIX, "run_v1.json")))
    c = compare_runs(plugin, web)
    assert any(v["field"] == "implementation" for v in c["version_changes"])
