# Changelog

All notable changes to this project are recorded here from 0.1.2 on. Earlier releases are the git tags `v0.1.0` (2026-09-26) and `v0.1.1` (2026-10-08).

## [0.2.0] - 2026-11-22

Security baseline. Runs, results and evidence are unchanged; only invalid scheduler input and a corrupted run id are refused. Minor version because Python 3.9 support ends and the aei-link-clearance range moves.

### Changed (Python support and dependencies)

- **Requires `aei-link-clearance[elevation]>=0.4.0,<0.5`** (was `>=0.2.0,<0.3`). 0.4 carries the 2026-11 security fixes, and its `elevation` extra floors `requests>=2.33` / `urllib3>=2.8`. The full test suite was run against it before the range was set. Velorona QGIS can now share one Python environment with this package (it accepts the 0.4 line).
- **Python 3.9 is no longer supported (`requires-python >=3.10`)**: the required aei-link-clearance 0.4 and the patched requests/urllib3 need Python 3.10. pip on Python 3.9 keeps resolving 0.1.1.

### Fixed

- `schedule-template` no longer writes unchecked values into scheduler text. The label is restricted to letters, digits, `.`, `_` and `-`. `--every-minutes` must be 1-59 for cron (cron cannot express more) and 1-1440 otherwise. A store or Python path containing a character that a cron line (run by `/bin/sh`, `%` = newline) or a systemd unit would interpret (`"` `\` `$` `` ` `` `%` newline) is refused with an explanation instead of producing an injectable or broken line. Values in the launchd plist are XML-escaped, so a path with `&` or `<` gives a valid plist. The systemd `ExecStart` now quotes its paths, so a store path with spaces works.
- Store writes create their temporary file exclusively (`O_EXCL`) under an unpredictable name. A file or symlink planted at the temporary name makes the write fail instead of being followed, and a leftover from a crashed process can never collide. Permissions are unchanged (umask).
- `export_package` refuses a run whose `run_id` is not a valid id before building the output folder path. Runs written by this tool always have valid ids; this guards against a hand-edited `run.json`.
- Run history order is now deterministic. Runs are listed newest first by `started_at`, which has whole-second precision; runs recorded in the same second (for example the missed occurrences written by one `tick`) used to fall back to the order of their random run ids, so `status` could report a different "last run" from one read to the next. Ties are now ordered by the occurrence each run belongs to (`schedule.scheduled_for`; a manual run is the oldest of a tie), then by run id. This affects `status`, the `runs` listing and retention only when several runs share a second, and it made the test `test_scheduled_execution_through_tick` fail intermittently.

### Added

- `SECURITY.md`: reporting policy and the tool's security boundaries, controls and tests.
- `CHANGELOG.md` (this file).
