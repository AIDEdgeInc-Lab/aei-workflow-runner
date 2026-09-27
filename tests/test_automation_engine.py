"""The real aei_link_clearance engine through the workflow, with only the elevation HTTP call replaced."""

import pytest

pytest.importorskip("aei_link_clearance")
from aei_link_clearance import terrain  # noqa: E402

from automation_support import BOUNDS, HEADER, VERSIONS  # noqa: E402
from aei_workflow import engine  # noqa: E402
from aei_workflow.inputs import validate_links_csv  # noqa: E402
from aei_workflow.runner import execute  # noqa: E402
from aei_workflow.workflow import new_workflow  # noqa: E402


def flat_terrain(height):
    return lambda points, timeout=15.0: [height] * len(points)


CSV = f"""{HEADER}
L1,43.65,-79.38,30,43.76,-79.41,30,6.0
L2,43.70,-79.30,25,43.90,-79.10,25,11.0
"""


def test_real_engine_end_to_end_with_stubbed_elevation(monkeypatch):
    monkeypatch.setattr(terrain, "get_elevations", flat_terrain(100.0))
    v = validate_links_csv(CSV, BOUNDS)
    run = execute(new_workflow("real", workflow_id="real1"), v, engine.analyze_link_row,
                  source={"name": "x.csv", "sha256": "0" * 64}, versions=engine.collect_versions("test"))
    assert run["status"] == "completed"
    r = run["links"][0]["result"]
    assert r["los_status"] == "clear" and r["distance_km"] > 10 and len(r["profile"]) == 50
    assert r["k_factor"] == pytest.approx(4 / 3) and "explanation" in r and run["links"][0]["result"]["profile"][0]["ground_elevation_m"] == 100.0
    assert run["versions"]["analysis_engine"]["aei-link-clearance"] == "0.1.0"


def test_real_engine_result_matches_calling_the_library_directly(monkeypatch):
    monkeypatch.setattr(terrain, "get_elevations", flat_terrain(100.0))
    row = validate_links_csv(CSV, BOUNDS)["accepted"][0]
    direct = terrain.analyze_link(row["link_id"], row["site_a_lat"], row["site_a_lon"], row["site_a_height_m"],
                                  row["site_b_lat"], row["site_b_lon"], row["site_b_height_m"], row["frequency_ghz"])
    via = engine.analyze_link_row(row, {"k_factor": 4 / 3, "n_samples": 50})
    for f in ("distance_km", "clearance_ratio", "terrain_clearance_m", "required_clearance_m", "los_status", "near_threshold"):
        assert via[f] == getattr(direct, f)


def test_elevation_service_failure_is_a_failed_link_not_a_result(monkeypatch):
    def down(points, timeout=15.0):
        raise ConnectionError("Open-Meteo unreachable")
    monkeypatch.setattr(terrain, "get_elevations", down)
    run = execute(new_workflow("real", execution={"retry_delay_s": 0}), validate_links_csv(CSV, BOUNDS),
                  engine.analyze_link_row, sleep=lambda s: None)
    assert run["status"] == "failed"
    assert all(l["result"] is None and "unreachable" in l["error"]["message"] for l in run["links"])


def test_a_mountain_between_the_sites_is_obstructed_and_says_where(monkeypatch):
    def ridge(points, timeout=15.0):
        n = len(points)
        return [100.0 if i not in (n // 2 - 1, n // 2, n // 2 + 1) else 400.0 for i in range(n)]
    monkeypatch.setattr(terrain, "get_elevations", ridge)
    run = execute(new_workflow("real"), validate_links_csv(CSV, BOUNDS), engine.analyze_link_row)
    r = run["links"][0]["result"]
    assert r["los_status"] == "obstructed" and r["obstruction_distance_km"] is not None
