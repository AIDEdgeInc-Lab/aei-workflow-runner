"""Test wrapper around the real CLI: replaces ONLY the terrain HTTP call, and makes any network connection an error.

STUB_MODE: flat (default) | down | fail_from:N (calls N and later raise) ; STUB_DELAY: seconds per call ; STUB_COUNTER: file used to
count calls across processes. Because socket connections raise, a passing run proves the runner opens no other network path.
"""
import os
import socket
import sys
import time

from aei_link_clearance import terrain

mode, delay, counter = os.environ.get("STUB_MODE", "flat"), float(os.environ.get("STUB_DELAY", "0")), os.environ.get("STUB_COUNTER")


def _no_network(*a, **k):
    raise AssertionError("network connection attempted during a test run")


socket.socket.connect = _no_network
socket.create_connection = _no_network


def get_elevations(points, timeout=15.0):
    n = 1
    if counter:
        n = (int(open(counter).read()) if os.path.exists(counter) else 0) + 1
        open(counter, "w").write(str(n))
    time.sleep(delay)
    if mode == "down" or (mode.startswith("fail_from:") and n >= int(mode.split(":")[1])):
        raise ConnectionError("simulated elevation outage")
    return [100.0] * len(points)


terrain.get_elevations = get_elevations
from aei_workflow.cli import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
