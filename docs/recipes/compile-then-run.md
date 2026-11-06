# Compile on CPU Nodes, Then Run on the QPU

Compiling a large circuit inside a `--qpu` job holds the QPU reservation while
the CPUs work. This recipe compiles in a CPU-only job from a saved device
snapshot, then runs the result in a job that reserves the QPU only once the
compile has finished. The QPM runs the compiled circuit as it stands, or
refuses it; it does not compile it again.

Run `man 1 qfw-compile` for the compile command. The device must take circuits
compiled ahead of time; IBM and IQM devices through the shim do.

## 1. Take a device snapshot

In a job that reaches the QPM, write the device's records to a shared path:

```python
# snapshot.py
import json
import sys

from qfw_qiskit import QFwBackend

with open(sys.argv[2], "w") as stream:
	json.dump(QFwBackend(provider=sys.argv[1]).device_snapshot(), stream)
```

```bash
qfw-setup
qfw-srun snapshot.py <backend> "${SHARED}/device.json"
qfw-teardown
```

A snapshot holds the calibration it was taken at. Take a new one after the
device recalibrates.

## 2. Compile in a CPU job

`qfw-compile` needs no QPM, reservation, or `qfw-setup`:

```bash
# compile.sh
#!/bin/bash
source "${QFW_INSTALL_PREFIX}/bin/qfw-activate" --venv "${QFW_VENV}"
qfw-compile --snapshot "${SHARED}/device.json" \
  --in "${SHARED}/circuits.qpy" --out "${SHARED}/compiled.qpy"
```

## 3. Run once the compile has finished

```python
# run.py
import sys

from qiskit import qpy
from qfw_qiskit import QFwBackend
from qfw_example_context import qfw_reservation_options

backend = QFwBackend(provider=sys.argv[2])
with open(sys.argv[1], "rb") as stream:
	circuits = qpy.load(stream)
backend.check_isa(circuits)
print(backend.run(circuits, **qfw_reservation_options()).result().get_counts())
```

`check_isa` refuses a circuit the QPM would refuse, such as one compiled for a
calibration the device has left, before it waits in the QPU queue.

```bash
# run.sh
#!/bin/bash
source "${QFW_INSTALL_PREFIX}/bin/qfw-activate" --venv "${QFW_VENV}"
export PYTHONPATH="${QFW_SHARE_DIR}/examples/tests:${PYTHONPATH}"
qfw-setup
qfw-srun run.py "${SHARED}/compiled.qpy" <backend>
qfw-teardown
```

```bash
compiled=$(sbatch --parsable --partition=cpu compile.sh)
sbatch --dependency=afterok:"${compiled}" --qpu=<name> run.sh
```

`--qpu` reserves the QPU before the batch script starts, so compiling inside
`run.sh` would hold the reservation again.
