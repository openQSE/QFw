# QFw Setup Instructions

These steps summarize the setup and installation process used during prior hackathons and installation recipes. Some steps may still be environment-specific.

---

## 1. Initial Directory Setup

```bash
mkdir qhpc
cd qhpc
```

Copy required directories from the shared installation:

* `applications`
* `modules`
* `QFw`

From:

```text
/sw/frontier/lustre/qhpc
```

### Alternative: Clone from Git

```text
git@code.ornl.gov:hpcqc/applications.git
git@code.ornl.gov:a2e/qfw.git
https://github.com/amirshehataornl/DEFw
```

---

## 2. Update Hardcoded Paths

### Update paths in `QFw/examples` and `QFw/setup`

Use `sed` to replace old paths with your local path:

```bash
find /path/to/directory -type f -exec sed -i 's/old_string/new_string/g' {} +
```

Example:

```bash
find . -type f -exec sed -i 's/sw\/frontier\/lustre\/orion\/stf006\/scratch\/QC/your\/path\/here/g' {} +
```

### Update BASE_DIR

Edit the following file:

```text
modules/quantum/qsim
```

Change:

```text
set BASE_DIR /sw/frontier/qhpc/
```

to your local `qhpc` path.

---

## 3. Rebuild DEFw Locally

### Load Required Modules

```bash
module use modules/quantum
module load qsim
module load swig
```

### Install SCons

```bash
pip install scons
```

### Proxy Setup (If Required)

```bash
export all_proxy=socks://proxy.ccs.ornl.gov:3128/
export ftp_proxy=ftp://proxy.ccs.ornl.gov:3128/
export http_proxy=http://proxy.ccs.ornl.gov:3128/
export https_proxy=http://proxy.ccs.ornl.gov:3128/
export no_proxy='localhost,127.0.0.0/8,*.ccs.ornl.gov'
```

### Build DEFw

```bash
cd QFw/DEFw
scons
```

---

## 4. Allocate Compute Resources (Recommended Environment)

```bash
salloc -N 1 -t 4:00:00 -A <project> --network=single_node_vni
```

---

## 5. Configure DEFw Runtime Preferences

Edit:

```text
QFwTmp/defw_app_pref.yaml
```

Example configuration:

```yaml
editor: /usr/bin/vim
loglevel: defw_app
halt_on_exception: false
remote copy: false
RPC timeout: 300
num_intfs: 3
cmd verbosity: true
```

---

## 6. Run QFw Example Tests

From:

```bash
cd QFw/examples
```

Run:

```bash
./qfw_test.sh
./qfw_supermarq.sh async 1 4 100 0 ghz nwqsim
```

---

# Alternative Setup Using Provided Tarball

A tarball containing required files may be available at:

```text
/lustre/orion/world-shared/stf006/.../qfw_related.tar.gz
```

---

## 7. Extract and Prepare

```bash
tar -xvf qfw_related.tar.gz
cd qfw_related
chown -R $USER:$USER .
```

---

## 8. Update Paths in Modules

* In `modules/qsim`, replace any hardcoded home directory paths with the absolute path to this directory (ensure a trailing `/`).
* Use search/replace (VSCode or terminal) to remove any remaining old home directory references.
* Verify no stale paths remain.

---

## 9. Load QSim Module

```bash
module use $PWD/modules/
module load quantum/qsim
```

Verify:

```bash
which python
which pip
```

---

## 10. Create and Activate Python Virtual Environment

```bash
python -m venv qfwVirtEnv
source qfwVirtEnv/bin/activate
```

Verify:

```bash
which python
which pip
```

---

## 11. Reset Modules and Reactivate Environment

```bash
deactivate

module reset
module use /path/to/your/modules
module load quantum/qsim

source qfwVirtEnv/bin/activate
```

---

## 12. Install Python Dependencies

```bash
pip install qiskit
pip uninstall scons
pip install --no-cache-dir scons PyYAML netifaces paramiko
pip install qiskit-aer==0.17.1
```

---

## 13. Clean Build Artifacts

```bash
cd QFw/DEFw/src
rm -rf __pycache__
rm -f *.so defwp
cd ..
```

---

## 14. Build DEFw

```bash
which python
which pip
which scons
scons --version

scons .
```

---

## 15. Install Backend Libraries

```bash
cd QFw/backends
rm -rf __pycache__
pip install -e .
```

---

## 16. Run Validation Tests

```bash
cd QFw/examples
# If needed:
unset PYTHONNOUSERSITE

./run_supermarq.sh ghz 8 nwqsim MPI CPU sync 10
