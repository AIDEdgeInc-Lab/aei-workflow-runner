"""Headless run service: persistence, evidence, partial failure, cancel, interruption, locking, schedules, retention, privacy.

In-process, with a scripted analyzer standing in for the terrain lookup (the real engine path is covered in test_engine.py
and, end to end through a subprocess, in test_cli.py)."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from automation_support import fake_result
from svc_support import SECRET_IDS, make_workflow, store_at, write_links
from aei_workflow import service
from aei_workflow.locking import WorkflowLock, read_lock
from aei_workflow.service import ServiceError, install_workflow, run_workflow, status, tick
from aei_workflow.workflow import new_workflow

U = timezone.utc
VERS = {"velorona_plugin": None, "implementation": "velorona-run", "analysis_engine": {"aei-link-clearance": "0.1.0"}, "run_schema": 1, "workflow_schema": 1}
ok = lambda row, params: fake_result()  # noqa: E731


def dt(*a):
    return datetime(*a, tzinfo=U)


@pytest.fixture
def env(tmp_path):
    links = write_links(tmp_path / "links.csv")
    return store_at(tmp_path), links, tmp_path


def run(store, wf, **kw):
    kw.setdefault("analyzer", ok)
    kw.setdefault("versions", VERS)
    kw.setdefault("sleep", lambda s: None)
    return run_workflow(store, wf, **kw)


# ---- a run that succeeds ----------------------------------------------------------------------------------------------------------
def test_a_successful_run_persists_the_record_and_its_evidence(env):
    store, links, tmp = env
    wf = make_workflow(links, workflow_version=4)
    r = run(store, wf)
    assert r["status"] == "completed" and r["counts"]["ok"] == 3
    assert r["workflow_version"] == 4 and r["schedule"] == {"trigger": "manual", "scheduled_for": None, "timezone": "UTC"}
    assert r["versions"]["implementation"] == "velorona-run" and r["input"]["sha256"] == hashlib.sha256(open(links, "rb").read()).hexdigest()
    stored = store.load_run(r["run_id"])
    assert stored == r and store.list_runs()[0][0]["status"] == "completed"
    ev = tmp / "customer-store" / "runs" / r["run_id"] / "evidence"
    assert sorted(os.listdir(ev)) == ["input_links.csv", "links.geojson", "manifest.json", "report.md", "results.csv", "run.json"]
    manifest = json.load(open(ev / "manifest.json"))
    for name, sha in manifest["files"].items():
        assert hashlib.sha256((ev / name).read_bytes()).hexdigest() == sha
    assert json.load(open(ev / "run.json")) == r
    assert read_lock(store.root, wf["workflow_id"]) is None           # lock released


def test_evidence_can_be_switched_off_and_is_never_overwritten(env):
    store, links, tmp = env
    r = run(store, make_workflow(links, write_evidence=False))
    assert not (tmp / "customer-store" / "runs" / r["run_id"] / "evidence").exists()
    from aei_workflow import report
    ev = tmp / "e"
    report.write_package(r, str(ev))
    with pytest.raises(FileExistsError):
        report.write_package(r, str(ev))


# ---- failure and partial results ------------------------------------------------------------------------------------------------------
def test_a_failure_partway_keeps_the_completed_links_and_the_evidence(env):
    store, links, _ = env

    def flaky(row, params):
        if row["link_id"] == SECRET_IDS[2]:
            raise ConnectionError("elevation service down")
        return fake_result()
    r = run(store, make_workflow(links), analyzer=flaky)
    assert r["status"] == "partial" and [l["status"] for l in r["links"]] == ["ok", "ok", "failed"]
    assert r["links"][0]["result"] and r["links"][2]["result"] is None and "down" in r["links"][2]["error"]["message"]
    assert store.load_run(r["run_id"])["counts"]["ok"] == 2
    assert os.path.exists(os.path.join(store.root, "runs", r["run_id"], "evidence", "report.md"))


def test_every_link_failing_is_failed_not_completed(env):
    store, links, _ = env
    r = run(store, make_workflow(links), analyzer=lambda row, p: (_ for _ in ()).throw(ConnectionError("down")))
    assert r["status"] == "failed" and r["counts"]["ok"] == 0


def test_an_unusable_input_is_recorded_as_a_failed_run_not_lost(env):
    store, _, tmp = env
    (tmp / "emptydir").mkdir()
    for bad, needle in ((str(tmp / "nope.csv"), "does not exist"), (str(tmp / "emptydir"), "no .csv files"), (str(tmp / "notes.txt"), "only .csv")):
        (tmp / "notes.txt").write_text("x")
        r = run(store, make_workflow(bad))
        assert r["status"] == "failed" and needle in r["error"] and r["links"] == []
        assert store.load_run(r["run_id"])["status"] == "failed"
    (tmp / "wrongcols.csv").write_text("id,name\n1,x\n")
    r = run(store, make_workflow(str(tmp / "wrongcols.csv")))
    assert r["status"] == "failed" and "Missing required column" in r["error"]


def test_a_folder_input_uses_its_newest_csv_only(env):
    store, _, tmp = env
    d = tmp / "drop"
    d.mkdir()
    old = write_links(d / "old.csv", ["OLD-1"])
    new = write_links(d / "new.csv", ["NEW-1", "NEW-2"])
    os.utime(old, (1, 1))
    (d / "later.txt").write_text("ignored")
    r = run(store, make_workflow(str(d)))
    assert r["input"]["source_name"] == "new.csv" and r["counts"]["ok"] == 2


def test_only_csv_files_up_to_the_size_limit_are_ever_read(env, monkeypatch):
    store, _, tmp = env
    (tmp / "secret.json").write_text("{}")
    assert run(store, make_workflow(str(tmp / "secret.json")))["status"] == "failed"
    monkeypatch.setattr(service, "MAX_INPUT_BYTES", 10)
    r = run(store, make_workflow(str(tmp / "links.csv")))
    assert r["status"] == "failed" and "larger than" in r["error"]


# ---- cancellation and interruption -----------------------------------------------------------------------------------------------------
def test_cancel_keeps_finished_links_writes_evidence_and_releases_the_lock(env):
    store, links, _ = env
    calls = []

    def analyzer(row, params):
        calls.append(row["link_id"])
        return fake_result()
    r = run(store, make_workflow(links), analyzer=analyzer, is_canceled=lambda: len(calls) >= 2)
    assert r["status"] == "canceled" and [l["status"] for l in r["links"]] == ["ok", "ok", "not_run"]
    assert store.load_run(r["run_id"])["status"] == "canceled" and read_lock(store.root, "wf-night") is None
    assert os.path.exists(os.path.join(store.root, "runs", r["run_id"], "evidence", "results.csv"))


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_a_crashed_run_is_recovered_as_interrupted_and_the_next_run_proceeds(env):
    store, links, _ = env
    wf = make_workflow(links)
    snaps = []
    from aei_workflow.runner import execute
    from aei_workflow.inputs import validate_links_csv
    execute(wf, validate_links_csv(open(links).read()), ok, on_checkpoint=lambda r: snaps.append(json.loads(json.dumps(r))), sleep=lambda s: None)
    dead = snaps[2]                                       # two links finished, still 'running'
    assert dead["status"] == "running"
    store.save_run(dead)
    os.makedirs(os.path.join(store.root, "locks"), exist_ok=True)
    with open(os.path.join(store.root, "locks", "wf-night.lock"), "w") as f:            # the crashed process's lock
        json.dump({"pid": _dead_pid(), "host": __import__("socket").gethostname(), "started_at": "x", "token": "t"}, f)
    r = run(store, wf)
    assert r["status"] == "completed"
    old = store.load_run(dead["run_id"])
    assert old["status"] == "interrupted" and [l["status"] for l in old["links"]] == ["ok", "ok", "not_run"] and "did not finish" in old["error"]
    log = open(os.path.join(store.root, "logs", "wf-night.jsonl")).read()
    assert "lock_recovered" in log and "run_interrupted_recovered" in log


# ---- one run at a time ------------------------------------------------------------------------------------------------------------------
def test_a_live_lock_blocks_a_second_run_and_it_is_never_stolen(env):
    store, links, _ = env
    wf = make_workflow(links)
    with WorkflowLock(store.root, "wf-night"):
        with pytest.raises(ServiceError) as e:
            run(store, wf)
        assert e.value.exit_code == service.EXIT_LOCKED and "already running" in str(e.value)
        assert read_lock(store.root, "wf-night")["pid"] == os.getpid()            # untouched
    assert run(store, wf)["status"] == "completed"                                   # free again once released


def test_a_lock_from_another_host_or_an_unreadable_lock_is_treated_as_held(env):
    store, links, _ = env
    wf = make_workflow(links)
    path = os.path.join(store.root, "locks", "wf-night.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for content in (json.dumps({"pid": _dead_pid(), "host": "some-other-machine", "token": "t"}), "{not json"):
        open(path, "w").write(content)
        with pytest.raises(ServiceError) as e:
            run(store, wf)
        assert e.value.exit_code == service.EXIT_LOCKED and os.path.exists(path) and open(path).read() == content
    from aei_workflow.locking import force_unlock
    force_unlock(store.root, "wf-night")
    assert run(store, wf)["status"] == "completed"


def test_locks_are_per_workflow(env):
    store, links, _ = env
    with WorkflowLock(store.root, "wf-night"):
        assert run(store, make_workflow(links, workflow_id="wf-other"))["status"] == "completed"


# ---- schedules: due, missed, never duplicated ----------------------------------------------------------------------------------------------
def scheduled(links, **kw):
    return make_workflow(links, schedule={"kind": "daily", "at": "02:00", "grace_minutes": 30}, tz="America/Toronto", **kw)


def do_tick(store, now, **kw):
    kw.setdefault("analyzer", ok)
    kw.setdefault("versions", VERS)
    return tick(store, now, sleep=lambda s: None, **kw)


def test_the_first_tick_only_starts_scheduling_it_does_not_backfill(env):
    store, links, _ = env
    install_workflow(store, scheduled(links))
    (r,) = do_tick(store, dt(2026, 9, 29, 12))
    assert r["outcome"] == "scheduling_started" and store.list_runs()[0] == []
    assert r["next_due"] == "2026-09-30T06:00:00+00:00"                            # 02:00 EDT tomorrow


def test_a_due_occurrence_runs_once_and_a_second_tick_does_not_repeat_it(env):
    store, links, _ = env
    install_workflow(store, scheduled(links))
    do_tick(store, dt(2026, 9, 29, 12))
    (r,) = do_tick(store, dt(2026, 9, 30, 6, 5))                                   # 5 minutes after 02:00 EDT
    assert r["outcome"] == "ran" and r["status"] == "completed" and r["exit_code"] == 0
    run_doc = store.load_run(r["run_id"])
    assert run_doc["schedule"] == {"trigger": "scheduled", "scheduled_for": "2026-09-30T06:00:00+00:00", "timezone": "America/Toronto"}
    assert run_doc["started_at"] != run_doc["schedule"]["scheduled_for"]           # scheduled vs actual start are separate facts
    (again,) = do_tick(store, dt(2026, 9, 30, 6, 10))
    assert again["outcome"] == "not_due" and len(store.list_runs()[0]) == 1


def test_a_machine_that_was_off_gets_missed_records_not_a_pretend_run(env):
    store, links, _ = env
    install_workflow(store, scheduled(links))
    do_tick(store, dt(2026, 9, 29, 12))
    (r,) = do_tick(store, dt(2026, 10, 2, 15))                                     # off for days: 3 occurrences, latest is 9 h old
    assert r["outcome"] == "missed" and r["missed"] == 3 and r["exit_code"] == service.EXIT_MISSED
    runs = [store.load_run(s["run_id"]) for s in store.list_runs()[0]]
    assert len(runs) == 3 and all(x["status"] == "missed" and x["links"] == [] and x["counts"]["ok"] == 0 for x in runs)
    reasons = " ".join(x["error"] for x in runs)
    assert "past its 30-minute grace period" in reasons and "computer off or asleep" in reasons
    assert sorted(x["schedule"]["scheduled_for"] for x in runs) == ["2026-09-30T06:00:00+00:00", "2026-10-01T06:00:00+00:00", "2026-10-02T06:00:00+00:00"]
    st = status(store, now=dt(2026, 10, 2, 15))["workflows"][0]
    assert st["last_run"]["status"] == "missed" and st["last_completed_run"] is None and st["missed_in_history"] == 3


def test_when_the_scheduler_returns_the_latest_due_occurrence_runs_and_the_earlier_ones_are_missed(env):
    store, links, _ = env
    install_workflow(store, scheduled(links))
    do_tick(store, dt(2026, 9, 29, 12))
    (r,) = do_tick(store, dt(2026, 10, 2, 6, 10))                                  # 3 occurrences due, the latest 10 minutes ago
    assert r["outcome"] == "ran" and r["status"] == "completed" and r["missed"] == 2
    statuses = sorted(s["status"] for s in store.list_runs()[0])
    assert statuses == ["completed", "missed", "missed"]


def test_an_overlapping_scheduled_run_is_recorded_as_missed_not_started_twice(env):
    store, links, _ = env
    install_workflow(store, scheduled(links))
    do_tick(store, dt(2026, 9, 29, 12))
    with WorkflowLock(store.root, "wf-night"):
        (r,) = do_tick(store, dt(2026, 9, 30, 6, 5))
    assert r["outcome"] == "missed" and r["exit_code"] == service.EXIT_MISSED
    (rec,) = [store.load_run(s["run_id"]) for s in store.list_runs()[0]]
    assert rec["status"] == "missed" and "still in progress" in rec["error"]


def test_a_crash_after_an_occurrence_was_claimed_never_produces_a_duplicate(env, monkeypatch):
    store, links, _ = env
    install_workflow(store, scheduled(links))
    do_tick(store, dt(2026, 9, 29, 12))

    def boom(*a, **k):
        raise KeyboardInterrupt("machine lost power")
    monkeypatch.setattr(service, "run_workflow", boom)
    with pytest.raises(KeyboardInterrupt):
        do_tick(store, dt(2026, 9, 30, 6, 5))
    monkeypatch.undo()
    (r,) = do_tick(store, dt(2026, 9, 30, 6, 10))
    assert r["outcome"] == "not_due" and store.list_runs()[0] == []               # claimed occurrence is not re-run; no false success


def test_manual_workflows_are_ignored_by_tick_and_backwards_clocks_do_nothing(env):
    store, links, _ = env
    install_workflow(store, make_workflow(links))
    assert do_tick(store, dt(2026, 9, 30, 6)) == []
    install_workflow(store, scheduled(links, workflow_id="wf-sched"))
    do_tick(store, dt(2026, 9, 30, 12))
    (r,) = do_tick(store, dt(2026, 9, 29, 12), workflow_ids={"wf-sched"})
    assert r["outcome"] == "clock_went_backwards" and store.list_runs()[0] == []


def test_a_corrupt_schedule_state_is_reported_and_left_alone(env):
    store, links, _ = env
    install_workflow(store, scheduled(links))
    do_tick(store, dt(2026, 9, 29, 12))
    path = os.path.join(store.root, "schedule", "wf-night.json")
    open(path, "w").write("{corrupt")
    with pytest.raises(ServiceError, match="unreadable"):
        do_tick(store, dt(2026, 9, 30, 6, 5))
    assert open(path).read() == "{corrupt"


# ---- retention ---------------------------------------------------------------------------------------------------------------------------------
def _seed_runs(store, wf, n):
    ids = []
    for i in range(n):
        r = run(store, wf)
        r["started_at"] = r["finished_at"] = f"2026-09-{20 + i:02d}T12:00:00+00:00"
        store.save_run(r)
        ids.append(r["run_id"])
    return ids


def test_retention_by_count_moves_old_runs_aside_and_keeps_their_files(env):
    store, links, tmp = env
    wf = make_workflow(links, retain_runs=2, write_evidence=True)
    ids = _seed_runs(store, make_workflow(links, write_evidence=True), 4)
    moved = store.apply_retention(wf)
    assert sorted(moved) == sorted(ids[:2])
    assert {s["run_id"] for s in store.list_runs()[0]} == set(ids[2:])
    pruned = tmp / "customer-store" / "_pruned" / ids[0]
    assert (pruned / "run.json").exists() and (pruned / "evidence" / "report.md").exists()     # nothing deleted


def test_retention_by_hours_uses_the_customers_window_and_only_that(env):
    store, links, _ = env
    ids = _seed_runs(store, make_workflow(links), 4)                       # finished Sep 20..23 12:00 UTC
    now = dt(2026, 9, 23, 20)
    assert sorted(store.apply_retention(make_workflow(links, retain_hours=10), now=now)) == sorted(ids[:3])
    remaining = {s["run_id"] for s in store.list_runs()[0]}
    assert remaining == {ids[3]}                                            # only the run within 10 h of "now" is kept
    assert store.apply_retention(make_workflow(links, retain_hours=80000), now=now) == []


def test_default_retention_keeps_everything_and_running_runs_are_never_moved(env):
    store, links, _ = env
    ids = _seed_runs(store, make_workflow(links), 3)
    assert store.apply_retention(make_workflow(links)) == []
    live = store.load_run(ids[0])
    live["status"], live["finished_at"] = "running", None
    store.save_run(live)
    moved = store.apply_retention(make_workflow(links, retain_hours=1), now=dt(2027, 1, 1))
    assert ids[0] not in moved and ids[0] in {s["run_id"] for s in store.list_runs()[0]}


def test_a_run_applies_the_workflows_retention_and_never_touches_logs_or_other_workflows(env):
    store, links, _ = env
    other = make_workflow(links, workflow_id="wf-other")
    keep = run(store, other)
    for _ in range(4):
        run(store, make_workflow(links, retain_runs=2))
    assert len(store.list_runs("wf-night")[0]) == 2 and len(store.list_runs("wf-other")[0]) == 1 and keep["run_id"] in {s["run_id"] for s in store.list_runs("wf-other")[0]}
    assert os.path.exists(os.path.join(store.root, "logs", "wf-night.jsonl"))


# ---- workflow versions ---------------------------------------------------------------------------------------------------------------------------
def test_installing_never_overwrites_and_replacing_needs_a_higher_version_and_keeps_the_old_copy(env):
    store, links, tmp = env
    v1 = make_workflow(links)
    assert install_workflow(store, v1)["installed"]
    with pytest.raises(ServiceError, match="already installed"):
        install_workflow(store, v1)
    with pytest.raises(ServiceError, match="raise workflow_version"):
        install_workflow(store, make_workflow(links, workflow_version=1), replace=True)
    v2 = make_workflow(links, workflow_version=2, k_factor=1.2)
    assert install_workflow(store, v2, replace=True)["replaced_version"] == 1
    assert store.load_workflow("wf-night")["params"]["k_factor"] == 1.2
    hist = os.listdir(tmp / "customer-store" / "workflows" / "_history")
    assert len(hist) == 1 and hist[0].startswith("wf-night-v1-")
    assert [w["workflow_id"] for w in store.list_workflows()[0]] == ["wf-night"]        # _history is not listed as a workflow


# ---- privacy ---------------------------------------------------------------------------------------------------------------------------------------------
def test_logs_contain_no_link_ids_coordinates_paths_or_error_text(env):
    store, links, tmp = env

    def analyzer(row, params):
        raise ConnectionError("GET https://api.open-meteo.com/v1/elevation?latitude=43.61,43.62&longitude=-79.38 failed for " + row["link_id"])
    do_tick(store, dt(2026, 9, 29, 12))                                              # (no workflows yet: harmless)
    install_workflow(store, scheduled(links))
    do_tick(store, dt(2026, 9, 29, 12))
    do_tick(store, dt(2026, 10, 2, 6, 10), analyzer=analyzer)
    run(store, make_workflow(links), analyzer=analyzer)
    logs = "".join(open(os.path.join(store.root, "logs", f)).read() for f in os.listdir(os.path.join(store.root, "logs")))
    assert logs
    for secret in SECRET_IDS + ["43.6", "-79.3", "open-meteo", "links.csv", str(tmp), "elevation"]:
        assert secret not in logs, secret
    for line in logs.splitlines():
        assert set(json.loads(line)) <= {"ts", "event", "run_id", "status", "trigger", "scheduled_for", "counts", "exit_code", "error_type", "reason", "workflow_version", "engine"}


def test_the_log_writer_refuses_free_text_fields():
    from aei_workflow.logs import log_event
    with pytest.raises(ValueError):
        log_event("/tmp/none", "wf", "x", message="SECRET-LINK-ALPHA")
    with pytest.raises(ValueError):
        log_event("/tmp/none", "wf", "x", reason="free text about SECRET-LINK-ALPHA")


# ---- backup layout ------------------------------------------------------------------------------------------------------------------------------------------
def test_a_backup_holds_workflows_and_run_json_only_and_restores_into_an_empty_store(env, tmp_path):
    store, links, tmp = env
    install_workflow(store, make_workflow(links))
    r = run(store, make_workflow(links))
    z = str(tmp / "b.zip")
    info = store.export_backup(z)
    import zipfile
    names = sorted(zipfile.ZipFile(z).namelist())
    assert names == sorted(["manifest.json", "workflows/wf-night.json", f"runs/{r['run_id']}/run.json"]) and info["files"] == 2
    fresh = store_at(tmp_path / "second")
    rep = fresh.import_backup(z)
    assert len(rep["added"]) == 2 and not rep["rejected"] and fresh.load_run(r["run_id"]) == r


def test_status_reports_last_run_errors_next_due_and_lock(env):
    store, links, _ = env
    install_workflow(store, scheduled(links))
    do_tick(store, dt(2026, 9, 29, 12))
    do_tick(store, dt(2026, 9, 30, 6, 5), analyzer=lambda r, p: (_ for _ in ()).throw(ConnectionError("down")))
    with WorkflowLock(store.root, "wf-night"):
        w = status(store, now=dt(2026, 9, 30, 7))["workflows"][0]
    assert w["last_run"]["status"] == "failed" and w["last_run"]["link_errors"] == 3 and w["last_run"]["trigger"] == "scheduled"
    assert w["next_due"] == "2026-10-01T06:00:00+00:00" and w["running"]["pid"] == os.getpid() and w["last_completed_run"] is None
