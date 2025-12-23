# QFw Setup Instructions

These steps summarize the setup and installation process used during prior
hackathons and installation recipes. Some steps may still be
environment-specific.

---

## 1. Initial Directory Setup

```bash
mkdir qhpc
cd qhpc
```

### Clone from Git

```bash
git@code.ornl.gov:hpcqc/applications.git
git@code.ornl.gov:a2e/qfw.git
git checkout qfw-initial
git submodule update --init --recursive
```
Alternatively you can explicitly checkout DEFw in the QFw directory
```bash
git clone git@github.com:amirshehataornl/DEFw.git
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

### Update the modules file:

#### BASE_DIR

Edit the following file:

```bash
modules/quantum/qsim
```

Change:

```bash
set BASE_DIR /sw/frontier/qhpc/
```

to your local `qhpc` path.


#### Update the openmpi module

The QFw uses open MPI and libfabric to manage the simulation backend.
Load the correct ones. Ex:

```bash
module use /sw/frontier/ums/ums024/modules
module load openmpi/5.0.9.debug
```

---

## 3. Proxy Setup (If Required)

Proxy setup might be necessary depending on the setup of the network.

For Frontier and Borg clusters:

```bash
export all_proxy=socks://proxy.ccs.ornl.gov:3128/
export ftp_proxy=ftp://proxy.ccs.ornl.gov:3128/
export http_proxy=http://proxy.ccs.ornl.gov:3128/
export https_proxy=http://proxy.ccs.ornl.gov:3128/
export no_proxy='localhost,127.0.0.0/8,*.ccs.ornl.gov'
```

---

## 4. Load qsim Module

```bash
module use $PWD/modules/
module load quantum/qsim
# Load the desired python version. Ex:
moduel load cray-python/3.11.7
```

Verify:

```bash
which python
which pip
```

---

## 5. Create and Activate Python Virtual Environment

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

## 6. Reset Modules and activate Environment

```bash
module reset
module use /path/to/your/modules
module load quantum/qsim

source qfwVirtEnv/bin/activate
```

---

## 7. Install Python Dependencies

Ensure your python environment is activated.

```bash
# install DEFw requirements
cd <BASE_DIR>/DEFw
python -m pip install -r requirements.txt

# install QFw requirements
cd <BASE_DIR/QFw
python -m pip install -r requirements.txt

# install any other application requriements
```

---

## 8. Build the DEFw

Ensure your python environment is activated.

### Clean Build Artifacts

```bash
cd QFw/DEFw/src
rm -rf __pycache__
rm -f *.so defwp
cd ..
```

### Build DEFw

```bash
which python
which pip
which scons
scons --version

scons .
```

---

## 8. Install Backend Libraries

Ensure your python environment is activated.

The DEFw has a qiskit backend which is used with both qiskit and
pennylane.


```bash
cd QFw/backends
pip install -e .
```

---

## 9. Configure DEFw Runtime Preferences

The DEFw has a runtime configuration file which is picked up from the tmp
directory configured in the qsim module file:

```bash
setenv QFW_TMP_PATH $home_dir/QFwTmp
```

This configuration file is used to set the logging verbosity, timeouts and
other DEFw framework parameters.

Edit:

```bash
$QFW_TMP_PATH/defw_app_pref.yaml
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

## 10. Run QFw Example Tests

### Allocate Compute Resources (Recommended Environment)

Use SLURM's heterogeneous feature to allocate two job components:

- Job component one is used for the application
- Job component two is used for the simulation environment

```bash
salloc -N 1 -t 4:00:00 -A <project> --network=single_node_vni: -N 1 -t 4:00:00 -A <project> --network=single_node_vni
```

---

### Run a simple supermarq example

ssh to the head node of job component one, if you weren't placed their
automatically.

```bash
cd QFw/examples
```

Activate your python environment

```bash
source qfwVirtEnv/bin/activate
```

Run:
```bash
./qfw_test.sh
./qfw_supermarq.sh async 1 4 100 0 ghz nwqsim
```

