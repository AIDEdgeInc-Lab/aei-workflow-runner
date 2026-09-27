"""The contract files and the records other products wrote must keep reading exactly as they do today."""

import json
import os

from aei_workflow import bounds, report
from aei_workflow.compare import compare_runs
from aei_workflow.store import RunStore

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def test_the_committed_bounds_contract_equals_the_code():
    with open(os.path.join(ROOT, "contract", "terrestrial_bounds.json")) as f:
        assert json.load(f) == bounds.contract()
    assert {k: tuple(v) for k, v in bounds.bounds().items()} == {"site_a_height_m": (0.1, 1000.0), "site_b_height_m": (0.1, 1000.0), "frequency_ghz": (0.1, 100.0)}


def test_a_backup_written_by_the_runner_itself_still_restores_and_exports(tmp_path):
    st = RunStore(str(tmp_path / "s"))
    rep = st.import_backup(os.path.join(HERE, "fixtures", "cli_backup_v1.zip"))
    assert not rep["rejected"] and len(rep["added"]) == 6
    runs = [st.load_run(s["run_id"]) for s in st.list_runs()[0]]
    assert sorted(r["status"] for r in runs) == ["completed", "missed", "missed", "missed", "partial"]
    missed = next(r for r in runs if r["status"] == "missed")
    done = next(r for r in runs if r["status"] == "completed")
    assert missed["links"] == [] and missed["schedule"]["trigger"] == "scheduled" and "Not run" in missed["error"]
    assert done["workflow_version"] == 3 and done["schedule"]["timezone"] == "America/Toronto"
    assert compare_runs(done, missed)["comparable"] is False
    folder = report.export_package(missed, str(tmp_path / "out"))
    assert "did **not** happen" in open(os.path.join(folder, "report.md"), encoding="utf-8").read()


def test_the_web_backup_written_by_the_final_browser_build_restores_here(tmp_path):
    st = RunStore(str(tmp_path / "s"))
    rep = st.import_backup(os.path.join(HERE, "fixtures", "web_backup_v1.zip"))
    assert not rep["rejected"] and len(rep["added"]) >= 3
    web = st.load_run(st.list_runs()[0][0]["run_id"])
    assert web["versions"]["implementation"].startswith("velorona-map") and web["schedule"]["trigger"] == "manual" and web["workflow_version"] == 1
