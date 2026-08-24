# Install QFw in the Standard Site Location

Use a versioned, immutable site prefix and expose it through a stable `current`
link. QFw documentation uses `/opt/openqse/qfw/current` as the conventional
site path.

This recipe requires site-administrator access and a maintenance window when
changing the `current` link.

## 1. Select the release paths

```bash
export QFW_SRC=/path/to/QFw
export QFW_VERSION=v0.1.0
export QFW_BUILD="${HOME}/.cache/qfw/build-${QFW_VERSION}"
export QFW_RELEASE_PREFIX="/opt/openqse/qfw/releases/${QFW_VERSION}"
export QFW_VENV="/opt/openqse/qfw/venvs/${QFW_VERSION}"
export QFW_CURRENT=/opt/openqse/qfw/current
```

## 2. Prepare site-owned directories and the Python environment

Run these commands as the account responsible for the QFw installation. The
exact ownership policy is site-specific.

```bash
sudo install -d -o "${USER}" -g "$(id -gn)" \
  "$(dirname "${QFW_RELEASE_PREFIX}")" \
  "$(dirname "${QFW_VENV}")"

python3 -m venv "${QFW_VENV}"
source "${QFW_VENV}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "${QFW_SRC}/setup/build-requirements.txt"
python -m pip install -r "${QFW_SRC}/setup/requirements.txt"
```

Install provider and simulator dependencies in the venv or supply them through
the site module environment.

## 3. Install the versioned release

```bash
cd "${QFW_SRC}"
git submodule update --init --recursive
./setup/qfw_install.sh \
  --build-dir "${QFW_BUILD}" \
  --prefix "${QFW_RELEASE_PREFIX}" \
  --with-defw
```

Do not install a different build over the same release directory. Select a new
versioned prefix instead.

## 4. Publish the stable path

After validating the versioned prefix, update the stable link:

Create a temporary link and rename it into place so readers never observe a
partially updated link:

```bash
sudo ln -s "${QFW_RELEASE_PREFIX}" "${QFW_CURRENT}.new"
sudo mv -Tf "${QFW_CURRENT}.new" "${QFW_CURRENT}"
```

This intentionally fails if `current` is a real directory rather than a
symlink. Migrate such an installation during a planned maintenance operation
instead of overwriting it in place.

On a multinode system, deploy the installation and stable link consistently on
every node, or place `/opt/openqse/qfw` on shared storage.

The installation is now complete. Activation belongs to the application or
service runtime.

For a site-owned service, [configure and start the long-running
QPM](configure-long-running-qpm.md) before running applications against it. For
application-owned services, continue directly with [same-node
execution](test-same-node.md) or [heterogeneous
execution](test-heterogeneous-allocation.md). `qfw-setup(1)` starts the required
services within the application allocation. Run `man 1 qfw-setup` after
activation for its runtime and service options.

A production site module or service wrapper should select the matching QFw
prefix and virtual environment when the runtime starts.

<details>
<summary>Installation verification and build-environment cleanup</summary>

```bash
readlink -f /opt/openqse/qfw/current
test -x "${QFW_CURRENT}/bin/qfw-activate"
test -x "${QFW_CURRENT}/bin/qfw-setup"
test -x "${QFW_CURRENT}/bin/qfw-status"
test -x "${QFW_CURRENT}/bin/qfw-srun"
test -x "${QFW_CURRENT}/bin/qfw-teardown"
test -x "${QFW_CURRENT}/bin/qfw-dir-svc"
test -x "${QFW_CURRENT}/bin/qfw-qpm-svc"
test -r "${QFW_CURRENT}/share/man/man1/qfw-setup.1"
test -x "${QFW_CURRENT}/bin/defw-python"
test -d "${QFW_CURRENT}/share/qfw"
```

Confirm the prefix is visible from an allocated node:

```bash
srun --nodes=1 --ntasks=1 test -x "${QFW_CURRENT}/bin/qfw-setup"
```

Leave the build-time Python environment with:

```bash
deactivate
```

</details>
