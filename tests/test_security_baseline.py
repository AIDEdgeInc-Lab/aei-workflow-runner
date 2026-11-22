"""Security baseline (Phase 4): scheduler text that cannot be injected into, atomic writes that do not follow a planted file, and
an export that refuses a run id that is not a safe folder name. The controls that already existed (csvsafe, O_EXCL locks, privacy
whitelist for logs, zip-member validation, input bounds) keep their own tests in the other test files."""
import os
import plistlib

import pytest

from aei_workflow import report, store as store_mod
from aei_workflow.cli import schedule_template
from aei_workflow.service import ServiceError


# --- schedule-template ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["cron", "systemd"])
@pytest.mark.parametrize("store", ['/data/$(touch /tmp/pwned)', '/data/`id`', '/data/"q"', "/data/100%", "/data/a\nb", "/data/back\\slash"])
def test_paths_a_shell_or_systemd_would_interpret_are_refused(kind, store):
    with pytest.raises(ServiceError):
        schedule_template(kind, store, "/usr/bin/python3", 5, "ai.aidedge.velorona.tick")


@pytest.mark.parametrize("kind", ["cron", "systemd"])
def test_python_path_is_checked_too(kind):
    with pytest.raises(ServiceError, match="--python"):
        schedule_template(kind, "/data/store", "/opt/$(evil)/python", 5, "ai.aidedge.velorona.tick")


def test_launchd_plist_escapes_xml_and_still_parses():
    text = schedule_template("launchd", "/data/R&D <team>", "/usr/bin/python3", 5, "ai.aidedge.velorona.tick")
    plist = plistlib.loads(text.split("\n<!--")[0].encode())
    assert plist["ProgramArguments"][-1] == "/data/R&D <team>"
    assert plist["StandardOutPath"] == "/data/R&D <team>/logs/scheduler.out"


@pytest.mark.parametrize("label", ["", "a b", "x</string><key>RunAtLoad</key><true/><string>", "../evil", "a;b", "x" * 129])
@pytest.mark.parametrize("kind", ["cron", "launchd", "systemd"])
def test_label_is_restricted(kind, label):
    with pytest.raises(ServiceError, match="--label"):
        schedule_template(kind, "/data/store", "/usr/bin/python3", 5, label)


@pytest.mark.parametrize("kind,minutes", [("cron", 0), ("cron", 60), ("cron", -5), ("launchd", 0), ("systemd", 1441), ("cron", True)])
def test_interval_bounds(kind, minutes):
    with pytest.raises(ServiceError, match="--every-minutes"):
        schedule_template(kind, "/data/store", "/usr/bin/python3", minutes, "ai.aidedge.velorona.tick")


def test_ordinary_paths_with_spaces_still_work_and_are_quoted():
    cron = schedule_template("cron", "/Users/me/Velorona data", "/usr/bin/python3", 5, "ai.aidedge.velorona.tick")
    assert '--store "/Users/me/Velorona data"' in cron and cron.splitlines()[1].startswith("*/5 * * * * ")
    sysd = schedule_template("systemd", "/home/me/Velorona data", "/usr/bin/python3", 15, "ai.aidedge.velorona.tick")
    assert 'ExecStart="/usr/bin/python3" -m aei_workflow tick --store "/home/me/Velorona data"' in sysd


# --- atomic writes -------------------------------------------------------------------------------------------------------------------

def test_atomic_write_does_not_follow_a_planted_symlink(tmp_path, monkeypatch):
    target = tmp_path / "victim.txt"
    target.write_text("original")
    doc_path = tmp_path / "store" / "workflows" / "wf.json"
    doc_path.parent.mkdir(parents=True)
    # An attacker who could predict the temporary name would plant a symlink there. Force the name to be predictable to prove that
    # even then the link is not followed (O_EXCL), and the victim is untouched.
    monkeypatch.setattr(store_mod.uuid, "uuid4", lambda: type("U", (), {"hex": "fixed"})())
    planted = f"{doc_path}.tmp-{os.getpid()}-fixed"
    os.symlink(target, planted)
    with pytest.raises(FileExistsError):
        store_mod._atomic_write_json(str(doc_path), {"a": 1})
    assert target.read_text() == "original"
    assert not doc_path.exists()


def test_atomic_write_normal_case_and_no_leftovers(tmp_path):
    doc_path = tmp_path / "runs" / "r1" / "run.json"
    store_mod._atomic_write_json(str(doc_path), {"run_id": "r1"})
    store_mod._atomic_write_json(str(doc_path), {"run_id": "r1", "v": 2})
    assert doc_path.read_text().count('"v": 2') == 1
    assert os.listdir(doc_path.parent) == ["run.json"]


def test_atomic_write_refuses_nan(tmp_path):
    with pytest.raises(ValueError):
        store_mod._atomic_write_json(str(tmp_path / "x.json"), {"v": float("nan")})
    assert os.listdir(tmp_path) == []  # the temporary file is cleaned up


# --- export ----------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("run_id", ["../../escape", "a/b", "..", "", None, "x" * 65])
def test_export_refuses_a_run_id_that_is_not_a_safe_folder_name(tmp_path, run_id):
    run = {"run_id": run_id, "status": "completed"}
    with pytest.raises(ValueError, match="not a valid id"):
        report.export_package(run, str(tmp_path / "out"))
    assert not (tmp_path / "out").exists() and not (tmp_path / "escape").exists()


# --- run lock (existing control; these cases had no test) --------------------------------------------------------------------------------

import json  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

from aei_workflow.locking import LockHeld, WorkflowLock, lock_path  # noqa: E402


def _plant_lock(root, info):
    path = lock_path(str(root), "wf")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(info if isinstance(info, str) else json.dumps(info))
    return path


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


@pytest.mark.skipif(os.name != "posix", reason="process liveness is only tested on POSIX")
def test_stale_lock_on_same_host_with_dead_process_is_recovered(tmp_path):
    stale = {"pid": _dead_pid(), "host": socket.gethostname(), "started_at": "2026-01-01T00:00:00Z", "token": "old"}
    _plant_lock(tmp_path, stale)
    lock = WorkflowLock(str(tmp_path), "wf").acquire()
    assert lock.held and lock.recovered_from["token"] == "old"
    lock.release()


def test_lock_from_another_host_is_never_stolen(tmp_path):
    path = _plant_lock(tmp_path, {"pid": 1, "host": "some-other-host.invalid", "token": "theirs"})
    with pytest.raises(LockHeld, match="another host"):
        WorkflowLock(str(tmp_path), "wf").acquire()
    assert json.load(open(path))["token"] == "theirs"


def test_unreadable_lock_is_treated_as_held(tmp_path):
    _plant_lock(tmp_path, "{not json")
    with pytest.raises(LockHeld, match="cannot be read"):
        WorkflowLock(str(tmp_path), "wf").acquire()


def test_release_never_removes_a_lock_taken_by_someone_else(tmp_path):
    lock = WorkflowLock(str(tmp_path), "wf").acquire()
    path = lock_path(str(tmp_path), "wf")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pid": 1, "host": "x", "token": "newer-owner"}, f)
    lock.release()
    assert os.path.exists(path)


@pytest.mark.parametrize("bad", ["../wf", "a/b", "", "x" * 65])
def test_lock_path_refuses_unsafe_workflow_ids(tmp_path, bad):
    with pytest.raises(ValueError):
        lock_path(str(tmp_path), bad)
