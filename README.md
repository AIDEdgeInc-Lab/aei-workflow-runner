# aei-workflow-runner

[![PyPI version](https://img.shields.io/pypi/v/aei-workflow-runner.svg)](https://pypi.org/project/aei-workflow-runner/)
[![Python versions](https://img.shields.io/pypi/pyversions/aei-workflow-runner.svg)](https://pypi.org/project/aei-workflow-runner/)
[![License](https://img.shields.io/pypi/l/aei-workflow-runner.svg)](https://github.com/AIDEdgeInc-Lab/aei-workflow-runner/blob/main/LICENSE)
[![CI](https://github.com/AIDEdgeInc-Lab/aei-workflow-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/AIDEdgeInc-Lab/aei-workflow-runner/actions/workflows/ci.yml)

The product-neutral core of Velorona's workflow automation, plus a headless CLI, `velorona-run`, for **unattended local
runs started by your own operating-system scheduler**.

* **Shared by two independent products.** The Velorona QGIS plugin uses this package as a library. Velorona Web (the browser Map)
  cannot run Python, so it implements the same versioned documents in JavaScript and is held to them by tests. Neither product
  needs the other, and neither needs this CLI to work.
* **Local-first.** Everything is written to a folder you choose. No AID Edge server, account, database, cloud service or
  telemetry is involved, and none is required.
* **Scope today: terrestrial Path Clearance** on your own link list (CSV). It is read-only analysis and decision support. It does
  **not** poll SNMP, ingest operator telemetry, predict outages, or change any network equipment.

Status: 0.1.1 (alpha; 0.1.0 was the first public release). It is offered as-is under the Apache License 2.0; see the limitations below for what has and has not been tested.

## What it is not (read this first)

* **Not a monitoring system.** A scheduled run re-analyses your link list against terrain data. Repeated runs give you a history of
  *analysis results*, not telemetry samples over time, and not a replay of past conditions (terrain is a static model).
* **Not an always-on service.** It runs when your OS scheduler starts it. If the computer is off or asleep, nothing runs; the next
  run records that occurrence as **missed** (it never pretends it ran). Use an always-on machine or server for overnight jobs.
* **Not a bundled data source.** Every link analysis asks the Open-Meteo elevation service for terrain. **Open-Meteo's free tier
  is for non-commercial use only** (its terms: fewer than 10,000 calls/day, 5,000/hour, 600/minute, CC-BY 4.0 attribution;
  checked 2026-09-26 at open-meteo.com/en/terms). A commercial operator needs its own Open-Meteo commercial subscription and
  pays for it directly. This package has **no API-key or alternative-provider support yet** (NOT IMPLEMENTED), so a commercial
  plan cannot be used through it today.

## Install

```sh
python3 -m pip install aei-workflow-runner        # Python 3.9+; installs aei-link-clearance[elevation] and its dependencies
velorona-run --version
```

For development: `python3 -m pip install -e ".[dev]"` from a clone of this repository, then `pytest`.

Tested with Python 3.9.6 and 3.12.11 on macOS (Apple silicon). Linux is expected to work (POSIX code paths, cron/systemd
templates) but was **not tested by the maintainers**. Windows is **not supported** in this release: lock-liveness checks and Task
Scheduler setup were not implemented or tested.

## Verified local run

```sh
export VELORONA_STORE="$HOME/velorona-data"          # YOUR folder; there is deliberately no default location
velorona-run init --name "Nightly check" --links "$HOME/links/latest.csv" --out nightly.json
velorona-run validate nightly.json                    # checks the workflow and the CSV, runs nothing
velorona-run install nightly.json                     # puts it in the store
velorona-run run --workflow-id <id printed by init>   # one manual run
velorona-run status                                   # last run, errors, next due, lock state
```

`--links` may be a CSV file, or a folder (the newest `*.csv` in it is used each run, so a nightly export dropped in that folder
is picked up automatically). Required CSV columns are exactly those of `aei_link_clearance.batch`:
`link_id, site_a_lat, site_a_lon, site_a_height_m, site_b_lat, site_b_lon, site_b_height_m, frequency_ghz`.

## Verified recurring run (via your OS scheduler)

See `docs/SCHEDULING.md`. In short: give the workflow a schedule when you create it
(`--daily 02:00 --timezone America/Toronto`), install it, and have cron / launchd / systemd run `velorona-run tick` every 5
minutes. `velorona-run schedule-template --kind launchd|cron|systemd --store DIR` prints ready-to-use text; it installs nothing.
Verified end to end on macOS with launchd (see the handoff).

## Where things are stored

```
<store>/workflows/<id>.json              workflow definitions (older versions kept in workflows/_history/)
<store>/runs/<run_id>/run.json           the immutable-in-practice record of each run (also missed occurrences)
<store>/runs/<run_id>/evidence/          report.md, results.csv, links.geojson, input_links.csv, run.json, manifest.json (SHA-256)
<store>/_pruned/<run_id>/                runs moved aside by YOUR retention settings (never deleted by this program)
<store>/logs/<workflow_id>.jsonl         structured log: ids, statuses, counts, timings only (no link ids, coordinates, paths, errors)
<store>/locks/, <store>/schedule/        one-run-at-a-time lock; scheduler state
```

Writes are atomic (temp file, fsync, rename). Nothing is overwritten silently: a new run has a new id, an existing workflow
needs `--replace` and a higher `workflow_version`, backups and exports refuse to overwrite an existing file or folder.

## Retention

Set by you per workflow: `--retain-runs N` (keep the newest N) and/or `--retain-hours H` (keep runs finished in the last H hours,
e.g. 1, 2, 10, 24, or 720). With neither, **everything is kept**. Runs outside your window are *moved* to `_pruned/`, together with
their evidence; this program never deletes them. Delete `_pruned/` yourself when you decide to. Logs are not rotated or removed.

## What leaves your machine

Only the terrain request, per link: `GET https://api.open-meteo.com/v1/elevation?latitude=<50 values>&longitude=<50 values>`,
the coordinates of 50 points along the straight path between the two endpoints. Not sent: link IDs, antenna heights, frequencies,
file names, workflow names, CSV contents, run results. The request goes directly from your machine to Open-Meteo (they see your IP
address and what any HTTP client sends); Velorona does not control how they log or keep it. Nothing is sent to AID Edge.
The test suite checks that the runner opens no other network connection (every socket connect is an error in the CLI tests).

## Backup, restore, interoperability

`velorona-run backup --out FILE.zip` / `restore FILE.zip` use the same zip layout as the QGIS plugin and Velorona Web
(`docs/CONTRACT.md`): a backup from any of the three restores in the other two, tested in both directions. Restore never
overwrites an existing record and validates every file (schema version, checksum, names). `import-doc` imports a single
`run.json` or workflow file. Browser-local storage (IndexedDB) and this folder are different storage systems: nothing syncs
between them; you move data explicitly with backup/restore.

## Known limitations

* Cancel (Ctrl-C / SIGTERM) takes effect between links; a request in flight finishes first (15 s timeout).
* A crash or `kill -9` leaves the run `running` on disk, with finished links saved (checkpoint at most every 2 s). The next run of
  that workflow, or `status`/QGIS opening the store, relabels it `interrupted`.
* Lock liveness uses the process id on the same host; a lock from another host or an unreadable lock is treated as held and needs
  `velorona-run unlock --workflow-id ID --force`. A store on a network share is not recommended.
* Retry is a fixed delay, not adaptive back-off; use `--pause-seconds` to stay under a provider's rate limit.
* Only `.csv` inputs up to 50 MB are read, whatever a workflow file says.
