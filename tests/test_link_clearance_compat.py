"""The contract with the released aei-link-clearance: the declared range, the 0.2.0 earth-curvature convention, and ElevationDataError.

These run against whatever aei-link-clearance is installed (CI installs the released package from PyPI), never a source checkout."""

from importlib.metadata import requires, version
from types import SimpleNamespace

import pytest

pytest.importorskip("aei_link_clearance")
from packaging.requirements import Requirement  # noqa: E402
from aei_link_clearance import elevation, terrain  # noqa: E402

from automation_support import BOUNDS, HEADER  # noqa: E402
from aei_workflow import engine  # noqa: E402
from aei_workflow.inputs import validate_links_csv  # noqa: E402
from aei_workflow.runner import execute  # noqa: E402
from aei_workflow.workflow import new_workflow  # noqa: E402

PARAMS = {"k_factor": 4 / 3, "n_samples": 50}
# The worked example in aei-link-clearance's 0.2.0 changelog: flat ground, 30 m masts, 38.1 km, 6.35 GHz -> tightest clearance 8.6 m
# (0.1.x: 51.4 m, because the bulge was applied with the opposite sign).
LONG_FLAT = dict(link_id="LONG", site_a_lat=45.0, site_a_lon=-75.0, site_a_height_m=30.0,
                 site_b_lat=45.0 + 38.1 / 111.195, site_b_lon=-75.0, site_b_height_m=30.0, frequency_ghz=6.35)


def _declared():
    return next(Requirement(r) for r in requires("aei-workflow-runner") if Requirement(r).name == "aei-link-clearance")


def test_declared_range_is_the_corrected_0_2_line():
    spec = _declared().specifier
    assert "0.1.0" not in spec and "0.1.9" not in spec          # the wrong-sign releases are excluded
    assert "0.2.0" in spec and "0.2.7" in spec                  # every 0.2.x is allowed (no exact pin)
    assert "0.3.0" not in spec                                  # an unvetted minor line is not assumed compatible
    assert "elevation" in _declared().extras


def test_the_installed_library_satisfies_the_declared_range():
    assert version("aei-link-clearance") in _declared().specifier


def test_library_announces_the_required_convention():
    assert terrain.CLEARANCE_CONVENTION == engine.REQUIRED_CLEARANCE_CONVENTION


def test_long_path_clearance_uses_the_corrected_curvature_sign(monkeypatch):
    monkeypatch.setattr(terrain, "get_elevations", lambda pts, timeout=15.0: [100.0] * len(pts))
    r = engine.analyze_link_row(LONG_FLAT, PARAMS)
    assert r["terrain_clearance_m"] == pytest.approx(8.6, abs=0.1) and r["los_status"] == "marginal"   # 0.1.x gave 51.4 m, "clear"
    assert min(p["clearance_m"] for p in r["profile"][1:-1]) == pytest.approx(r["terrain_clearance_m"])   # tightest point is mid-path


def test_a_library_without_the_convention_is_refused(monkeypatch):
    monkeypatch.delattr(terrain, "CLEARANCE_CONVENTION")
    with pytest.raises(engine.LibraryOutOfDateError, match="pre-correction"):
        engine.analyze_link_row(LONG_FLAT, PARAMS)


def test_a_library_with_the_old_convention_is_refused(monkeypatch):
    monkeypatch.setattr(terrain, "CLEARANCE_CONVENTION", "bulge-subtracted-from-terrain")
    with pytest.raises(engine.LibraryOutOfDateError):
        engine.analyze_link_row(LONG_FLAT, PARAMS)


def test_an_out_of_date_library_fails_each_link_once_and_records_no_result(monkeypatch):
    monkeypatch.setattr(terrain, "get_elevations", lambda pts, timeout=15.0: [100.0] * len(pts))
    monkeypatch.delattr(terrain, "CLEARANCE_CONVENTION")
    csv = f"{HEADER}\nL1,43.65,-79.38,30,43.76,-79.41,30,6.0\n"
    run = execute(new_workflow("old-lib", execution={"retry_delay_s": 0}), validate_links_csv(csv, BOUNDS), engine.analyze_link_row,
                  sleep=lambda s: None)
    link = run["links"][0]
    assert run["status"] == "failed" and link["result"] is None
    assert link["error"]["type"] == "LibraryOutOfDateError" and link["error"]["attempts"] == 1    # deterministic: not retried


class _Resp:
    def __init__(self, elevations):
        self._e = elevations

    def raise_for_status(self):
        pass

    def json(self):
        return {"elevation": self._e}


@pytest.mark.parametrize("bad", [None, float("nan"), "n/a"])
def test_a_missing_elevation_value_is_a_failed_link_never_zero_metres(monkeypatch, bad):
    """0.2.0's own get_elevations() validation (only the HTTP call is replaced): ElevationDataError is a ValueError, so the runner records
    the link as failed on the first attempt and never as a result computed from a made-up 0 m."""
    import requests

    def get(url, params=None, timeout=None):
        n = len(params["latitude"].split(","))
        return _Resp([100.0] * (n - 1) + [bad])
    monkeypatch.setattr(requests, "get", get)
    csv = f"{HEADER}\nL1,43.65,-79.38,30,43.76,-79.41,30,6.0\n"
    run = execute(new_workflow("bad-elev", execution={"retry_delay_s": 0}), validate_links_csv(csv, BOUNDS), engine.analyze_link_row,
                  sleep=lambda s: None)
    link = run["links"][0]
    assert issubclass(elevation.ElevationDataError, ValueError)
    assert run["status"] == "failed" and link["result"] is None
    assert link["error"]["type"] == "ElevationDataError" and link["error"]["attempts"] == 1
    assert "never treated as 0 m" in link["error"]["message"]
