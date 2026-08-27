# Optional simulator dependencies

These source-tree tools build the simulator executables consumed by QFw QPM
services. They are intentionally independent of the QFw CMake installation and
are not copied into a QFw install prefix.

The caller owns the dependency source/build workspace and install prefix. A
site normally runs these tools while constructing a software image or module,
then provides an environment module named by the QFw service manifest.

```bash
tools/dependencies/nwqsim/build.sh \
  --work-dir /tmp/qfw-dependencies/nwqsim \
  --prefix /opt/openqse/nwqsim

tools/dependencies/tnqvm/build.sh \
  --work-dir /tmp/qfw-dependencies/tnqvm \
  --prefix /opt/openqse/tnqvm \
  --mpi-prefix /opt/openmpi
```

TNQVM accepts `--rocm auto`, `--rocm on`, or `--rocm off`. Automatic mode
selects ROCm only when `hipcc`, HIP headers, and hipBLAS are all available.
Common compatibility patches are applied in both modes. The CPU and ROCm
paths then apply their own previously validated patch sets.
