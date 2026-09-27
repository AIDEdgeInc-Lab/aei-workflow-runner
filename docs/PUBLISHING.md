# Publishing process

This repository is public (`AIDEdgeInc-Lab/aei-workflow-runner`). The setup mirrors the other AEI packages (`aei-geo-features`,
`aei-microwave-link-exposure`, `aei-link-clearance`).

## How a release is published

- **Normal path:** a maintainer publishes a GitHub Release whose tag is `v<version>` (for example `v0.1.0`). That fires the workflow's
  `release: published` trigger, which builds the wheel and sdist and publishes them to production PyPI (`environment: pypi`). The build
  job fails if the tag does not equal the version in `pyproject.toml`.
- **Manual path:** `workflow_dispatch` with a `target` of `testpypi` (default) or `pypi`. `pypi` is never the default.
- There is no `push` or `pull_request` trigger. Ordinary pushes never publish.

## Trusted Publishing, no token

PyPI Trusted Publishing lets this workflow (identified by owner, repository, workflow filename and environment) obtain a short-lived
upload credential from PyPI over OIDC at publish time. No long-lived PyPI token exists, is stored in GitHub secrets, or can leak in a log.
The upload step is `pypa/gh-action-pypi-publish` pinned to an immutable commit SHA.

## Registration (done on PyPI, by a human)

| Field | Value |
|---|---|
| PyPI project | `aei-workflow-runner` |
| Owner | `AIDEdgeInc-Lab` |
| Repository | `aei-workflow-runner` |
| Workflow | `publish.yml` |
| Environment | `pypi` |

A pending publisher is registered on pypi.org for these values. **None is registered on test.pypi.org**, so the `testpypi` job cannot
authenticate until one is (environment `testpypi`).

The `pypi` GitHub environment requires manual reviewer approval, so a production deployment always pauses in the Actions UI.

## Versions

PyPI never allows a version to be re-uploaded or replaced (a release can only be yanked). Bump `version` in `pyproject.toml` and
`src/aei_workflow/__init__.py` together for every release. Check https://pypi.org/project/aei-workflow-runner/ for the actual state.
