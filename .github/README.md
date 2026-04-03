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

All three trigger workflows call `test-reusable.yml` with identical steps, so PR and post-merge runs never diverge.

**Current scope** (standard GitHub Actions runners):
- **Flake8 lint** — `services/`, `service-apis/`, `backends/`, `examples/` using `DEFw/.flake8` config
- **Syntax check** — `python -m py_compile` on all `.py` files in those directories

**Matrix:** Python 3.10 and 3.12 on ubuntu-latest.

**Integration tests** require a running distributed framework (PRTE, DEFw, QPM services, simulator binaries) and must be run manually or on dedicated hardware. When self-hosted runners or a containerized stack become available, add steps to `test-reusable.yml`.

## Running checks locally

`scripts/ci-checks.sh` is the single source of truth for all checks — it is what CI runs, and what developers should run locally before pushing.

```bash
pip install flake8        # one-time dependency install
./scripts/ci-checks.sh   # run all checks
```

> **Single source of truth:** All checks are defined in `scripts/ci-checks.sh`.
> `test-reusable.yml` calls that script directly — edit it there and the change
> is automatically reflected in both CI and local runs. Update the `Dependencies`
> comment in the script if new tools are required.

## Merge controls (`check-merge-labels.yml`)

- **`do-not-merge` label** — blocks merge until removed
- **PR dependencies** — add `depends-on: #123` (or `depends-on: #123, #456`) to the PR description to block merge until those PRs are merged

## Releases

Push a version tag to trigger `test-release.yml`:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

A GitHub release is created automatically if all checks pass. Tags containing `-` (e.g., `v1.0.0-beta`) are marked as prereleases.
