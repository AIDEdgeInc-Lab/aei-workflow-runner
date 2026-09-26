# Release handoff (state on 2026-09-26; nothing pushed, merged to main, deployed or published)

## Repositories, branches, commits

| Product | Location | Branch | HEAD | Working tree |
|---|---|---|---|---|
| Shared core + CLI | `velorona-repos/aei-workflow-runner` (new local repo, no remote) | `feat/initial-shared-core` | see `git log` (last code commit e358075, then docs) | clean |
| Velorona Web (integration) | `_worktrees/map-integration` | `integration/release-2026-09-29` | 8b747e8 | clean |
| Velorona Web (earlier, preserved) | `_worktrees/map-automation` | `feat/map-automation` | 3a0b112 | clean |
| Agent 1 landing/UI | `aei-link-clearance` (main checkout) | `feat/landing-corrections` | 444b30b | Agent 1's untracked `audit/`, `shots/`, `web/samples/` (not mine, untouched) |
| QGIS plugin | `_worktrees/velorona` | `feat/automation-workflow-runs` | b10edd2 | clean |
| QGIS plugin (owner's main tree) | `velorona` | `main` | 3cd7541 | the same 5 modified files as at session start (untouched) |

The integration branch = `fb63526` (Agent 1) + `feat/map-automation` (merge, one conflict in `web/app.html`, resolved) + contract commit +
`444b30b` (merge, no conflict). No branch was rebased, reset, force-moved or deleted; the earlier branches are intact.

## Tests actually run on these commits

| Suite | Result |
|---|---|
| Core: `pytest` (163 tests: contract, engine, store, compare, report, schedule, service, 14 CLI subprocess tests incl. SIGTERM, SIGKILL, competing processes, hostile workflow file, no-network, no-GUI-imports) | 163 passed |
| Core mutation checks (8 deliberate breaks: steal locks, no state-before-act, late run pretends, retention deletes, log whitelist off, any file read, restore overwrites, timezone ignored) | 8/8 caught; control green |
| Real OS scheduler: macOS launchd agent ran `velorona-run tick`; scheduled run completed against live Open-Meteo | passed (temporary agent removed) |
| Non-editable install of the core into a scratch dir, `velorona-run --version` | passed |
| Web (integration): full `pytest tests` (incl. CSV import 21, parity 112 cases, CSP/SRI/XSS/safety scans, contract test) | 415 passed |
| Web: JS unit tests (`node --test tests/js/automation.test.js`) | 48 passed |
| Web: real Chromium automation check (`automation_browser_check.js`, real Report-Only CSP policy) | 28 passed, 0 CSP violations |
| Web: existing `xss_browser_check.js` | 43/43 (run before the 444b30b merge) |
| Web: existing `csp_browser_check.js` via wrangler | 29/29 (run before the 444b30b merge) |
| Plugin: `run_qgis_tests.sh`: Qt6 enum check clean; unit 23; QGIS e2e 450/450 (1 documented SKIP: CelesTrak unavailable); automation e2e 41/41 | passed |
| Plugin: clean-install smoke from a release zip with the core vendored and the core repo off `sys.path` | 40/40 |

**Not run:** Linux; Windows; any browser other than Chromium; Agent 1's `homepage_url_browser_check.js`; the live production-site
data-flow check; a full run of any suite on the merged `444b30b` other than the Map `pytest` and my browser check; any CI workflow;
PyPI/TestPyPI publication; the QGIS plugin-repository security scan on the new code (flake8/bandit are not installed here).

**Changed expectations, disclosed:** `clean_install_smoke.py` hard-coded 5 toolbar actions; the plugin now has 6 (the Automate action), so it
now expects 6. The plugin's workflow/run unit tests moved with the code into this repository (extended, not removed).

## Release checklist

### Safe to approve (verified above)
- Velorona Web batch panel (interactive, browser-local) on the integration branch, with Agent 1's landing work.
- QGIS plugin batch-run dialog using the shared core (bundled in the zip).
- Shared contract + fixtures, in both directions between all three products.

### Approve only as "beta / documented limits" (works, with caveats a customer must read)
- `velorona-run` on **macOS with launchd** (verified). Linux cron/systemd text is provided but **untested**. **Windows: not supported.**
- Not on PyPI: customers install from the repository. Decide how it is distributed before promising installs.

### Must remain disabled / not claimed
- Any claim of monitoring, telemetry, SNMP/OSS integration, outage prediction or unattended browser execution.
- Any claim of Linux or Windows support.
- Any suggestion that the runner works for a commercial operator on Open-Meteo's free tier (see next).

### Decisions and blockers that need the owner
1. **Open-Meteo terms.** The free API is non-commercial only (<10,000 calls/day, 5,000/hour, 600/minute; CC-BY 4.0; open-meteo.com/en/terms,
   read 2026-09-26). Web, plugin and runner all call it for every link. Commercial operators need their own subscription; there is **no API-key
   or alternative-provider support** (NOT IMPLEMENTED). Decide the customer-facing wording and whether to implement key support before release.
2. **Licensing.** The new package is Apache-2.0; the plugin is GPL-2.0-or-later and now bundles it. Confirm compatibility (GPL v3+ terms) or choose
   another licence before publishing.
3. **Plugin version.** The release zip built from the worktree is 1.1.1; your uncommitted main tree has 1.1.2 metadata and other changes. Reconcile
   versions/changelog before a release build. Rebuild the zip from a clean core commit (`package.sh` records it).
4. **Deploy.** Map deployment is manual (`wrangler pages deploy`), so merging does not deploy. The landing branch's untracked `web/samples/` and `shots/`
   are Agent 1's to commit or drop.
5. **Merge order.** Merge `feat/landing-corrections` is already inside the integration branch; review the integration diff, then decide how to land it.

### Post-release roadmap (dependencies in `docs/ARCHITECTURE.md`)
API-key/alternative terrain provider; telemetry connectors; weather-exposure in the runner/Web; Windows support; failure notifications; PyPI
publication and removal of vendoring; generating JS constants from the Python source.
