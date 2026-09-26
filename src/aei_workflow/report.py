"""Traceable result package for one run.

    velorona-run-<run_id>/
        report.md          human-readable: what ran, on what, under which assumptions, what came back
        results.csv        one row per input row (analysed, failed, not run, or rejected)
        input_links.csv    the accepted rows in the canonical input schema -- re-importable (tested)
        links.geojson      one LineString per analysed link (the sampled path), same property names as the library's writer
        run.json           the complete record (velorona.run/1); restorable through backup import (tested)
        manifest.json      SHA-256 of every other file

results.csv, links.geojson and report.md are outputs, not inputs: they are not claimed to re-import.
links.geojson is the GIS-facing file (tested: loads in QGIS as a line layer with every result as an
attribute). results.csv follows the Evidence-CSV convention of '#' preamble lines, so it is for
spreadsheets and people; OGR reads those lines as the header, so do not load it into QGIS as a table.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os

from .csvsafe import rows_to_csv
from .schema import LINK_FAILED, LINK_NOT_RUN, LINK_OK
from .workflow import utc_now

RESULT_HEADER = ["link_id", "source_row", "status", "distance_km", "first_fresnel_radius_m", "required_clearance_m",
                 "terrain_clearance_m", "clearance_ratio", "los_status", "near_threshold", "obstruction_distance_km",
                 "warnings", "error", "analyzed_at", "explanation"]


def _engine_text(engine: dict, sep: str = ", ") -> str:
    return sep.join(f"{name} {version or 'unknown'}" for name, version in engine.items())


def _implementation_text(versions: dict) -> str:
    """Runs written by Velorona Map name their implementation; a plugin run does not, and is the default."""
    if versions.get("implementation"):
        build = versions.get("velorona_map_build")
        return f"{versions['implementation']}" + (f", build {build}" if build else "")
    return f"Velorona plugin {versions.get('velorona_plugin') or 'unknown'}"


def _f(value, places):
    return "" if value is None else f"{value:.{places}f}"


def _preamble(run: dict) -> list:
    v = run.get("versions", {})
    engine = v.get("analysis_engine", {})
    return [
        "Velorona -- batch Terrestrial Path Clearance results",
        f"Run: {run['run_id']}  Workflow: {run['workflow_name']} ({run['workflow_id']})  Status: {run['status']}",
        f"Started: {run['started_at']}  Finished: {run.get('finished_at')}",
        f"Input file: {run['input'].get('source_name')}  sha256: {run['input'].get('sha256')}",
        f"Engine: {_engine_text(engine)}; {_implementation_text(v)}",
        "Parameters: " + ", ".join(f"{k}={v}" for k, v in run["workflow_snapshot"]["params"].items()),
        "Terrain: " + "; ".join(s["name"] for s in run["provenance"]["data_sources"]),
        "Not a reconstruction of past conditions; see report.md for assumptions and limitations.",
    ]


def results_csv(run: dict) -> str:
    rows = []
    for l in run["links"]:
        r = l.get("result") or {}
        err = l.get("error")
        rows.append([
            l["link_id"], l.get("source_row"), l["status"], _f(r.get("distance_km"), 3),
            _f(r.get("first_fresnel_radius_m"), 2), _f(r.get("required_clearance_m"), 2),
            _f(r.get("terrain_clearance_m"), 2), _f(r.get("clearance_ratio"), 3),
            r.get("los_status", ""), r.get("near_threshold", ""), _f(r.get("obstruction_distance_km"), 3),
            " | ".join(l.get("warnings", [])),
            f"{err['type']}: {err['message']} (attempts: {err['attempts']})" if err else "",
            l.get("analyzed_at") or "", r.get("explanation", ""),
        ])
    for rej in run["rejected_rows"]:
        rows.append([rej["link_id"], rej["source_row"], "rejected"] + [""] * 8 + [""] + [rej["reason"], "", ""])
    return rows_to_csv(_preamble(run), RESULT_HEADER, rows)


def input_links_csv(run: dict) -> str:
    cols = run["input"].get("columns") or []
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for l in run["links"]:
        row = {"link_id": l["link_id"], **l["input"]}
        w.writerow([_csv_input_cell(row[c]) for c in cols])
    return buf.getvalue()


def _csv_input_cell(v):
    # Input files are for re-import, so values are written verbatim (repr round-trips floats exactly).
    return repr(v) if isinstance(v, float) else v


def geojson(run: dict) -> dict:
    features = []
    for l in run["links"]:
        if l["status"] != LINK_OK or not l["result"]["profile"]:
            continue
        r = l["result"]
        # GeoJSON order is [longitude, latitude]. Same geometry and property names as the library's
        # batch.results_to_geojson(), but built from the stored record: a reopened run has no live
        # LinkClearanceResult objects to hand to it.
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[p["longitude"], p["latitude"]] for p in r["profile"]]},
            "properties": {
                "run_id": run["run_id"], "link_id": l["link_id"], "analyzed_at": l["analyzed_at"],
                **{k: r.get(k) for k in ("distance_km", "bearing_deg", "frequency_ghz", "k_factor",
                                         "first_fresnel_radius_m", "required_clearance_m", "terrain_clearance_m",
                                         "clearance_ratio", "percent_fresnel_clear", "los_status", "near_threshold",
                                         "obstruction_distance_km", "explanation")},
                "warnings": " | ".join(l.get("warnings", [])),
            },
        })
    return {"type": "FeatureCollection", "features": features,
            "velorona_run": {"run_id": run["run_id"], "workflow_id": run["workflow_id"],
                             "started_at": run["started_at"], "status": run["status"]}}


def _md(text) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def report_md(run: dict) -> str:
    c, v, w = run["counts"], run.get("versions", {}), run["workflow_snapshot"]
    eng = v.get("analysis_engine", {})
    L = [f"# Velorona run report -- {_md(run['workflow_name'])}", "",
         f"**Run** `{run['run_id']}` · **Status: {run['status'].upper()}** · started {run['started_at']}, "
         f"finished {run.get('finished_at') or 'n/a'}", ""]
    sched = run.get("schedule") or {}
    if sched.get("trigger") == "scheduled":
        L += [f"Scheduled run (workflow version {run.get('workflow_version', 1)}): due {sched.get('scheduled_for')}, schedule time zone {sched.get('timezone')}.", ""]
    elif run.get("workflow_version") is not None:
        L += [f"Manual run (workflow version {run.get('workflow_version')}).", ""]
    if run["status"] == "missed":
        L += [f"> This scheduled run did **not** happen: {run.get('error') or 'reason not recorded'}. No links were analysed and no results exist for it.", ""]
    elif run["status"] != "completed":
        L += [f"> This run is **{run['status']}**: {c['ok']} of {c['input_rows']} input rows were analysed "
              f"({c['failed']} failed, {c['rejected']} rejected, {c['not_run']} not run)."
              + (f" {run['error']}" if run.get("error") else ""), ""]
    L += ["## Inputs", "",
          f"- File: `{_md(run['input'].get('source_name'))}` (sha256 `{run['input'].get('sha256')}`)",
          f"- Rows: {c['input_rows']} · analysed {c['ok']} · failed {c['failed']} · rejected {c['rejected']} · not run {c['not_run']}",
          "", "## Configuration", ""]
    units = w.get("param_units", {})
    for k, val in w["params"].items():
        L.append(f"- {k} = {val} ({units.get(k, '')})")
    ex = w["execution"]
    L += [f"- attempts per link: {ex['max_attempts']}, retry delay {ex['retry_delay_s']} s, pause between links {ex['pause_between_links_s']} s",
          "", "## Data-source provenance", ""]
    for s in run["provenance"]["data_sources"]:
        L.append(f"- **{s['role']}**: {s['name']} -- {s['temporal_kind']}; observation time: "
                 f"{s['observation_time'] or 'none provided'}; retrieved {s['retrieved']}.")
    L.append("- Historical replay: **not supported** by this data source.")
    L += ["", "## Assumptions and limitations", ""] + [f"- {t}" for t in run["assumptions_and_limitations"]]
    L += ["", "## Versions", "",
          f"- {_implementation_text(v)} · QGIS {v.get('qgis') or 'n/a'} · Python {v.get('python') or 'n/a'}",
          f"- engine: {_engine_text(eng, ' · ')}",
          f"- run schema {v.get('run_schema')} · workflow schema {v.get('workflow_schema')}", "",
          "## Results", "", "| Link | Status | LOS | Ratio | Clearance (m) | Required (m) | Distance (km) | Notes |",
          "|---|---|---|---|---|---|---|---|"]
    for l in run["links"]:
        r = l.get("result") or {}
        notes = " ".join(l.get("warnings", []))
        if l["status"] == LINK_FAILED:
            notes = f"{l['error']['type']}: {l['error']['message']}"
        elif l["status"] == LINK_NOT_RUN:
            notes = "not run (run stopped before this link)"
        los = r.get("los_status", "")
        if r.get("near_threshold"):
            los += " (near threshold)"
        L.append(f"| {_md(l['link_id'])} | {l['status']} | {los} | {_f(r.get('clearance_ratio'), 3)} | "
                 f"{_f(r.get('terrain_clearance_m'), 2)} | {_f(r.get('required_clearance_m'), 2)} | "
                 f"{_f(r.get('distance_km'), 3)} | {_md(notes)} |")
    if run["rejected_rows"]:
        L += ["", "## Rejected input rows (not analysed)", ""] + [f"- {_md(r['reason'])}" for r in run["rejected_rows"]]
    L += ["", f"_Generated {utc_now()} from run.json. Read-only analysis; no network equipment was changed._", ""]
    return "\n".join(L)


def package_files(run: dict) -> dict:
    """{file name: text} for the result package. Deterministic for a given run apart from the two generation timestamps."""
    return {
        "run.json": json.dumps(run, indent=2, allow_nan=False),
        "input_links.csv": input_links_csv(run),
        "results.csv": results_csv(run),
        "links.geojson": json.dumps(geojson(run), indent=2, allow_nan=False),
        "report.md": report_md(run),
    }


def write_package(run: dict, folder: str) -> str:
    """Write the package INTO `folder` (created if needed). Files are created exclusively: an existing file is never overwritten."""
    if run["status"] == "running":
        raise ValueError("The run is still in progress; export it when it finishes.")
    os.makedirs(folder, exist_ok=True)
    manifest = {"format": "velorona.result-package", "run_id": run["run_id"], "created_at": utc_now(), "files": {}}
    for name, text in package_files(run).items():
        data = text.encode("utf-8")
        with open(os.path.join(folder, name), "xb") as f:
            f.write(data)
        manifest["files"][name] = hashlib.sha256(data).hexdigest()
    with open(os.path.join(folder, "manifest.json"), "x", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return folder


def export_package(run: dict, dest_dir: str) -> str:
    """Write the package under dest_dir/velorona-run-<id>; refuses to reuse an existing folder. Returns its path."""
    if run["status"] == "running":
        raise ValueError("The run is still in progress; export it when it finishes.")
    folder = os.path.join(dest_dir, f"velorona-run-{run['run_id']}")
    if os.path.exists(folder):
        raise FileExistsError(f"{folder} already exists; nothing was overwritten")
    return write_package(run, folder)
