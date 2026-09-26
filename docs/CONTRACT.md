# The shared contract (schema version 1)

Velorona Web, the Velorona QGIS plugin and `velorona-run` exchange plain JSON documents. Rules that apply to all of them:

* Every document has `schema` and an integer `schema_version`. A reader refuses a **newer** version (it never rewrites it) and
  reports the file, so an old install cannot damage a record written by a new one.
* New fields are **additive and optional** within a version. Readers ignore fields they do not know and **preserve** them on
  re-export. Removing or changing the meaning of a field requires a new schema version and a written migration.
* Documents are **data**. Nothing in a workflow or run is executed. Ids become file names, so they are restricted to
  `[A-Za-z0-9_-]{1,64}`; paths and file names inside documents are treated as untrusted.
* Imports never overwrite an existing record, and a run keeps the id it was created with.

## `velorona.workflow / 1`

| Field | Meaning |
|---|---|
| `workflow_id`, `name` | identity; `name` is free text |
| `engine` | `terrestrial-clearance` (the only supported engine) |
| `input` | `{kind: "csv", path: string or null}`; a file, or a folder whose newest `.csv` is used (runner only) |
| `params` | `k_factor` (>0, default 4/3), `n_samples` (2 to 100; **Velorona Web only runs 50**, the number its data disclosure states) |
| `param_units` | documentation of units |
| `execution` | `max_attempts` 1-5, `retry_delay_s`, `pause_between_links_s` |
| `output` | `retain_runs` (int or null), optional `retain_hours` (number or null), optional `write_evidence` (bool, runner) |
| optional `workflow_version` | your revision number, integer >= 1 (default 1) |
| optional `schedule` | `{kind: "manual"}`, `{kind: "interval", every_minutes: 5..10080}`, `{kind: "daily", at: "HH:MM"}`, `{kind: "weekly", days: ["mon",..], at: "HH:MM"}`; each may have `grace_minutes` (0..1440, default 15). **Only `velorona-run tick` acts on it**; Web and QGIS keep it unchanged and never run it |
| optional `timezone` | IANA name (default `UTC`); schedule times are wall-clock in this zone |

Thresholds (60% first-Fresnel "clear", 30% marginal/obstructed split, 15 m elevation-uncertainty band) are properties of the
analysis engine (`aei-link-clearance` and its parity-tested JS port), not of the workflow, and are recorded in each run's
`assumptions_and_limitations`. Input bounds are in `contract/terrestrial_bounds.json` (single source: `aei_workflow.bounds`).

## `velorona.run / 1`

Identity and lineage: `run_id`, `workflow_id`, `workflow_name`, `workflow_version`, `workflow_snapshot` (the full workflow as run),
`schedule: {trigger: "manual"|"scheduled", scheduled_for, timezone}`, `started_at`, `finished_at` (UTC, `+00:00`).

`status`: `running` (on disk only while in flight or after a crash), `completed` (every input row analysed), `partial`, `failed`
(no link produced a result), `canceled`, `interrupted` (a `running` record relabelled after a crash; finished links kept), and
`missed` (a scheduled occurrence that did not run; `links` is empty and `error` says why; **never a result and never comparable**).

Inputs: `input {source_name, sha256, columns}` (a fingerprint, not the data), `rejected_rows [{source_row, link_id, reason}]`.
Results: `links [{link_id, source_row, status ok|failed|not_run, input, result|null, error|null, warnings, analyzed_at, elapsed_s}]`,
`counts`. A failed link has `result: null` and an `error`; an error is never converted into a result.

Provenance and versions: `provenance.data_sources[]` (terrain = Open-Meteo Elevation, Copernicus DEM GLO-90, a static surface
model; `observation_time: null`; retrieval time is `links[].analyzed_at`), `provenance.historical_replay_supported: false`,
`versions` (implementation, engine versions, runner version, schema versions), `assumptions_and_limitations`, `error`.

## Backup bundle (zip) and result package

Backup: `manifest.json {format: "velorona.backup", format_version: 1, files: {name: sha256}}`, `workflows/<id>.json`,
`runs/<run_id>/run.json`. Nothing else is a member (evidence is regenerable with `export`). Restore verifies every checksum, name
and id, skips what already exists, and reports what it rejected.

Result package folder `velorona-run-<id>/`: `run.json`, `input_links.csv` (canonical input schema, **re-importable, tested**),
`results.csv` (spreadsheet-safe: formula-like cells neutralised; for people and spreadsheets, not for GIS import),
`links.geojson` (GIS-ready), `report.md`, `manifest.json` (SHA-256 of each file). Only `input_links.csv` and `run.json` are
claimed to re-import.

## Storage differences (do not conflate)

| | Velorona Web | QGIS plugin | `velorona-run` |
|---|---|---|---|
| Where | browser IndexedDB (per browser profile, per device) | JSON files in the QGIS profile, or any folder you choose | JSON files in `--store` |
| Can be lost by | clearing site data, private window, browser eviction, profile removal | deleting the folder | deleting the folder |
| Syncs anywhere | no | no | no |
| Moves between products | explicit backup/restore or `run.json` | same | same |

The plugin and the CLI can share one store folder (they take the same per-workflow lock). The browser cannot open that folder;
use backup/restore.

## Evidence kinds (what a history is and is not)

1. **Single-run evidence**: one run's results, warnings and report.
2. **Repeated scheduled run history**: many such runs over time, plus explicit `missed` records. Useful for "what did each nightly
   analysis say", not a time series of network behaviour.
3. **Telemetry samples over time**: **NOT IMPLEMENTED.** A future connector would be a new engine plus a new data-source entry in
   `provenance.data_sources`; nothing in the schema blocks that, and nothing here claims it exists.
