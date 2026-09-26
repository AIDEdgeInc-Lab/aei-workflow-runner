# Architecture and product boundaries

```
                 velorona.workflow/1  +  velorona.run/1  +  backup zip     (docs/CONTRACT.md, tests, fixtures)
                                  |
   +------------------------------+-------------------------------+
   |                              |                               |
Velorona Web                aei-workflow-runner              Velorona QGIS plugin
(browser, JavaScript)       (Python, no UI)                  (Python, QGIS UI)
 map, panel, IndexedDB       schema, validation, runner,      dialog, QgsTask, layers,
 JS port of the contract     store, compare, report,          its own release zip;
 + linkmath.js engine port   CLI `velorona-run`, tick         bundles this package under _vendor/
                                  |
                          aei-link-clearance (unmodified analysis; Python)
```

## One engine, two implementations of the *terrain maths*, checked against each other

The analysis calculation (Fresnel geometry, earth curvature, thresholds, status, explanation) lives in `aei-link-clearance`
(Python). Velorona Web has always carried a JavaScript port of it (`linkmath.js`, `explain.js`) because a browser cannot run that
package. This work did **not** add a second copy: the automation code in both places calls the existing engine. What keeps the
two engines from diverging is a parity test (`tests/test_web_automation_parity.py` in the Map repo: 112 cases, every result field
and the explanation text, tolerance 1e-9, including all three statuses, the near-threshold flag and an antimeridian crossing).

## What is shared, and how

| Piece | Where it lives | Used by |
|---|---|---|
| Schemas, validation, execution semantics, comparison, evidence files, backup layout, terrestrial bounds | `aei_workflow` (this package) | QGIS plugin (library, bundled), CLI |
| The same semantics in JavaScript | Map repo `web/automation_*.js` | Velorona Web |
| Held equal by | fixtures written by each product and read by the others; `contract/terrestrial_bounds.json`; the parity test | CI / release checks |

The shared core imports no UI, no DOM and no QGIS (a test blocks those imports and runs the package). The plugin does not import
Web code; Web does not import Python or QGIS; the CLI does not import either product.

## Remaining duplication (disclosed, not hidden)

* The JavaScript port of the workflow/run logic and of the terrain maths cannot be removed while the Web product runs in a browser.
  It is controlled by tests, not by shared source. A change to schema or semantics must be made in both and both suites pass.
* The bounds table exists in `evidence.js` and `aei_workflow.bounds`; the contract test compares them. The contract JSON is copied
  into the Map repo's fixtures; if bounds change, update both copies (each repo's test fails until they agree with their code).

Smallest post-release step: publish `aei-workflow-runner` (and a release of `aei-link-clearance` if needed) to PyPI, so the plugin
can depend on it instead of vendoring, and generate the JS constants (bounds, status names, schedule vocabulary) from the Python
source.

## Distribution

* **Plugin**: `tools/package.sh` copies this package into the zip (`velorona/_vendor/aei_workflow`, with `VENDORED.txt` recording the
  commit and `LICENSE`) and refuses a dirty checkout. The installed plugin uses that copy first.
* **CLI**: install from the repository (`pip install -e .`). Not on PyPI.
* **Web**: static files, deployed by the Map repo's existing (manual) process; nothing here changes deployment.

## Licence note (needs an owner decision before publishing)

This package is licensed Apache-2.0, like the other public `aei-*` libraries. The QGIS plugin is GPL-2.0-or-later. Bundling Apache-2.0
code inside a GPL-2.0-or-later plugin zip is compatible under GPL version 3 or later, but that is a licensing decision AID Edge
should confirm (legal review) before a public release; the code originated in the plugin repository and AID Edge can choose the
licence.

## Roadmap (not implemented; do not claim)

| Item | Depends on |
|---|---|
| Commercial elevation access (API key) or an alternative terrain provider | provider choice and contract; a `provider` setting in the workflow; parity re-verification |
| Telemetry connectors (SNMP, OSS/NMS export ingestion) and time-series retention | customer data sources, credential handling design, a new engine and provenance entries |
| Weather-exposure runs in the runner and Web | live-data provenance model (current observations, not replay) |
| Windows scheduler support | Task Scheduler setup and liveness check, tested |
| Failure notification (e-mail/webhook) | customer-configured endpoint; secret handling |
| Cross-process locking on network shares | a different lock design |
| Published packages, installer | release process decisions |
