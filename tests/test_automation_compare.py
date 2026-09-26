"""Run comparison: what changed, why it may have changed, and when a comparison is not valid."""

import copy

from automation_support import fake_result, make_run, row, VERSIONS
from aei_workflow.compare import compare_runs
from aei_workflow.workflow import new_workflow


def _analyzer(overrides=None):
    overrides = overrides or {}
    return lambda r, p: {**fake_result(), **overrides.get(r["link_id"], {})}


def by_id(cmp):
    return {l["link_id"]: l for l in cmp["links"]}


def test_identical_runs_are_comparable_and_unchanged():
    a, b = make_run(), make_run()
    c = compare_runs(a, b)
    assert c["comparable"] and c["reasons"] == []
    assert c["summary"]["results_changed"] == 0 and c["summary"]["compared"] == 3
    assert all(l["note"] == "Unchanged." for l in c["links"])
    assert c["parameter_changes"] == [] and c["version_changes"] == [] and not c["input_file"]["file_changed"]


def test_changed_input_is_attributed_to_the_input_not_the_environment():
    a = make_run()
    b = make_run(rows=[row("L1", 0), row("L2", 1, site_a_height_m=45.0), row("L3", 2)],
                 analyzer=_analyzer({"L2": {"clearance_ratio": 2.2, "los_status": "clear"}}))
    c = compare_runs(a, b)
    l2 = by_id(c)["L2"]
    assert [x["field"] for x in l2["input_changes"]] == ["site_a_height_m"]
    assert "inputs changed (site_a_height_m)" in l2["note"]
    assert c["comparable"]                                    # same method and data source
    assert {x["field"]: round(x["delta"], 6) for x in l2["result_changes"]}["clearance_ratio"] == 0.7


def test_status_change_is_counted():
    a = make_run()
    b = make_run(analyzer=_analyzer({"L1": {"los_status": "marginal", "clearance_ratio": 0.8}}))
    c = compare_runs(a, b)
    assert c["summary"]["los_status_changed"] == 1
    ch = {x["field"]: (x["a"], x["b"]) for x in by_id(c)["L1"]["result_changes"]}
    assert ch["los_status"] == ("clear", "marginal")


def test_result_moved_with_nothing_else_changed_is_unexplained_not_attributed():
    a = make_run()
    b = make_run(analyzer=_analyzer({"L1": {"terrain_clearance_m": 3.0, "clearance_ratio": 0.55, "los_status": "marginal"}}))
    note = by_id(compare_runs(a, b))["L1"]["note"]
    assert "identical, yet the result differs" in note and "cannot observe" in note
    assert "weather" not in note.lower() and "rain" not in note.lower()


def test_engine_version_change_is_reported_and_offered_as_a_possible_cause():
    a = make_run()
    b = make_run(versions={**VERSIONS, "analysis_engine": {"aei-link-clearance": "0.2.0", "aei-geo-features": "0.1.4"}},
                 analyzer=_analyzer({"L1": {"clearance_ratio": 1.4}}))
    c = compare_runs(a, b)
    assert c["version_changes"] == [{"field": "analysis_engine.aei-link-clearance", "a": "0.1.0", "b": "0.2.0"}]
    assert "engine version changed" in by_id(c)["L1"]["note"]
    assert c["comparable"]                                    # a version change is disclosed, not disqualifying


def test_different_parameters_make_the_comparison_invalid_with_reasons():
    a = make_run()
    b = make_run(workflow=new_workflow("Test workflow", workflow_id="wf1", k_factor=1.0, execution={"retry_delay_s": 0}))
    c = compare_runs(a, b)
    assert not c["comparable"] and "'k_factor' differs" in c["reasons"][0]
    assert c["parameter_changes"][0]["field"] == "k_factor"
    assert len(c["links"]) == 3                               # differences are still shown, but flagged invalid


def test_different_data_source_makes_the_comparison_invalid():
    a, b = make_run(), make_run()
    b["provenance"]["data_sources"][0]["name"] = "Some other DEM"
    c = compare_runs(a, b)
    assert not c["comparable"] and c["data_source_changes"][0]["b"] == "Some other DEM"


def test_links_present_in_only_one_run_and_failed_links_are_not_compared():
    a = make_run()
    def analyzer(r, p):
        if r["link_id"] == "L1":
            raise RuntimeError("down")
        return fake_result()
    b = make_run(rows=[row("L1", 0), row("L2", 1), row("NEW", 5)], analyzer=analyzer)
    c = compare_runs(a, b)
    links = by_id(c)
    assert links["L3"]["presence"] == "only_a" and links["NEW"]["presence"] == "only_b"
    assert "nothing to compare" in links["L1"]["note"] and "result_changes" not in links["L1"]
    assert c["summary"]["compared"] == 1


def test_a_failed_or_running_run_cannot_be_a_valid_baseline():
    ok = make_run()
    failed = make_run(analyzer=lambda r, p: (_ for _ in ()).throw(RuntimeError("x")))
    c = compare_runs(ok, failed)
    assert not c["comparable"] and "failed" in " ".join(c["reasons"])
    running = copy.deepcopy(ok)
    running["status"] = "running"
    assert not compare_runs(running, ok)["comparable"]


def test_input_file_change_is_visible_and_the_note_never_claims_a_replay():
    a, b = make_run(), make_run()
    b["input"]["sha256"] = "cd" * 32
    c = compare_runs(a, b)
    assert c["input_file"]["file_changed"] and "reconstruction of past conditions" in c["note"] and "Neither" in c["note"]
