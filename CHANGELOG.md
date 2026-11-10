# Changelog

All notable changes to this project are recorded here from 0.1.2 on. Earlier releases are the git tags `v0.1.0` (2026-09-26) and `v0.1.1` (2026-10-08).

## [0.1.2] - unreleased (release candidate)

Security baseline. Runs, results and evidence are unchanged; only invalid scheduler input and a corrupted run id are refused.

### Fixed

- `schedule-template` no longer writes unchecked values into scheduler text. The label is restricted to letters, digits, `.`, `_` and `-`. `--every-minutes` must be 1-59 for cron (cron cannot express more) and 1-1440 otherwise. A store or Python path containing a character that a cron line (run by `/bin/sh`, `%` = newline) or a systemd unit would interpret (`"` `\` `$` `` ` `` `%` newline) is refused with an explanation instead of producing an injectable or broken line. Values in the launchd plist are XML-escaped, so a path with `&` or `<` gives a valid plist. The systemd `ExecStart` now quotes its paths, so a store path with spaces works.
- Store writes create their temporary file exclusively (`O_EXCL`) under an unpredictable name. A file or symlink planted at the temporary name makes the write fail instead of being followed, and a leftover from a crashed process can never collide. Permissions are unchanged (umask).
- `export_package` refuses a run whose `run_id` is not a valid id before building the output folder path. Runs written by this tool always have valid ids; this guards against a hand-edited `run.json`.

### Added

- `SECURITY.md`: reporting policy and the tool's security boundaries, controls and tests.
- `CHANGELOG.md` (this file).
