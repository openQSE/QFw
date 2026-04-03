# QFw CI Overview

## Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `test-pr.yml` | PR → main | Run checks on every pull request |
| `test-main.yml` | Push → main | Run checks after merge |
| `test-release.yml` | Tag `v*` | Run checks then create GitHub release |
| `test-reusable.yml` | Called by above | Shared test logic (single source of truth) |
| `check-merge-labels.yml` | PR → main | Block merge on labels or unmet dependencies |

## Test logic (`test-reusable.yml`)

All three trigger workflows call `test-reusable.yml` with identical jobs, so PR and post-merge runs never diverge.

**Current scope** (standard GitHub Actions runners):
- **`ci-syntax`** — Flake8 lint on `services/`, `service-apis/`, `backends/`, `examples/`, plus `python -m py_compile` on those directories
- **`ci-mock`** — `python -m pytest tests/mock -q`

**Matrix:** Python 3.10 and 3.12 on ubuntu-latest, configured once via the reusable workflow's shared `python_versions` input.

**Integration tests** require a running distributed framework (PRTE, DEFw, QPM services, simulator binaries) and must be run manually or on dedicated hardware. When self-hosted runners or a containerized stack become available, add steps to `test-reusable.yml`.

## Running checks locally

`.github/scripts/ci-syntax.sh` and `.github/scripts/ci-mock.sh` are the local wrappers for the CI checks. CI runs them as separate jobs so GitHub reports distinct checks for lint/syntax and mock-test failures, and developers can run either one locally before pushing.

```bash
pip install flake8 pytest        # one-time dependency install
./.github/scripts/ci-syntax.sh   # run lint and syntax checks
./.github/scripts/ci-mock.sh     # run CI-safe mock tests
```

> **CI helper location:** CI-oriented shell helpers live in `.github/scripts/`.
> `test-reusable.yml` calls those scripts directly — edit them there and the change
> is automatically reflected in both CI and local runs. Update the `Dependencies`
> comment in each script if new tools are required.

## Merge controls (`check-merge-labels.yml`)

- **`do-not-merge` label** — blocks merge until removed
- **PR dependencies** — add `depends-on: #123` (or `depends-on: #123, #456`) to the PR description to block merge until those PRs are merged

## Releases

Push a version tag to trigger `test-release.yml`:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

A GitHub release is created automatically if all checks pass. Tags containing `-` (e.g., `v1.0.0-beta`) are marked as prereleases.
