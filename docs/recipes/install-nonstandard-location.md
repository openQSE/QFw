# Install QFw in a Non-standard Location

Use this recipe for a user-owned, development, release-candidate, or otherwise
version-specific installation. The prefix may be any writable path, but it and
the associated Python environment must be available at the same paths on every
node that runs QFw.

## 1. Select paths

The following paths match the Docker Slurm convention. Change the final names
when installing another version or commit.

```bash
export QFW_SHARED_ROOT=/workspace/qfw-container-base
export QFW_SRC="${QFW_SHARED_ROOT}/QFw"
export QFW_VENV="${QFW_SHARED_ROOT}/qfw-release-v0.1-venv"
export QFW_BUILD="${QFW_SHARED_ROOT}/qfw-release-v0.1-build"
export QFW_INSTALL_PREFIX="${QFW_SHARED_ROOT}/qfw-release-v0.1-install"
```

## 2. Prepare the Python environment

```bash
python3 -m venv "${QFW_VENV}"
source "${QFW_VENV}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "${QFW_SRC}/setup/build-requirements.txt"
python -m pip install -r "${QFW_SRC}/setup/requirements.txt"
```

Provider clients and simulator-specific Python packages should be installed in
this environment when they are not supplied by the site or container image.

## 3. Build and install QFw and bundled DEFw

```bash
cd "${QFW_SRC}"
git submodule update --init --recursive
./setup/qfw_install.sh \
  --build-dir "${QFW_BUILD}" \
  --prefix "${QFW_INSTALL_PREFIX}" \
  --with-defw
```

Use a fresh prefix for a reproducible release installation. Reusing a prefix
can leave files from an older build that are no longer produced.

The installation is now complete. Activation belongs to the runtime
lifecycle. Continue with [same-node execution](test-same-node.md),
[heterogeneous execution](test-heterogeneous-allocation.md), or
[long-running QPM configuration](configure-long-running-qpm.md). Those recipes
activate this prefix and venv before starting an application or service.

<details>
<summary>Installation verification and build-environment cleanup</summary>

```bash
test -x "${QFW_INSTALL_PREFIX}/bin/qfw-activate"
test -x "${QFW_INSTALL_PREFIX}/bin/qfw-setup"
test -x "${QFW_INSTALL_PREFIX}/bin/qfw-status"
test -x "${QFW_INSTALL_PREFIX}/bin/qfw-srun"
test -x "${QFW_INSTALL_PREFIX}/bin/qfw-teardown"
test -x "${QFW_INSTALL_PREFIX}/bin/qfw-dir-svc"
test -x "${QFW_INSTALL_PREFIX}/bin/qfw-qpm-svc"
test -r "${QFW_INSTALL_PREFIX}/share/man/man1/qfw-setup.1"
test -x "${QFW_INSTALL_PREFIX}/bin/defw-python"
test -d "${QFW_INSTALL_PREFIX}/share/qfw"
```

Run the installation-tree smoke test if the build directory is retained:

```bash
ctest --test-dir "${QFW_BUILD}" --output-on-failure
```

```bash
deactivate
```

</details>
