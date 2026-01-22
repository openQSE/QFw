# QFw Setup Instructions

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

## 2. Create a QFw install configuration YAML file

There are two types of configuration files outlined below. All fields in
the configuration files are mandatory except highlighted ones.

  1. A configuration file based on the module utility with the following
     yaml format

```bash
base-dir: </path/to/QFw/base/directory> # example: /sw/frontier/qhpc
module-path: </path/to/module/files> # example: /sw/frontier/qhpc/modules/
module-file: </path/to/module> # example: quantum/qsim
python-venv-activate: </path/to/python/env/activation/file> # example: /home/user/tmp/qfw_venv/bin/activate
install-py-requirements: [True | False] # Optional. True to install python requirements. Defaults to false.
```

  2. A configuration file based on setting environment variables with the
     following yaml format

```bash
base-dir: </path/to/QFw/base/directory> # example: /sw/frontier/qhpc
python-venv-activate: </path/to/python/env/activation/file> # example: /home/user/tmp/qfw_venv/bin/activate
libfabric-install: </path/to/libfabric/installation/directory>
mpi-install: </path/to/open-mpi/installation/directory>
dev-install: </path/to/base/directory/for/rocm-or-cuda> # example: /opt/rocm-6.2.4/
install-py-requirements: [True | False] # Optional. True to install python requirements. Defaults to false.
```

## 3. Run the installation script

```bash
cd /path/to/QFw/base/directory/QFw/setup
qfw_install -c /path/to/yaml-config
```

The installation script will generate a `qfw_activate` script to activate
the QFw environment. This includes activating the python virtual
environment specified in the configuration. If indicated it'll install the
DEFw and QFw python requirements

## 6. Run example tests

### Allocate Compute Resources (Recommended Environment)

Use SLURM's heterogeneous feature to allocate two job components:

- Job component one is used for the application
- Job component two is used for the simulation environment

```bash
salloc -N 1 -t 4:00:00 -A <project> --network=single_node_vni: -N 1 -t 4:00:00 -A <project> --network=single_node_vni
```

---

### Activate the QFw environment

```bash
cd /path/to/QFw/base/directory/QFw/setup
source ./qfw_activate
```

### Run simple QFw example scripts

`ssh` to the head node of job component one, if you weren't placed their
automatically.

```bash
cd /path/to/QFw/base/directory/QFw/setup
```

Run:
```bash
./qfw_test.sh
./qfw_supermarq.sh async 1 4 100 0 ghz nwqsim
```

---

## Building the Distributed Execution Framework (DEFw)

The DEFw is a C wrapper around python. It augments python with a set of
features, including communication features which allow different
applications running within the DEFw to communicate with each other via
the implemented protocol.

The DEFw will need to be built before the QFw can be used


## 1. Activate the QFw environment

The first time you install make sure to set `install-py-requirements = True`

```bash
cd /path/to/QFw/base/directory/QFw/setup
source ./qfw_activate
```

---

## 8. Build the DEFw

### Clean Build Artifacts

```bash
cd QFw/DEFw/src
rm -rf __pycache__
rm -f *.so defwp
cd ..
```

### Build DEFw

```bash
scons .
```

---

## 9. Configure DEFw Runtime Preferences

The DEFw runs all the QFw services:
  - Resource manager
  - QPM services for each of the simulator types supported
  - QFw setup temporary services
  - Python application

The DEFw has a runtime configuration file which is picked up from the QFw tmp
directory:

```bash
QFW_TMP_PATH=$home_dir/QFwTmp
```

This configuration file is used to set the logging verbosity, timeouts and
other DEFw framework parameters.

Each of the services above has a configuration file. If one is not there
the DEFw will create one automatically. These are useful to manipulate
for debugging purposes.

Resource Manager: `$QFW_TMP_PATH/defw_resmgr_pref.yaml`
QPM Services: `$QFW_TMP_PATH/defw_<qpm_type>_pref.yaml`
Application: `$QFW_TMP_PATH/defw_app_pref.yaml`

Example configuration:

```bash
vi $QFW_TMP_PATH/defw_app_pref.yaml
```

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


