# QFw Docker Builds

This directory builds two container images that together provide a
ready-to-run QFw environment: a toolchain "builder" image and a "qfw"
image layered on top of it that contains the actual QRMI, QDMI, and QFw
installation. A `Makefile` orchestrates both builds and derives their
image tags, build args, and versions from one place.

## Quick Start

1. Build all docker images:

    ```sh
    # Build images, use explicit, instead of the auto-generated, tags:
    make docker-build IMAGE_TAG=latest
    ```

2. Run the qfw image as a long-lived container and exec into it:

    ```sh
    docker run -d --name qfw openqse/qfw:<tag>
    docker exec -it qfw bash
    ```

Run `make help` and read the [Typical Usage](#typical-usage) below to
control different aspects of the build process and set explicit
overrides of the defaults.

## Docker Images

Each built docker image has its own Dockerfile.

### `Dockerfile.builder`

This dockerfile generates the `openqse/builder-qfw`, which installs the
OS-level build toolchain needed by everything downstream. It also sets
standard [OCI image labels](https://github.com/opencontainers/image-spec/blob/main/annotations.md).

This image has no QFw-specific content — it's purely the compiler/toolchain
layer, published separately so it can be rebuilt (or cached) independently
of application code changes.

### `Dockerfile.qfw`

This Dockerfile builds the `openqse/qfw`

It uses the `openqse/builder-qfw` image as its base and layers the build
into different build stages for the QRMI, QDMI, and QFw respectively.

The **QFw** — installs a couple of extra packages and then builds
and installs QFw itself mirroring the manual "Build QFw" steps in the
top-level [README.md](../README.md).

Notably, the QFw source tree is **not** `COPY`-ed into the image.
It's bind-mounted read-only at build time
(`--mount=type=bind,source=.,target=/workspace,ro`), so only the
built/installed artifacts end up in the image layer — not the source,
`.git`, or submodule metadata. This has two consequences for anyone
building this image directly:
- The build context passed to `docker build` must be the QFw
  repository root (not the `docker/` directory), since `source=.` is
  resolved relative to the build context.
- Submodules must already be initialized on the host
  (`git submodule update --init --recursive`) before building —
  the bind mount only exposes what's already on disk.

All stages install everything under one configurable root,
`${INSTALL_DIR}` (conventionally `/opt/qfw`), with each dependency in
its own `INSTALL_DIR/<package-name>` subdirectory.

The image is meant to be run as long-lived containers on can `exec` into,
not one-shot jobs or daemons.

## Dependency Versions

The versions of key dependencies are controlled from a single place.

The [VERSIONS.env](VERSIONS.env) file is a plain `KEY=VALUE` file (no
`export`, no quoting) so it can be read both by the Makefile (via
`include`) and used in a shell (`source VERSIONS.env`).

This file the single place to bump dependency versions — nothing else
in `docker/` should hard-code them.

## Implementation Details

This section describes how the Makefile ties the builds together.

Run `make help` from this directory for a live list of targets and
tunable variables; the sections below explain the mechanics behind it.

### Image naming and tagging

Each image name is composed as `$(IMAGE_NAMESPACE)/$(IMAGE_NAME):$(TAG)`,
e.g. `openqse/builder-qfw:0.1.260823`. The tag logic supports two modes:

- **Explicit tag** — pass `IMAGE_TAG=<something>` on the `make` command
  line and both images are tagged with exactly that value.
- **Derived tag (default)** — if `IMAGE_TAG` isn't set, it's computed as
  `$(IMAGE_VERSION).<timestamp>`, e.g. `0.1.260823`. Inside GitHub
  Actions (`GITHUB_ACTIONS` is set) the timestamp includes precise
  `H:M:S` to keep concurrent CI builds from colliding; local, non-CI
  builds use a date-only timestamp instead.

The built images share the same tag value, so a given `make docker-build`
invocation produces `openqse/builder-qfw:<tag>` and `openqse/qfw:<tag>` as
a matched pair.

### Build args

Two build-arg lists are assembled from Make variables and passed with
`--build-arg`. The Make variables themselves are generated from the
versions file named in the `VERSIONS` variable.

The ARGs specify Dockerfile LABELs, base images etc. for the build.

### Build context

The two images need different build contexts:

- `docker-build-builder` builds with context `.` (the `docker/`
  directory itself) — it only needs `Dockerfile.builder`, no repository
  source.
- `docker-build-qfw` builds with context `$(SOURCE_TOPDIR)`, the QFw
  repository root, computed from the Makefile's own location
  (`.../docker/Makefile` -> `..`). This satisfies the bind-mount
  requirement described above.

### Targets

- `make docker-build-builder` — builds the toolchain image.
- `make docker-build-qfw` — depends on `docker-build-builder`, so it
  always builds (or rebuilds) the toolchain image first, then builds
  the qfw image on top of it using that freshly built image as
  `BASE_IMAGE`.
- `make docker-build` — depends on both of the above, then lists the
  resulting images (`docker image ls 'openqse/*<tag>'`) as a summary.

### Self-documentation

The `make help` command prints usage information.

Select Makefile targets and variables are listed via `make help` command.
Each item that is listed contains a doc string behind the `##` delimiter
The doc string should be on the same line, *after* the construct.

There are two kinds of self-documenting annotations using `awk`:
  - `target: ## description` — shown under "Targets".
  - `VAR ?= value ## description` — shown under "Variables".

The latter pattern tolerates optional whitespace around `?=` (e.g. both
`VAR ?= value` and `VAR?=value` are recognized).

### Other knobs

- `FORCE=1` (or any non-empty value) — adds `--no-cache` to both docker
  builds, bypassing layer caching entirely.
- `ROCKYLINUX_VERSION`, `QRMI_VERSION`, `QDMI_VERSION` — override
  anything read from `VERSIONS.env` for a one-off build, e.g.
  `make docker-build-qfw QRMI_VERSION=0.18.0`. If `VERSIONS.env` is
  present (the normal case) its values win via `include`; the `?=`
  fallbacks in the Makefile (which resolve to a deliberately-broken
  `<undefined>` placeholder) only kick in if the file can't be found at
  all — which, per the `$(error ...)` above, actually aborts the build
  first.
- `DOCKER_INSTALL_DIR` — where dependencies land inside the qfw image
  (default `/opt/qfw`).

## Typical usage

```sh
# Build images with auto-generated tags:
make docker-build

# Pin an explicit tag for both images instead:
make docker-build IMAGE_TAG=latest

# Force a clean rebuild, ignoring the docker layer cache:
make docker-build FORCE=1

# Bump QRMI for a one-off build without touching VERSIONS.env:
make docker-build-qfw QRMI_VERSION=0.18.0
```
