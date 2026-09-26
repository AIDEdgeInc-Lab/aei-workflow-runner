"""velorona-run end to end, in real subprocesses. The wrapper (stub_main.py) replaces ONLY the terrain HTTP call and turns any
socket connection into an error, so every passing test here also shows the runner opens no other network path."""

import json
import os
import signal
import subprocess
import sys
import time
import plistlib

import pytest

from svc_support import HERE, SECRET_IDS, cli, popen, read_json, write_links

ENV_FAST = {"STUB_DELAY": "0"}


def wf_file(tmp, links, *extra):
    out = tmp / "wf.json"
    r = cli(["init", "--name", "Nightly check", "--links", str(links), "--out", str(out), "--attempts", "1", *extra])
    assert r.returncode == 0, r.stderr
    return out


@pytest.fixture
def ctx(tmp_path):
    links = write_links(tmp_path / "links.csv")
    return tmp_path, links, str(tmp_path / "store")


def test_manual_end_to_end_init_validate_install_run_status_export_compare_backup_restore(ctx):
    tmp, links, store = ctx
    wf = wf_file(tmp, links, "--retain-hours", "48")
    doc = read_json(wf)
    assert doc["schedule"] == {"kind": "manual"} and doc["output"]["retain_hours"] == 48 and doc["workflow_version"] == 1
    v = cli(["validate", str(wf)])
    assert v.returncode == 0 and "3 link(s) ready" in v.stdout
    assert cli(["install", str(wf), "--store", store]).returncode == 0
    r = cli(["run", "--store", store, "--workflow-id", doc["workflow_id"]])
    assert r.returncode == 0 and "COMPLETED" in r.stdout and "3 analysed" in r.stdout, r.stderr
    run_id = r.stdout.split()[1].rstrip(":")
    st = cli(["status", "--store", store])
    assert st.returncode == 0 and "COMPLETED" in st.stdout and "last COMPLETED run" in st.stdout
    js = json.loads(cli(["status", "--store", store, "--json"]).stdout)
    assert js["workflows"][0]["last_run"]["run_id"] == run_id and js["workflows"][0]["running"] is None
    out = tmp / "export"
    out.mkdir()
    e = cli(["export", "--store", store, "--run", run_id, "--out", str(out)])
    assert e.returncode == 0 and (out / f"velorona-run-{run_id}" / "report.md").exists()
    assert cli(["export", "--store", store, "--run", run_id, "--out", str(out)]).returncode == 2          # never overwrites
    r2 = cli(["run", "--store", store, "--workflow-id", doc["workflow_id"]])
    run2 = r2.stdout.split()[1].rstrip(":")
    c = cli(["compare", store and "--store", store, run_id, run2]) if False else cli(["compare", "--store", store, run_id, run2])
    assert c.returncode == 0 and "LIKE-FOR-LIKE" in c.stdout and "0 changed" in c.stdout
    z = tmp / "b.zip"
    assert cli(["backup", "--store", store, "--out", str(z)]).returncode == 0
    assert cli(["backup", "--store", store, "--out", str(z)]).returncode == 2                            # never overwrites
    other = str(tmp / "other-store")
    rs = cli(["restore", "--store", other, str(z)])
    assert rs.returncode == 0 and "restored 3" in rs.stdout
    assert cli(["restore", "--store", other, str(z)]).stdout.startswith("restored 0; 3 already present")
    d = cli(["import-doc", "--store", str(tmp / "third"), str(out / f"velorona-run-{run_id}" / "run.json")])
    assert d.returncode == 0 and "imported" in d.stdout


def test_exit_codes_reflect_the_outcome(ctx):
    tmp, links, store = ctx
    wf = wf_file(tmp, links)
    cli(["install", str(wf), "--store", store])
    wid = read_json(wf)["workflow_id"]
    partial = cli(["run", "--store", store, "--workflow-id", wid], {"STUB_MODE": "fail_from:3", "STUB_COUNTER": str(tmp / "n1")})
    assert partial.returncode == 10 and "PARTIAL" in partial.stdout
    down = cli(["run", "--store", store, "--workflow-id", wid], {"STUB_MODE": "down"})
    assert down.returncode == 11 and "FAILED" in down.stdout
    assert cli(["run", "--workflow-id", wid], {"VELORONA_STORE": ""}).returncode == 2                     # no store given: explicit error
    assert cli(["run", "--store", store, "--workflow-id", "no-such-workflow"]).returncode == 2
    runs = json.loads(cli(["runs", "--store", store, "--json"]).stdout)["runs"]
    assert sorted(r["status"] for r in runs) == ["failed", "partial"]


def test_the_store_can_come_from_the_environment(ctx):
    tmp, links, store = ctx
    wf = wf_file(tmp, links)
    assert cli(["install", str(wf)], {"VELORONA_STORE": store}).returncode == 0
    assert cli(["run", "--workflow-id", read_json(wf)["workflow_id"]], {"VELORONA_STORE": store}).returncode == 0


def test_invalid_workflows_and_unsupported_schema_versions_are_refused_with_a_reason(ctx):
    tmp, links, store = ctx
    good = read_json(wf_file(tmp, links))
    cases = {"newer.json": ({**good, "schema_version": 2}, "newer Velorona"), "engine.json": ({**good, "engine": "satellite"}, "unsupported engine"),
             "sched.json": ({**good, "schedule": {"kind": "cron", "expr": "* * * * *"}}, "schedule.kind"), "tz.json": ({**good, "timezone": "Mars/Base"}, "unknown time zone"),
             "id.json": ({**good, "workflow_id": "../../etc"}, "workflow_id"), "notdoc.json": ([1, 2], "not a velorona.workflow")}
    for name, (doc, needle) in cases.items():
        p = tmp / name
        p.write_text(json.dumps(doc))
        r = cli(["validate", str(p)])
        assert r.returncode == 3 and needle in r.stderr, (name, r.stderr)
    (tmp / "junk.json").write_text("{nope")
    assert cli(["validate", str(tmp / "junk.json")]).returncode == 3
    assert cli(["install", str(tmp / "newer.json"), "--store", store]).returncode == 3
    assert not os.path.exists(os.path.join(store, "workflows", f"{good['workflow_id']}.json"))


def test_a_workflow_file_is_only_data_extra_fields_are_never_executed_and_a_hostile_input_path_is_refused(ctx):
    tmp, links, store = ctx
    marker = tmp / "pwned"
    doc = read_json(wf_file(tmp, links))
    doc.update(command=f"touch {marker}", hooks={"post": f"touch {marker}"})
    doc["input"]["path"] = "/etc/passwd"
    p = tmp / "hostile.json"
    p.write_text(json.dumps(doc))
    assert cli(["validate", str(p)]).returncode == 3                       # not a .csv: refused, never parsed
    cli(["install", str(p), "--store", store])
    r = cli(["run", "--store", store, "--workflow-id", doc["workflow_id"]])
    assert r.returncode == 11 and "only .csv" in r.stdout and not marker.exists()
    doc["input"]["path"] = str(links)
    p.write_text(json.dumps(doc))
    assert cli(["run", "--store", store, "--workflow", str(p)]).returncode == 0 and not marker.exists()


def test_init_never_overwrites_and_rejects_bad_settings(ctx):
    tmp, links, _ = ctx
    out = tmp / "w.json"
    assert cli(["init", "--name", "a", "--links", links, "--out", str(out)]).returncode == 0
    before = out.read_text()
    assert cli(["init", "--name", "b", "--links", links, "--out", str(out)]).returncode == 2 and out.read_text() == before
    for bad in (["--daily", "25:00"], ["--every-minutes", "1"], ["--weekly", "funday@02:00"], ["--daily", "02:00", "--every-minutes", "10"], ["--timezone", "Nowhere/Land", "--daily", "02:00"], ["--retain-hours", "0"]):
        r = cli(["init", "--name", "c", "--links", links, "--out", str(tmp / "bad.json"), *bad])
        assert r.returncode == 2 and not (tmp / "bad.json").exists(), bad


# ---- scheduled execution through the documented interface (tick) --------------------------------------------------------------------
def test_scheduled_execution_through_tick(ctx):
    tmp, links, store = ctx
    wf = wf_file(tmp, links, "--daily", "02:00", "--timezone", "America/Toronto", "--grace-minutes", "30")
    wid = read_json(wf)["workflow_id"]
    cli(["install", str(wf), "--store", store])
    t = lambda now: cli(["tick", "--store", store, "--now", now])                       # noqa: E731
    a = t("2026-09-29T12:00:00+00:00")
    assert a.returncode == 0 and "scheduling_started" in a.stdout and "next due 2026-09-30T06:00:00+00:00" in a.stdout
    b = t("2026-09-30T06:04:00+00:00")
    assert b.returncode == 0 and "ran (completed)" in b.stdout
    assert "not_due" in t("2026-09-30T06:09:00+00:00").stdout
    runs = json.loads(cli(["runs", "--store", store, "--json"]).stdout)["runs"]
    assert len(runs) == 1
    c = t("2026-10-02T20:00:00+00:00")                                                # two occurrences (Oct 1, Oct 2) were missed by hours
    assert c.returncode == 14 and "missed" in c.stdout
    st = cli(["status", "--store", store, "--now", "2026-10-02T20:00:00+00:00"])
    assert "missed occurrences in stored history: 2" in st.stdout and "last COMPLETED run" in st.stdout and "MISSED" in st.stdout
    d = t("2026-10-03T06:02:00+00:00")
    assert d.returncode == 0 and "ran (completed)" in d.stdout
    all_runs = json.loads(cli(["runs", "--store", store, "--json"]).stdout)["runs"]
    assert sorted(r["status"] for r in all_runs) == ["completed", "completed", "missed", "missed"]
    assert os.path.exists(os.path.join(store, "runs", runs[0]["run_id"], "evidence", "report.md"))


# ---- signals, crashes, competing processes -------------------------------------------------------------------------------------------
def _wait_for(pred, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _slow_setup(tmp, n_links=6):
    links = write_links(tmp / "slow.csv", [f"SECRET-SLOW-{i}" for i in range(n_links)])
    store = str(tmp / "store")
    wf = tmp / "wf.json"
    assert cli(["init", "--name", "slow", "--links", links, "--out", str(wf), "--attempts", "1"]).returncode == 0
    cli(["install", str(wf), "--store", store])
    return store, read_json(wf)["workflow_id"], str(tmp / "count")


def test_sigterm_cancels_cleanly_keeping_finished_links_and_releasing_the_lock(tmp_path):
    store, wid, counter = _slow_setup(tmp_path)
    proc = popen(["run", "--store", store, "--workflow-id", wid], {"STUB_DELAY": "0.4", "STUB_COUNTER": counter})
    assert _wait_for(lambda: os.path.exists(counter) and int(open(counter).read() or 0) >= 2)
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 12 and "CANCELED" in out, err
    run = read_json(next(os.path.join(store, "runs", d, "run.json") for d in os.listdir(os.path.join(store, "runs"))))
    assert run["status"] == "canceled" and 1 <= run["counts"]["ok"] < 6 and run["counts"]["not_run"] > 0
    assert all(l["result"] for l in run["links"] if l["status"] == "ok")
    assert not os.path.exists(os.path.join(store, "locks", f"{wid}.lock"))
    assert os.path.exists(os.path.join(store, "runs", run["run_id"], "evidence", "report.md"))


def test_a_second_run_of_the_same_workflow_is_refused_while_the_first_is_live(tmp_path):
    store, wid, counter = _slow_setup(tmp_path, 3)
    first = popen(["run", "--store", store, "--workflow-id", wid], {"STUB_DELAY": "0.6", "STUB_COUNTER": counter})
    assert _wait_for(lambda: os.path.exists(os.path.join(store, "locks", f"{wid}.lock")))
    second = cli(["run", "--store", store, "--workflow-id", wid])
    assert second.returncode == 75 and "already running" in second.stderr
    out, _ = first.communicate(timeout=30)
    assert first.returncode == 0 and "COMPLETED" in out
    assert len(os.listdir(os.path.join(store, "runs"))) == 1                          # the refused attempt created nothing
    assert cli(["run", "--store", store, "--workflow-id", wid]).returncode == 0        # free again afterwards


def test_sigkill_mid_run_leaves_a_recoverable_interrupted_run_and_no_lost_results(tmp_path):
    store, wid, counter = _slow_setup(tmp_path, 10)          # ~5 s in total, so it is still running when it is killed
    proc = popen(["run", "--store", store, "--workflow-id", wid], {"STUB_DELAY": "0.5", "STUB_COUNTER": counter})
    run_json = lambda: [os.path.join(store, "runs", d, "run.json") for d in os.listdir(os.path.join(store, "runs"))] if os.path.isdir(os.path.join(store, "runs")) else []  # noqa: E731
    assert _wait_for(lambda: os.path.exists(counter) and int(open(counter).read() or 0) >= 6)          # >2 s in: the throttled checkpoint (every 2 s) has landed
    proc.kill()
    proc.communicate()
    (path,) = run_json()
    dead = read_json(path)
    assert dead["status"] == "running" and 1 <= dead["counts"]["ok"] < 10              # finished links are on disk (at most ~2 s of work is not)
    st = cli(["status", "--store", store])
    assert "RUNNING now or lock held" in st.stdout                                      # visible, not hidden
    nxt = cli(["run", "--store", store, "--workflow-id", wid])                          # dead holder recovered automatically
    assert nxt.returncode == 0
    recovered = read_json(path)
    assert recovered["status"] == "interrupted" and recovered["counts"]["ok"] == dead["counts"]["ok"] and recovered["counts"]["not_run"] > 0
    assert "did not finish" in recovered["error"]


def test_unlock_needs_force(ctx):
    tmp, links, store = ctx
    lock = os.path.join(store, "locks", "wf.lock")
    os.makedirs(os.path.dirname(lock))
    open(lock, "w").write("{}")
    assert cli(["unlock", "--store", store, "--workflow-id", "wf"]).returncode == 2 and os.path.exists(lock)
    assert cli(["unlock", "--store", store, "--workflow-id", "wf", "--force"]).returncode == 0 and not os.path.exists(lock)


# ---- privacy, network, headless -----------------------------------------------------------------------------------------------------------
def test_no_network_is_opened_and_no_private_text_reaches_stdout_stderr_or_logs(tmp_path):
    store, wid, counter = _slow_setup(tmp_path, 3)
    # stub_main turns any socket connect into an AssertionError; a passing run therefore made none (terrain call is stubbed).
    r = cli(["run", "--store", store, "--workflow-id", wid], {"STUB_MODE": "down"})
    assert r.returncode == 11 and "AssertionError" not in r.stderr
    logs = "".join(open(os.path.join(store, "logs", f)).read() for f in os.listdir(os.path.join(store, "logs")))
    for secret in ("SECRET-SLOW", "43.6", "links", "simulated elevation outage", str(tmp_path)):
        assert secret not in logs
    tick = cli(["tick", "--store", store])
    assert "SECRET" not in tick.stdout + tick.stderr


def test_the_runner_imports_and_runs_without_qgis_pyqt_or_any_gui_toolkit(tmp_path):
    code = r"""
import sys, importlib.abc
BLOCKED = ("qgis", "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter", "wx", "gi")
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("GUI/QGIS import attempted: " + name)
sys.meta_path.insert(0, Block())
import aei_workflow, aei_workflow.cli, aei_workflow.service, aei_workflow.bounds, aei_workflow.report, aei_workflow.store
from aei_workflow.cli import main
rc = main(["schedule-template", "--kind", "cron", "--store", "/tmp/x"])
assert not any(m.split(".")[0] in BLOCKED for m in sys.modules), [m for m in sys.modules if m.split(".")[0] in BLOCKED]
sys.exit(rc)
"""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([os.path.join(os.path.dirname(HERE), "src"), os.environ.get("PYTHONPATH", "")])}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr


def test_schedule_templates_are_well_formed_and_install_nothing(tmp_path):
    cron = cli(["schedule-template", "--kind", "cron", "--store", str(tmp_path), "--every-minutes", "5", "--python", "/usr/bin/python3"]).stdout
    assert "*/5 * * * *" in cron and "-m aei_workflow tick --store" in cron and str(tmp_path) in cron
    plist = plistlib.loads(cli(["schedule-template", "--kind", "launchd", "--store", str(tmp_path), "--python", "/usr/bin/python3"]).stdout.encode())
    assert plist["StartInterval"] == 300 and plist["ProgramArguments"][:4] == ["/usr/bin/python3", "-m", "aei_workflow", "tick"] and "RunAtLoad" not in plist
    sysd = cli(["schedule-template", "--kind", "systemd", "--store", str(tmp_path)]).stdout
    assert "OnUnitActiveSec=5min" in sysd and "Type=oneshot" in sysd and "Persistent=true" in sysd
    assert not os.path.exists(tmp_path / "logs")
