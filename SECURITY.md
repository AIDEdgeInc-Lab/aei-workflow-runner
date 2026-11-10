# Security Policy

## Supported versions

This project is published on PyPI (https://pypi.org/project/aei-workflow-runner/).
Only the latest published version and the latest commit on `main` are
supported.

| Version | Supported |
|---|---|
| 0.1.x | Yes, best-effort |
| < 0.1.0 | No |

## Reporting a vulnerability

**Please do not open a public GitHub issue for a suspected security
vulnerability.**

Report vulnerabilities privately using GitHub's private vulnerability
reporting (Security tab -> "Report a vulnerability") on the repository,
which opens a private advisory visible only to maintainers. If that
channel is unavailable, contact the maintainers through a private channel
and avoid including exploit details in any public forum.

Please include:
- A description of the vulnerability and its potential impact
- Steps to reproduce, or a minimal proof of concept
- The affected version(s)

## What to expect

- We will acknowledge receipt as soon as reasonably possible.
- We do not commit to a specific response-time or fix-time service level
  agreement (SLA). This is a small, community-maintained-scale project,
  not a commercially supported product.
- We will credit reporters who wish to be credited, once a fix is
  released.

## Scope

## Scope

`velorona-run` reads a customer's links CSV, runs terrain-clearance analyses through aei-link-clearance (which calls the Open-Meteo
Elevation API), and keeps workflows, runs, evidence and logs in a customer-owned folder. Those are its security boundaries. Each
control below is covered by tests; the test file is named.

| Boundary | Control | Where | Tests |
|---|---|---|---|
| Customer CSV input | Only regular `.csv` files up to 50 MB; UTF-8 required; non-finite numbers, out-of-range coordinates, out-of-bounds heights/frequency, identical endpoints and duplicate link ids are rejected per row with a reason, never clamped or guessed | `inputs.py`, `service.resolve_input`, `bounds.py` | `test_automation_inputs.py`, `test_service.py` |
| Ids that become file names | Workflow and run ids restricted to `[A-Za-z0-9_-]{1,64}`; checked before any path is built, including on export | `schema.valid_id`, `store.py`, `locking.py`, `report.export_package` | `test_automation_runner.py`, `test_cli.py`, `test_security_baseline.py` |
| Writes to the store | Atomic replace; the temporary file is created exclusively under an unpredictable name, so a planted file or symlink is never followed; NaN/Infinity refused in JSON; evidence files created exclusively, never overwritten | `store._atomic_write_json`, `report.write_package` | `test_security_baseline.py`, `test_automation_report.py` |
| Backup import | Only the two layouts the store writes are accepted (absolute paths, `..`, extra depth refused); 64 MB per member; SHA-256 checked against the manifest; every document schema-validated; existing records never overwritten | `store.import_backup`, `store._validate_member` | `test_automation_store.py` |
| Concurrent runs | One run per workflow: lock file created with `O_EXCL`; a stale lock is recovered only when certain (same host, process gone); never stolen otherwise | `locking.py` | `test_cli.py`, `test_security_baseline.py` |
| Logs | Privacy whitelist: only fixed fields, no link ids, coordinates, paths or exception text | `logs.py` | `test_service.py`, `test_cli.py` |
| CSV evidence opened in a spreadsheet | Cells that a spreadsheet would evaluate as a formula are written as text | `csvsafe.py` | `test_automation_report.py` |
| OS scheduler text (`schedule-template`) | Label restricted; interval bounded; paths a cron line or systemd unit would interpret are refused; launchd plist values XML-escaped; nothing is installed | `cli.schedule_template` | `test_cli.py`, `test_security_baseline.py` |

Not in scope: no credentials are stored or read, and nothing listens on a network port. The store's file permissions follow the
user's umask; keep the store in a folder only the intended users can read, because run records contain link coordinates.
Open-Meteo's free tier is for non-commercial use only.
