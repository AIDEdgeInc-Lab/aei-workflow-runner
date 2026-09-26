"""Shared helpers for the automation tests: no QGIS, no network."""

from aei_workflow.runner import execute  # noqa: E402
from aei_workflow.workflow import new_workflow  # noqa: E402
HEADER = "link_id,site_a_lat,site_a_lon,site_a_height_m,site_b_lat,site_b_lon,site_b_height_m,frequency_ghz"
from aei_workflow.bounds import bounds as _bounds  # noqa: E402

BOUNDS = _bounds()
VERSIONS = {"velorona_plugin": "test", "analysis_engine": {"aei-link-clearance": "0.1.0", "aei-geo-features": "0.1.4"},
            "python": "3", "qgis": None, "run_schema": 1, "workflow_schema": 1}


def row(link_id, n=0, **over):
    r = {"link_id": link_id, "source_row": n + 2, "site_a_lat": 43.65 + n * 0.01, "site_a_lon": -79.38,
         "site_a_height_m": 30.0, "site_b_lat": 43.76 + n * 0.01, "site_b_lon": -79.41, "site_b_height_m": 30.0,
         "frequency_ghz": 6.0}
    r.update(over)
    return r


def fake_result(ratio=1.5, status="clear", near=False, distance=12.3):
    return {"distance_km": distance, "bearing_deg": 10.0, "frequency_ghz": 6.0, "k_factor": 4 / 3,
            "first_fresnel_radius_m": 9.0, "required_clearance_m": 5.4, "terrain_clearance_m": 5.4 * ratio,
            "clearance_ratio": ratio, "los_status": status, "obstruction_distance_km": None if status == "clear" else 6.0,
            "percent_fresnel_clear": 0.6 * ratio, "near_threshold": near, "explanation": f"test {status}",
            "profile": [{"distance_from_a_km": 0.0, "latitude": 43.65, "longitude": -79.38, "ground_elevation_m": 80.0,
                         "clearance_m": 30.0, "percent_fresnel_clear": None},
                        {"distance_from_a_km": distance, "latitude": 43.76, "longitude": -79.41, "ground_elevation_m": 90.0,
                         "clearance_m": 30.0, "percent_fresnel_clear": None}]}


def fake_analyzer(row_, params):
    return fake_result()


def validation(rows, rejected=()):
    return {"columns": HEADER.split(","), "accepted": list(rows), "rejected": list(rejected)}


def make_run(rows=None, analyzer=fake_analyzer, workflow=None, rejected=(), **kw):
    workflow = workflow or new_workflow("Test workflow", workflow_id="wf1", execution={"retry_delay_s": 0})
    rows = rows if rows is not None else [row("L1", 0), row("L2", 1), row("L3", 2)]
    return execute(workflow, validation(rows, rejected), analyzer, source={"name": "links.csv", "sha256": "ab" * 32},
                   versions=kw.pop("versions", VERSIONS), sleep=lambda s: None, **kw)
