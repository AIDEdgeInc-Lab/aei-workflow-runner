"""Persistence, recovery, retention, backup/restore, schema gating."""

import json
import os
import zipfile

import pytest

from automation_support import make_run, row
from aei_workflow import store as store_mod
from aei_workflow.schema import SchemaError
from aei_workflow.store import RunStore, StoreError
from aei_workflow.workflow import new_workflow


@pytest.fixture
def st(tmp_path):
    return RunStore(str(tmp_path / "velorona"))


def test_workflow_round_trip_and_listing(st):
    wf = new_workflow("Beta", workflow_id="b1")
    st.save_workflow(wf)
    st.save_workflow(new_workflow("alpha", workflow_id="a1"))
    assert st.load_workflow("b1") == wf
    listed, problems = st.list_workflows()
    assert [w["name"] for w in listed] == ["alpha", "Beta"] and problems == []


def test_run_round_trip_is_exact_and_reopenable(st):
    run = make_run()
    st.save_run(run)
    assert st.load_run(run["run_id"]) == run
    # A second store object on the same folder (a new QGIS session) sees it.
    assert RunStore(st.root).load_run(run["run_id"])["links"][2]["link_id"] == "L3"


def test_history_is_newest_first_and_filterable_by_workflow(st):
    a, b, c = make_run(), make_run(), make_run(workflow=new_workflow("other", workflow_id="wf2"))
    a["started_at"], b["started_at"], c["started_at"] = "2026-01-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"
    for r in (a, b, c):
        st.save_run(r)
    ids = [s["run_id"] for s in st.list_runs()[0]]
    assert ids == [b["run_id"], c["run_id"], a["run_id"]]
    assert [s["run_id"] for s in st.list_runs("wf2")[0]] == [c["run_id"]]


def test_corrupt_file_is_reported_and_left_untouched(st):
    good = make_run()
    st.save_run(good)
    bad_dir = os.path.join(st.root, "runs", "broken")
    os.makedirs(bad_dir)
    bad = os.path.join(bad_dir, "run.json")
    with open(bad, "w") as f:
        f.write('{"schema": "velorona.run", "sche')
    summaries, problems = st.list_runs()
    assert [s["run_id"] for s in summaries] == [good["run_id"]]
    assert problems[0]["file"].endswith("run.json") and os.path.exists(bad)
    assert open(bad).read().startswith('{"schema"')


def test_a_run_from_a_newer_schema_is_refused_and_not_modified(st):
    run = make_run()
    st.save_run(run)
    path = os.path.join(st.root, "runs", run["run_id"], "run.json")
    doc = json.load(open(path))
    doc["schema_version"] = 2
    with open(path, "w") as f:
        json.dump(doc, f)
    before = open(path, "rb").read()
    with pytest.raises(SchemaError, match="newer Velorona"):
        st.load_run(run["run_id"])
    assert st.list_runs()[1][0]["problem"].startswith("velorona.run version 2")
    assert st.recover_interrupted() == [] and open(path, "rb").read() == before


def test_ids_cannot_escape_the_store(st):
    for bad in ("../x", "a/b", "", ".", "a.b", "x" * 65):
        with pytest.raises(StoreError):
            st.load_run(bad)
        with pytest.raises(StoreError):
            st.load_workflow(bad)


def test_a_failed_write_leaves_the_previous_file_intact_and_no_temp_file(st, monkeypatch):
    run = make_run()
    st.save_run(run)
    run2 = dict(run, status="failed")

    def broken_dump(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(store_mod.json, "dump", broken_dump)
    with pytest.raises(OSError):
        st.save_run(run2)
    monkeypatch.undo()
    assert st.load_run(run["run_id"])["status"] == "completed"
    folder = os.path.join(st.root, "runs", run["run_id"])
    assert os.listdir(folder) == ["run.json"]


def test_crashed_running_run_is_recovered_as_interrupted_keeping_finished_links(st):
    snaps = []
    make_run(on_checkpoint=lambda r: snaps.append(json.loads(json.dumps(r))))
    mid = snaps[2]                                   # after two links, still 'running'
    assert mid["status"] == "running"
    st.save_run(mid)
    assert st.recover_interrupted(active_run_ids={"someone-else"}) == [mid["run_id"]]
    r = st.load_run(mid["run_id"])
    assert r["status"] == "interrupted" and [l["status"] for l in r["links"]] == ["ok", "ok", "not_run"]
    assert "did not finish" in r["error"]


def test_an_active_run_is_not_marked_interrupted(st):
    snaps = []
    make_run(on_checkpoint=lambda r: snaps.append(json.loads(json.dumps(r))))
    st.save_run(snaps[1])
    assert st.recover_interrupted(active_run_ids={snaps[1]["run_id"]}) == []
    assert st.load_run(snaps[1]["run_id"])["status"] == "running"


def test_retention_moves_old_runs_aside_and_never_deletes(st):
    wf = new_workflow("keep two", workflow_id="k2", retain_runs=2)
    runs = []
    for month in ("01", "02", "03", "04"):
        r = make_run(workflow=wf)
        r["started_at"] = f"2026-{month}-01T00:00:00+00:00"
        st.save_run(r)
        runs.append(r)
    moved = st.apply_retention(wf)
    assert sorted(moved) == sorted([runs[0]["run_id"], runs[1]["run_id"]])
    assert {s["run_id"] for s in st.list_runs()[0]} == {runs[2]["run_id"], runs[3]["run_id"]}
    assert os.path.exists(os.path.join(st.root, "_pruned", runs[0]["run_id"], "run.json"))


def test_retention_default_keeps_everything(st):
    wf = new_workflow("all", workflow_id="all")
    for _ in range(3):
        st.save_run(make_run(workflow=wf))
    assert st.apply_retention(wf) == [] and len(st.list_runs()[0]) == 3


# -- backup / restore --------------------------------------------------------------------------------

def _populate(st):
    wf = new_workflow("W", workflow_id="w1")
    st.save_workflow(wf)
    runs = [make_run(workflow=wf), make_run(workflow=wf, rows=[row("Z")])]
    for r in runs:
        st.save_run(r)
    return wf, runs


def test_backup_restores_into_an_empty_store_exactly(st, tmp_path):
    wf, runs = _populate(st)
    z = str(tmp_path / "backup.zip")
    assert st.export_backup(z)["files"] == 3
    fresh = RunStore(str(tmp_path / "other"))
    rep = fresh.import_backup(z)
    assert len(rep["added"]) == 3 and not rep["rejected"] and not rep["skipped_existing"]
    assert fresh.load_workflow("w1") == wf
    assert [fresh.load_run(r["run_id"]) for r in runs] == runs


def test_restore_never_overwrites_existing_records(st, tmp_path):
    wf, runs = _populate(st)
    z = str(tmp_path / "b.zip")
    st.export_backup(z)
    local = st.load_run(runs[0]["run_id"])
    local["error"] = "edited locally"
    st.save_run(local)
    rep = st.import_backup(z)
    assert len(rep["skipped_existing"]) == 3 and rep["added"] == []
    assert st.load_run(runs[0]["run_id"])["error"] == "edited locally"


def test_backup_refuses_to_overwrite_an_existing_file(st, tmp_path):
    _populate(st)
    z = tmp_path / "b.zip"
    z.write_text("precious")
    with pytest.raises(StoreError):
        st.export_backup(str(z))
    assert z.read_text() == "precious"


def test_tampered_backup_member_is_rejected_others_still_import(st, tmp_path):
    wf, runs = _populate(st)
    z = str(tmp_path / "b.zip")
    st.export_backup(z)
    tampered = str(tmp_path / "t.zip")
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(tampered, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == f"runs/{runs[0]['run_id']}/run.json":
                data = data.replace(b'"completed"', b'"failed"')
            dst.writestr(name, data)
    fresh = RunStore(str(tmp_path / "f"))
    rep = fresh.import_backup(tampered)
    assert len(rep["rejected"]) == 1 and "checksum" in rep["rejected"][0]["problem"] and len(rep["added"]) == 2


def test_backup_with_path_traversal_member_is_rejected_and_writes_nothing_outside(st, tmp_path):
    z = str(tmp_path / "evil.zip")
    import hashlib
    payload = b"{}"
    names = ["runs/../../evil.json", "/abs/run.json", "runs/x/y/run.json", "workflows/../w.json"]
    manifest = {"format": "velorona.backup", "format_version": 1, "files": {n: hashlib.sha256(payload).hexdigest() for n in names}}
    with zipfile.ZipFile(z, "w") as w:
        w.writestr("manifest.json", json.dumps(manifest))
        for n in names:
            w.writestr(n, payload)
    rep = RunStore(str(tmp_path / "s")).import_backup(z)
    assert rep["added"] == [] and len(rep["rejected"]) == 4
    assert not os.path.exists(str(tmp_path / "evil.json"))


def test_unknown_backup_version_or_junk_is_refused_before_anything_is_written(st, tmp_path):
    z = str(tmp_path / "v2.zip")
    with zipfile.ZipFile(z, "w") as w:
        w.writestr("manifest.json", json.dumps({"format": "velorona.backup", "format_version": 2, "files": {}}))
    with pytest.raises(StoreError, match="unsupported"):
        st.import_backup(z)
    junk = tmp_path / "junk.zip"
    junk.write_text("not a zip")
    with pytest.raises(StoreError):
        st.import_backup(str(junk))
    assert not os.path.exists(os.path.join(st.root, "runs"))


def test_backup_member_whose_name_disagrees_with_its_id_is_rejected(st, tmp_path):
    import hashlib
    run = make_run()
    data = json.dumps(run).encode()
    z = str(tmp_path / "m.zip")
    name = "runs/someotherid/run.json"
    with zipfile.ZipFile(z, "w") as w:
        w.writestr("manifest.json", json.dumps({"format": "velorona.backup", "format_version": 1,
                                                "files": {name: hashlib.sha256(data).hexdigest()}}))
        w.writestr(name, data)
    rep = st.import_backup(z)
    assert rep["added"] == [] and "does not match" in rep["rejected"][0]["problem"]
