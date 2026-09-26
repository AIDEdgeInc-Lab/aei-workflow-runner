"""Workflow definition + execution: multi-link, partial failure, retries, cancellation, checkpoints."""

import json

import pytest

from automation_support import fake_analyzer, fake_result, make_run, row, validation, VERSIONS
from aei_workflow.runner import execute
from aei_workflow.schema import SchemaError
from aei_workflow.workflow import new_workflow, validate_workflow


# -- workflow definition ---------------------------------------------------------------------------

def test_new_workflow_has_ids_units_and_defaults():
    wf = new_workflow("Weekly check", input_path="/tmp/x.csv")
    assert wf["schema"] == "velorona.workflow" and wf["schema_version"] == 1
    assert wf["workflow_id"] and wf["engine"] == "terrestrial-clearance"
    assert wf["params"]["n_samples"] == 50 and "GHz" in wf["param_units"]["frequency_ghz"]
    assert wf["output"]["retain_runs"] is None      # keep everything unless the user says otherwise


@pytest.mark.parametrize("change", [
    {"name": " "}, {"engine": "satellite"}, {"workflow_id": "../evil"},
    {"params": {"k_factor": 0, "n_samples": 50}}, {"params": {"k_factor": 1.3, "n_samples": 1}},
    {"params": {"k_factor": 1.3, "n_samples": 500}}, {"params": {"k_factor": True, "n_samples": 50}},
    {"output": {"retain_runs": 0}}, {"schema_version": 2}, {"schema": "other"},
])
def test_invalid_workflows_are_refused(change):
    wf = {**new_workflow("ok"), **change}
    with pytest.raises(SchemaError):
        validate_workflow(wf)


def test_execution_settings_are_validated():
    wf = new_workflow("ok")
    wf["execution"]["max_attempts"] = 0
    with pytest.raises(SchemaError):
        validate_workflow(wf)


# -- execution -------------------------------------------------------------------------------------

def test_single_link_run_completes():
    run = make_run(rows=[row("ONLY")])
    assert run["status"] == "completed"
    assert run["counts"] == {"input_rows": 1, "rejected": 0, "ok": 1, "failed": 0, "not_run": 0}
    assert run["links"][0]["result"]["los_status"] == "clear"


def test_multi_link_run_records_each_link_with_its_inputs_and_timestamp():
    run = make_run()
    assert run["status"] == "completed" and [l["link_id"] for l in run["links"]] == ["L1", "L2", "L3"]
    for l in run["links"]:
        assert l["status"] == "ok" and l["analyzed_at"] and l["input"]["frequency_ghz"] == 6.0
    assert run["versions"] == VERSIONS
    assert run["provenance"]["historical_replay_supported"] is False
    assert run["provenance"]["data_sources"][0]["observation_time"] is None


def test_one_failing_link_gives_a_partial_run_and_keeps_the_others():
    def analyzer(r, p):
        if r["link_id"] == "L2":
            raise RuntimeError("HTTP 429 from elevation service")
        return fake_result()
    run = make_run(analyzer=analyzer)
    assert run["status"] == "partial"
    l1, l2, l3 = run["links"]
    assert (l1["status"], l2["status"], l3["status"]) == ("ok", "failed", "ok")
    assert l2["result"] is None and "429" in l2["error"]["message"]      # an error is never dressed up as a result
    assert run["counts"]["ok"] == 2 and run["counts"]["failed"] == 1


def test_transient_failures_are_retried_up_to_max_attempts_then_recorded():
    calls = []

    def flaky(r, p):
        calls.append(r["link_id"])
        if r["link_id"] == "L1" and calls.count("L1") < 2:
            raise ConnectionError("timeout")
        if r["link_id"] == "L2":
            raise ConnectionError("down")
        return fake_result()
    run = make_run(analyzer=flaky)
    assert run["links"][0]["status"] == "ok"                       # succeeded on attempt 2
    assert run["links"][1]["status"] == "failed" and run["links"][1]["error"]["attempts"] == 2
    assert calls.count("L2") == 2


def test_engine_value_errors_are_not_retried():
    calls = []

    def analyzer(r, p):
        calls.append(1)
        raise ValueError("frequency_ghz must be positive")
    run = make_run(rows=[row("BAD")], analyzer=analyzer)
    assert len(calls) == 1 and run["status"] == "failed" and run["links"][0]["error"]["attempts"] == 1


def test_run_where_every_link_fails_is_failed_not_completed():
    def boom(r, p):
        raise RuntimeError("service unavailable")
    run = make_run(analyzer=boom)
    assert run["status"] == "failed" and run["counts"]["ok"] == 0


def test_rejected_input_rows_make_an_otherwise_successful_run_partial():
    rej = [{"source_row": 4, "link_id": "X", "reason": "Row 4 (X): not a number. Skipped."}]
    run = make_run(rows=[row("L1"), row("L2", 1)], rejected=rej)
    assert run["status"] == "partial"
    assert run["counts"]["rejected"] == 1 and run["counts"]["input_rows"] == 3
    assert run["rejected_rows"][0]["reason"].startswith("Row 4")


def test_no_valid_links_is_a_failed_run_with_a_reason():
    run = make_run(rows=[], rejected=[{"source_row": 2, "link_id": "X", "reason": "bad"}])
    assert run["status"] == "failed" and run["links"] == []


def test_empty_file_is_not_reported_as_success():
    run = make_run(rows=[])
    assert run["status"] == "failed" and "no data rows" in run["error"]


def test_cancel_keeps_finished_links_and_marks_the_rest_not_run():
    done = []
    run = make_run(is_canceled=lambda: len(done) >= 1, on_progress=lambda i, n, l: done.append(l["link_id"]))
    assert run["status"] == "canceled"
    assert [l["status"] for l in run["links"]] == ["ok", "not_run", "not_run"]
    assert run["links"][0]["result"] is not None and run["counts"]["not_run"] == 2


def test_cancel_before_start_runs_nothing():
    run = make_run(is_canceled=lambda: True)
    assert run["status"] == "canceled" and run["counts"]["ok"] == 0


def test_cancel_during_retry_wait_stops_retrying():
    state = {"calls": 0}

    def analyzer(r, p):
        state["calls"] += 1
        raise ConnectionError("x")
    run = make_run(rows=[row("L1")], analyzer=analyzer, is_canceled=lambda: state["calls"] >= 1)
    assert state["calls"] == 1 and run["links"][0]["status"] == "failed"


def test_progress_reports_every_link_in_order():
    events = []
    make_run(on_progress=lambda done, total, link: events.append((done, total, link["link_id"], link["status"])))
    assert events == [(1, 3, "L1", "ok"), (2, 3, "L2", "ok"), (3, 3, "L3", "ok")]


def test_checkpoints_are_written_while_running_and_end_in_the_final_state():
    snapshots = []
    run = make_run(on_checkpoint=lambda r: snapshots.append(json.loads(json.dumps(r))))
    assert snapshots[0]["status"] == "running" and snapshots[0]["links"][0]["status"] == "not_run"
    assert snapshots[1]["links"][0]["status"] == "ok" and snapshots[1]["status"] == "running"
    assert snapshots[-1]["status"] == "completed" == run["status"]


def test_a_failing_save_is_recorded_and_the_record_is_still_returned():
    def bad_checkpoint(r):
        if r["status"] != "running":
            raise OSError("disk full")
    run = make_run(on_checkpoint=bad_checkpoint)
    assert run["status"] == "completed" and "could not save run: disk full" in run["error"]


def test_a_failing_progress_callback_does_not_report_success():
    def bad_progress(i, n, l):
        raise RuntimeError("ui gone")
    run = make_run(on_progress=bad_progress)
    assert run["status"] == "partial" and "ui gone" in run["error"]
    assert run["links"][0]["status"] == "ok" and run["links"][1]["status"] == "not_run"


def test_non_finite_results_are_stored_as_null_with_a_warning_and_the_record_is_valid_json():
    inf = fake_result()
    inf["clearance_ratio"] = float("inf")
    run = make_run(rows=[row("INF")], analyzer=lambda r, p: inf)
    link = run["links"][0]
    assert link["result"]["clearance_ratio"] is None and "finite" in link["warnings"][0]
    json.dumps(run, allow_nan=False)


def test_near_threshold_link_carries_a_warning():
    run = make_run(rows=[row("N")], analyzer=lambda r, p: fake_result(near=True))
    assert "Near threshold" in run["links"][0]["warnings"][0]


def test_pause_between_links_uses_the_configured_delay():
    pauses = []
    wf = new_workflow("rate limited", execution={"pause_between_links_s": 1.5, "retry_delay_s": 0})
    execute(wf, validation([row("A"), row("B", 1), row("C", 2)]), fake_analyzer, sleep=pauses.append)
    assert pauses == [1.5, 1.5]          # between links, not after the last


def test_run_carries_workflow_snapshot_so_later_edits_do_not_rewrite_history():
    wf = new_workflow("orig", workflow_id="w9", k_factor=1.2)
    run = execute(wf, validation([row("A")]), fake_analyzer, sleep=lambda s: None)
    wf["params"]["k_factor"] = 9.9
    assert run["workflow_snapshot"]["params"]["k_factor"] == 1.2
