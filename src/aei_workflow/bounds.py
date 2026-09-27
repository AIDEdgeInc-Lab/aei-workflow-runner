"""Terrestrial Path Clearance input bounds and their stated basis -- the single source of truth.

Product-neutral: no QGIS, no browser. The QGIS plugin builds its parameter dialog from this list, the runner
validates every input row against it, and the Web Map's evidence.js TERRESTRIAL_BOUNDS is held equal to it by
tests/test_bounds_contract.py (against contract/terrestrial_bounds.json) and by the Map repo's own copy of that file.

An out-of-range value is refused, never clamped.
"""

# Bounds carry their own basis, because an out-of-range value is accepted,
# computed and exported typed "Calculated" -- a physically meaningless number
# wearing the same authority as a real one. Each "basis" below says where the
# limit comes from; where no citable source exists it says so explicitly
# rather than presenting an engineering judgment as derived.
TERRESTRIAL_PARAM_SPEC = [
    {"key": "site_a_height_m", "label": "Site A antenna height", "type": "float",
     "default": 30.0, "suffix": " m", "min": 0.1, "max": 1000.0,
     "basis": "Conservative engineering limit, not a derived bound: no library or "
              "standard constrains antenna height. 1000 m clears the CN Tower (553 m) "
              "and the tallest guyed mast (~628 m) with margin."},
    {"key": "site_b_height_m", "label": "Site B antenna height", "type": "float",
     "default": 30.0, "suffix": " m", "min": 0.1, "max": 1000.0,
     "basis": "Conservative engineering limit, not a derived bound: no library or "
              "standard constrains antenna height. 1000 m clears the CN Tower (553 m) "
              "and the tallest guyed mast (~628 m) with margin."},
    {"key": "frequency_ghz", "label": "Frequency", "type": "float",
     "default": 7.0, "suffix": " GHz", "min": 0.1, "max": 100.0,
     "basis": "Lower bound derived: aei_link_clearance.fresnel.fresnel_radius_m and "
              "terrain.py both raise ValueError for a non-positive frequency. The "
              "0.1-100 GHz envelope is a conservative engineering limit, not a model "
              "range -- Fresnel geometry has no frequency ceiling. It brackets the "
              "loaded ISED Fixed Service data (915.1 MHz to 85.75 GHz) so it cannot "
              "exclude a real record."},
]


def bounds() -> dict:
    """{key: (min, max)} for every bounded parameter."""
    return {p["key"]: (p["min"], p["max"]) for p in TERRESTRIAL_PARAM_SPEC if "min" in p}


def contract() -> dict:
    """The machine-readable form written to contract/terrestrial_bounds.json (labels and units, no prose)."""
    return {"schema": "velorona.terrestrial-bounds", "version": 1,
            "bounds": {p["key"]: {"min": p["min"], "max": p["max"], "label": p["label"], "suffix": p.get("suffix", "").strip()}
                       for p in TERRESTRIAL_PARAM_SPEC if "min" in p}}
