"""Compare two runs without overstating what the comparison means.

Every difference is filed under one of four headings, because they mean different things:

    parameters / input file   what the user asked for changed
    data source               where the terrain data came from changed
    versions                  the software or engine that calculated it changed
    link results              the numbers changed

A comparison is 'like for like' only when the method and data source are the same. Otherwise
comparable=False, the reasons are listed, and any per-link differences are shown but labelled as
not a valid before/after. Even a like-for-like comparison is two stored analyses, not a replay of
conditions: the terrain source is a static model with no observation time, so a result that moved
with nothing else changed is reported as unexplained rather than attributed to anything.
"""

from __future__ import annotations

from .schema import LINK_OK

RESULT_FIELDS = ("los_status", "near_threshold", "distance_km", "first_fresnel_radius_m", "required_clearance_m",
                 "terrain_clearance_m", "clearance_ratio", "percent_fresnel_clear", "obstruction_distance_km")
TOLERANCE = 1e-9

NOTE = ("These are two stored analyses. Neither is a reconstruction of past conditions: the terrain source "
        "is a static model and records no observation time.")


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= TOLERANCE
    return a == b


def _diff(a: dict, b: dict, keys) -> list:
    out = []
    for k in keys:
        if not _same(a.get(k), b.get(k)):
            out.append({"field": k, "a": a.get(k), "b": b.get(k)})
    return out


def _flatten(d, prefix=""):
    flat = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            flat.update(_flatten(v, key + "."))
        else:
            flat[key] = v
    return flat


def _sources(run: dict) -> str:
    return "; ".join(sorted(s.get("name", "") for s in run.get("provenance", {}).get("data_sources", [])))


def compare_runs(a: dict, b: dict) -> dict:
    reasons = []
    wa, wb = a.get("workflow_snapshot", {}), b.get("workflow_snapshot", {})
    if wa.get("engine") != wb.get("engine"):
        reasons.append(f"Different analysis engines ({wa.get('engine')} vs {wb.get('engine')}).")
    param_changes = _diff(wa.get("params", {}), wb.get("params", {}), sorted(set(wa.get("params", {})) | set(wb.get("params", {}))))
    for change in param_changes:
        reasons.append(f"Analysis parameter '{change['field']}' differs ({change['a']} vs {change['b']}), "
                       "so the results were calculated under different assumptions.")
    source_changes = []
    if _sources(a) != _sources(b):
        source_changes.append({"field": "terrain data source", "a": _sources(a), "b": _sources(b)})
        reasons.append("The terrain data source differs between the runs.")
    for r, label in ((a, "first"), (b, "second")):
        if r.get("status") == "running":
            reasons.append(f"The {label} run is still in progress.")
        elif r.get("status") == "failed":
            reasons.append(f"The {label} run failed and has no results to compare.")

    version_changes = [{"field": k, "a": va, "b": vb}
                       for k, (va, vb) in sorted(_paired(_flatten(a.get("versions")), _flatten(b.get("versions"))).items())
                       if va != vb]
    engine_versions_changed = any(c["field"].startswith("analysis_engine.") for c in version_changes)

    ia, ib = a.get("input", {}), b.get("input", {})
    input_file = {"same_workflow": a.get("workflow_id") == b.get("workflow_id"),
                  "file_changed": ia.get("sha256") != ib.get("sha256"),
                  "a": {"name": ia.get("source_name"), "sha256": ia.get("sha256")},
                  "b": {"name": ib.get("source_name"), "sha256": ib.get("sha256")}}

    la = {l["link_id"]: l for l in a.get("links", [])}
    lb = {l["link_id"]: l for l in b.get("links", [])}
    links = []
    for link_id in sorted(set(la) | set(lb)):
        entry = {"link_id": link_id}
        if link_id not in lb:
            entry.update(presence="only_a", note="Present only in the first run.")
        elif link_id not in la:
            entry.update(presence="only_b", note="Present only in the second run.")
        else:
            x, y = la[link_id], lb[link_id]
            entry.update(presence="both", status_a=x["status"], status_b=y["status"])
            if x["status"] != LINK_OK or y["status"] != LINK_OK:
                entry["note"] = "At least one run has no result for this link (failed or not run); nothing to compare."
            else:
                entry["input_changes"] = _diff(x["input"], y["input"], sorted(x["input"]))
                entry["result_changes"] = [{**c, "delta": _delta(c["a"], c["b"])}
                                           for c in _diff(x["result"], y["result"], RESULT_FIELDS)]
                entry["note"] = _attribute(entry, engine_versions_changed)
        links.append(entry)

    both = [l for l in links if l["presence"] == "both" and "result_changes" in l]
    summary = {
        "in_both": sum(1 for l in links if l["presence"] == "both"),
        "only_in_first": sum(1 for l in links if l["presence"] == "only_a"),
        "only_in_second": sum(1 for l in links if l["presence"] == "only_b"),
        "compared": len(both),
        "results_changed": sum(1 for l in both if l["result_changes"]),
        "los_status_changed": sum(1 for l in both if any(c["field"] == "los_status" for c in l["result_changes"])),
    }
    return {
        "run_a": _ident(a), "run_b": _ident(b),
        "comparable": not reasons, "reasons": reasons, "note": NOTE,
        "parameter_changes": param_changes, "input_file": input_file,
        "data_source_changes": source_changes, "version_changes": version_changes,
        "links": links, "summary": summary,
    }


def _paired(fa: dict, fb: dict) -> dict:
    return {k: (fa.get(k), fb.get(k)) for k in set(fa) | set(fb)}


def _ident(run: dict) -> dict:
    return {k: run.get(k) for k in ("run_id", "workflow_id", "workflow_name", "started_at", "status")}


def _delta(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return b - a
    return None


def _attribute(entry: dict, engine_versions_changed: bool) -> str:
    if entry["input_changes"]:
        fields = ", ".join(c["field"] for c in entry["input_changes"])
        return f"This link's inputs changed ({fields}); any result difference follows from that."
    if not entry["result_changes"]:
        return "Unchanged."
    if engine_versions_changed:
        return "Inputs are identical but the analysis engine version changed; the difference may come from that."
    return ("Inputs, parameters and engine versions are identical, yet the result differs. Nothing recorded "
            "explains it; the elevation service's data may have changed, which this product cannot observe.")
