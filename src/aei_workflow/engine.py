"""The default analyzer: aei_link_clearance.analyze_link, unmodified, plus version discovery.

An analyzer is any callable  analyzer(row, params) -> dict  returning the fields below. The runner
depends only on that contract, so tests (and a future Map worker) can supply their own.
"""

from __future__ import annotations

from dataclasses import asdict

from .schema import json_safe

# What the terrain engine's elevation data actually is -- taken from the library's own statements
# (elevation.py / terrain.py), not characterised by this product.
TERRAIN_SOURCE_NOTE = (
    "Open-Meteo Elevation API (Copernicus DEM GLO-90, 90 m). A digital SURFACE model: it includes "
    "vegetation and buildings, not bare earth."
)


# aei-link-clearance 0.2.0 corrected the earth-curvature sign and announces the convention it implements. 0.1.x lacks the constant and
# overstates clearance (by twice the earth bulge), so results from it must not be recorded as if they were correct.
REQUIRED_CLEARANCE_CONVENTION = "bulge-added-to-terrain"


class LibraryOutOfDateError(ValueError):
    """The installed aei-link-clearance predates the earth-curvature correction. A ValueError, so the runner records the link as failed
    with this message and does not retry it (reinstalling the library is the only fix)."""


def require_corrected_clearance() -> None:
    from aei_link_clearance import terrain
    found = getattr(terrain, "CLEARANCE_CONVENTION", None)
    if found != REQUIRED_CLEARANCE_CONVENTION:
        raise LibraryOutOfDateError(
            f"The installed aei-link-clearance uses the pre-correction earth-curvature sign (CLEARANCE_CONVENTION={found!r}, required "
            f"{REQUIRED_CLEARANCE_CONVENTION!r}); its clearance would be overstated. Install aei-link-clearance>=0.2.0,<0.3.")


def analyze_link_row(row: dict, params: dict) -> dict:
    from aei_link_clearance import analyze_link, explain
    require_corrected_clearance()

    result = analyze_link(
        link_id=row["link_id"],
        site_a_lat=row["site_a_lat"], site_a_lon=row["site_a_lon"], site_a_height_m=row["site_a_height_m"],
        site_b_lat=row["site_b_lat"], site_b_lon=row["site_b_lon"], site_b_height_m=row["site_b_height_m"],
        frequency_ghz=row["frequency_ghz"],
        k_factor=params["k_factor"], n_samples=params["n_samples"],
    )
    out = asdict(result)
    out.pop("link_id", None)
    out["explanation"] = explain(result)
    out["profile"] = [
        {k: p[k] for k in ("distance_from_a_km", "latitude", "longitude", "ground_elevation_m", "clearance_m",
                           "percent_fresnel_clear")}
        for p in out["profile"]
    ]
    return json_safe(out)


def _pkg_version(dist_name: str):
    """Installed distribution version; falls back to the module's own __version__ (a vendored or
    zipped copy has no dist-info). None only when neither is available -- never a guess."""
    from importlib import import_module
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(dist_name)
    except PackageNotFoundError:
        pass
    try:
        return getattr(import_module(dist_name.replace("-", "_")), "__version__", None)
    except ImportError:
        return None


def collect_versions(plugin_version: str = None, qgis_version: str = None, implementation: str = None) -> dict:
    """implementation: None for the QGIS plugin (identified by velorona_plugin); a short name such as "velorona-run" for the CLI."""
    import platform
    from . import __version__
    from .schema import RUN_SCHEMA_VERSION, WORKFLOW_SCHEMA_VERSION
    versions = {
        "velorona_plugin": plugin_version,
        "workflow_runner": __version__,
        "analysis_engine": {
            "aei-link-clearance": _pkg_version("aei-link-clearance"),
            "aei-geo-features": _pkg_version("aei-geo-features"),
        },
        "python": platform.python_version(),
        "qgis": qgis_version,
        "run_schema": RUN_SCHEMA_VERSION,
        "workflow_schema": WORKFLOW_SCHEMA_VERSION,
    }
    if implementation:
        versions["implementation"] = implementation
    return versions


def collect_provenance() -> dict:
    """What each data source is, per run. The terrain source is a static model: it has no
    observation time, so none is recorded -- retrieval time is per link (links[].analyzed_at)."""
    return {
        "data_sources": [{
            "role": "terrain elevation",
            "name": TERRAIN_SOURCE_NOTE,
            "temporal_kind": "static model (not an observation)",
            "observation_time": None,
            "retrieved": "per link, at analysis time (links[].analyzed_at)",
        }],
        "historical_replay_supported": False,
    }


ASSUMPTIONS_AND_LIMITATIONS = [
    "Geometry-only path clearance: earth-curvature-adjusted terrain versus the first Fresnel zone. "
    "It does not model weather, ducting, rain, or any live network condition.",
    "The 60% first-Fresnel 'clear' threshold is standard microwave-link engineering practice, not an "
    "ITU-R compliance figure. The 30% marginal/obstructed split is this project's own judgment.",
    "Terrain is a 90 m surface model. The 15 m elevation-uncertainty used for the near-threshold flag "
    "is a deliberately conservative estimate, not a statistical confidence interval.",
    "A stored result is a record of what was calculated at the time; it is not a reconstruction of "
    "past conditions. Running the same workflow later re-queries the elevation service and is a new "
    "analysis.",
    "Read-only decision support. Nothing here changes network equipment.",
]
