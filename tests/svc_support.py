"""Helpers for the service/CLI tests: customer-style input files, workflows, stores, a scripted analyzer. No network."""

import json
import os
import subprocess
import sys

from aei_workflow.service import open_store
from aei_workflow.workflow import new_workflow

HEADER = "link_id,site_a_lat,site_a_lon,site_a_height_m,site_b_lat,site_b_lon,site_b_height_m,frequency_ghz"
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
SECRET_IDS = ["SECRET-LINK-ALPHA", "SECRET-LINK-BRAVO", "SECRET-LINK-CHARLIE"]


def write_links(path, ids=SECRET_IDS, extra_lines=()):
    lines = [HEADER] + [f"{lid},43.{60 + i},-79.38,30,43.{70 + i},-79.41,30,6.0" for i, lid in enumerate(ids)] + list(extra_lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return str(path)




def make_workflow(links, **kw):
    kw.setdefault("workflow_id", "wf-night")
    kw.setdefault("execution", {"retry_delay_s": 0, "max_attempts": 1})
    return new_workflow(kw.pop("name", "Nightly check"), input_path=str(links), **kw)


def store_at(tmp_path):
    return open_store(str(tmp_path / "customer-store"))


def cli(args, env_extra=None, wrapper=True, timeout=120, **popen):
    """Run velorona-run in a real subprocess. The wrapper replaces only the terrain HTTP call and blocks every socket connect."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([SRC, HERE, os.environ.get("PYTHONPATH", "")]), **(env_extra or {})}
    cmd = [sys.executable, os.path.join(HERE, "stub_main.py") if wrapper else "-m", *([] if wrapper else ["aei_workflow"]), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout, **popen)


def popen(args, env_extra=None):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([SRC, HERE, os.environ.get("PYTHONPATH", "")]), **(env_extra or {})}
    return subprocess.Popen([sys.executable, os.path.join(HERE, "stub_main.py"), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
