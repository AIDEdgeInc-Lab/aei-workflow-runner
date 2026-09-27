"""Backward compatibility: records written by schema v1 must stay readable, comparable and exportable.

tests/fixtures/*_v1.json were written by the v1 code and are committed as-is. If a future schema
change makes any test here fail, that is a migration to write -- not a fixture to regenerate.
"""

import json
import os

from aei_workflow import report
from aei_workflow.compare import compare_runs
from aei_workflow.store import RunStore
from aei_workflow.workflow import validate_workflow

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


def test_v1_workflow_and_run_load_through_the_store(tmp_path):
    wf, run = load("workflow_v1.json"), load("run_v1.json")
    validate_workflow(wf)
    st = RunStore(str(tmp_path))
    st.save_workflow(wf)
    st.save_run(run)
    assert st.load_run("golden-run-v1") == run and st.load_workflow("golden") == wf
    assert st.list_runs()[0][0]["status"] == "partial"


def test_v1_run_still_compares_and_exports(tmp_path):
    run = load("run_v1.json")
    c = compare_runs(run, run)
    assert c["comparable"] is True and c["reasons"] == []
    assert c["summary"]["results_changed"] == 0
    folder = report.export_package(run, str(tmp_path))
    assert "PARTIAL" in open(os.path.join(folder, "report.md")).read()
