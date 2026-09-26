"""Local, customer-controlled persistence: plain JSON files in one folder.

    <root>/workflows/<workflow_id>.json
    <root>/runs/<run_id>/run.json
    <root>/_pruned/<run_id>/run.json      (runs moved out by retention; recoverable by hand)

Why files: a customer can see, back up, diff and delete them with ordinary tools; nothing leaves the
machine; QGIS and a future CLI/worker can share the same folder. Every write is write-temp, fsync,
rename, so a crash leaves either the old file or the new one, never a torn one.

Nothing here deletes user data. A file that cannot be read is reported and left exactly as it is.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile

from .schema import RUN_SCHEMA, RUN_SCHEMA_VERSION, STATUS_INTERRUPTED, STATUS_RUNNING, check_document, valid_id
from .workflow import utc_now, validate_workflow

BACKUP_FORMAT = "velorona.backup"
BACKUP_FORMAT_VERSION = 1
MAX_BACKUP_MEMBER_BYTES = 64 * 1024 * 1024


class StoreError(Exception):
    pass


def _atomic_write_json(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, sort_keys=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class RunStore:
    def __init__(self, root: str):
        self.root = root

    # -- workflows -------------------------------------------------------

    def _workflow_path(self, workflow_id: str) -> str:
        if not valid_id(workflow_id):
            raise StoreError(f"invalid workflow id: {workflow_id!r}")
        return os.path.join(self.root, "workflows", f"{workflow_id}.json")

    def save_workflow(self, workflow: dict) -> None:
        validate_workflow(workflow)
        _atomic_write_json(self._workflow_path(workflow["workflow_id"]), workflow)

    def load_workflow(self, workflow_id: str) -> dict:
        try:
            doc = _read_json(self._workflow_path(workflow_id))
        except FileNotFoundError:
            raise StoreError(f"no saved workflow {workflow_id}") from None
        return validate_workflow(doc)

    def list_workflows(self) -> tuple:
        """(workflows sorted by name, problems). Unreadable files are listed as problems, not dropped."""
        folder = os.path.join(self.root, "workflows")
        found, problems = [], []
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            if not name.endswith(".json"):
                continue
            try:
                found.append(validate_workflow(_read_json(os.path.join(folder, name))))
            except (OSError, ValueError) as exc:  # includes json.JSONDecodeError and SchemaError
                problems.append({"file": name, "problem": str(exc)})
        found.sort(key=lambda w: w["name"].lower())
        return found, problems

    # -- runs ------------------------------------------------------------

    def _run_path(self, run_id: str, pruned: bool = False) -> str:
        if not valid_id(run_id):
            raise StoreError(f"invalid run id: {run_id!r}")
        return os.path.join(self.root, "_pruned" if pruned else "runs", run_id, "run.json")

    def save_run(self, run: dict) -> None:
        check_document(run, RUN_SCHEMA, RUN_SCHEMA_VERSION)
        _atomic_write_json(self._run_path(run["run_id"]), run)

    def load_run(self, run_id: str) -> dict:
        try:
            doc = _read_json(self._run_path(run_id))
        except FileNotFoundError:
            raise StoreError(f"no saved run {run_id}") from None
        check_document(doc, RUN_SCHEMA, RUN_SCHEMA_VERSION)
        return doc

    def list_runs(self, workflow_id: str = None) -> tuple:
        """(summaries newest first, problems). A summary is the run without its per-link bodies'
        profiles -- enough for a history table; use load_run() to open one."""
        folder = os.path.join(self.root, "runs")
        summaries, problems = [], []
        for run_id in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            path = os.path.join(folder, run_id, "run.json")
            if not os.path.exists(path):
                continue
            try:
                doc = _read_json(path)
                check_document(doc, RUN_SCHEMA, RUN_SCHEMA_VERSION)
            except (OSError, ValueError) as exc:
                problems.append({"file": os.path.join(run_id, "run.json"), "problem": str(exc)})
                continue
            if workflow_id and doc.get("workflow_id") != workflow_id:
                continue
            summaries.append({k: doc.get(k) for k in ("run_id", "workflow_id", "workflow_name", "status",
                                                      "started_at", "finished_at", "counts", "error")})
        summaries.sort(key=lambda s: s["started_at"] or "", reverse=True)
        return summaries, problems

    def recover_interrupted(self, active_run_ids=()) -> list:
        """A run left in 'running' by a crash or a killed QGIS is relabelled 'interrupted'. Its
        finished links are kept; the ones that did not finish stay 'not_run'."""
        recovered = []
        summaries, _ = self.list_runs()
        for s in summaries:
            if s["status"] == STATUS_RUNNING and s["run_id"] not in active_run_ids:
                run = self.load_run(s["run_id"])
                run["status"] = STATUS_INTERRUPTED
                run["finished_at"] = run["finished_at"] or utc_now()
                run["error"] = run["error"] or "The run did not finish (QGIS closed or crashed while it was running)."
                self.save_run(run)
                recovered.append(s["run_id"])
        return recovered

    def apply_retention(self, workflow: dict) -> list:
        """Move (never delete) a workflow's oldest runs beyond output.retain_runs to _pruned/."""
        keep = workflow["output"]["retain_runs"]
        if keep is None:
            return []
        summaries, _ = self.list_runs(workflow["workflow_id"])
        moved = []
        for s in summaries[keep:]:
            if s["status"] == STATUS_RUNNING:
                continue
            src = os.path.dirname(self._run_path(s["run_id"]))
            dst = os.path.dirname(self._run_path(s["run_id"], pruned=True))
            if os.path.exists(dst):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            moved.append(s["run_id"])
        return moved

    # -- backup / restore ------------------------------------------------

    def export_backup(self, zip_path: str) -> dict:
        """One zip with every workflow and run plus a manifest of SHA-256 hashes."""
        if os.path.exists(zip_path):
            raise StoreError(f"{zip_path} already exists; choose a new file name")
        members = []
        for sub in ("workflows", "runs"):
            base = os.path.join(self.root, sub)
            for dirpath, _, files in os.walk(base):
                for fn in sorted(files):
                    if fn.endswith(".json"):
                        full = os.path.join(dirpath, fn)
                        members.append((os.path.relpath(full, self.root).replace(os.sep, "/"), full))
        manifest = {"format": BACKUP_FORMAT, "format_version": BACKUP_FORMAT_VERSION, "created_at": utc_now(),
                    "files": {}}
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for arc, full in sorted(members):
                with open(full, "rb") as f:
                    data = f.read()
                manifest["files"][arc] = hashlib.sha256(data).hexdigest()
                z.writestr(arc, data)
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
        return {"files": len(members), "path": zip_path}

    def import_backup(self, zip_path: str) -> dict:
        """Merge a backup into this store. Existing workflows/runs are never overwritten; anything
        that fails validation is reported and skipped. Returns {'added','skipped_existing','rejected'}."""
        report = {"added": [], "skipped_existing": [], "rejected": []}
        try:
            z = zipfile.ZipFile(zip_path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise StoreError(f"not a readable backup: {exc}") from exc
        with z:
            try:
                manifest = json.loads(z.read("manifest.json"))
            except (KeyError, ValueError) as exc:
                raise StoreError("not a Velorona backup (manifest.json missing or unreadable)") from exc
            if manifest.get("format") != BACKUP_FORMAT or manifest.get("format_version") != BACKUP_FORMAT_VERSION:
                raise StoreError("unsupported backup format or version; nothing was imported")
            for arc, expected in manifest.get("files", {}).items():
                try:
                    kind, doc = self._validate_member(z, arc, expected)
                except (ValueError, KeyError, StoreError) as exc:
                    report["rejected"].append({"file": arc, "problem": str(exc)})
                    continue
                if kind == "workflow":
                    target, doc_id = self._workflow_path(doc["workflow_id"]), doc["workflow_id"]
                else:
                    target, doc_id = self._run_path(doc["run_id"]), doc["run_id"]
                if os.path.exists(target) or (kind == "run" and os.path.exists(self._run_path(doc_id, pruned=True))):
                    report["skipped_existing"].append(f"{kind}:{doc_id}")
                    continue
                _atomic_write_json(target, doc)
                report["added"].append(f"{kind}:{doc_id}")
        return report

    @staticmethod
    def _validate_member(z: zipfile.ZipFile, arc: str, expected_sha: str):
        parts = arc.split("/")
        # Only the two layouts this store writes; anything else (absolute paths, '..', extra depth)
        # is refused before it can be turned into a path.
        if parts[0] == "workflows" and len(parts) == 2 and parts[1].endswith(".json"):
            kind = "workflow"
        elif parts[0] == "runs" and len(parts) == 3 and parts[2] == "run.json":
            kind = "run"
        else:
            raise StoreError("unexpected path in backup")
        info = z.getinfo(arc)
        if info.file_size > MAX_BACKUP_MEMBER_BYTES:
            raise StoreError("file too large")
        data = z.read(arc)
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise StoreError("checksum mismatch (file damaged or altered)")
        doc = json.loads(data)
        if kind == "workflow":
            validate_workflow(doc)
            if parts[1] != f"{doc['workflow_id']}.json":
                raise StoreError("file name does not match workflow_id")
        else:
            check_document(doc, RUN_SCHEMA, RUN_SCHEMA_VERSION)
            if parts[1] != doc.get("run_id") or not valid_id(doc.get("run_id")):
                raise StoreError("folder name does not match run_id")
        return kind, doc
