import argparse
import os
import sys
import logging

import defw  # noqa: F401
from svc_lib_qpm.descriptor import resolve_descriptor
from svc_lib_qpm.frontend import Frontend
from svc_lib_qpm.drivers import QrmiDriver
from qiskit.circuit import QuantumCircuit, ParameterVector
from util.circuit_payload import dump_qpy
from types import SimpleNamespace

# Set logger to stdout. QFw and QRMI DEBUG messages appear in the terminal.
_fmt = logging.Formatter('{asctime} - {name} - [{levelname}] - {filename}:{funcName}() -> {message}', style='{')
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(_fmt)
logger = logging.getLogger()
logger.handlers.clear()          # drop the file-only handler defw registered
logger.addHandler(_stderr_handler)
logger.setLevel(logging.INFO)

parser = argparse.ArgumentParser(description="Test the QFw frontend with an IBM Quantum System device")
parser.add_argument("device", nargs="?", default="ibm-fake-lagos",
                    help="device name in the device access config (default: %(default)s)")
args = parser.parse_args()

# Set QRMI driver preference and descriptor
os.environ["QFW_QPU_IFACE_PREF"] = "qrmi"
desc = resolve_descriptor(args.device)
fe = Frontend([QrmiDriver(desc)], desc)

assert desc.get("resource_type") == "IBMQuantumSystem"
assert fe.route("get_device_info").name == "qrmi"
assert fe.route("get_coupling_graph").name == "qrmi"
assert fe.route("get_calibration_snapshot").name == "qrmi"
assert fe.route("get_dynamic_backend_info").name == "qrmi"
assert fe.route("get_backend_info").name == "qrmi"
assert fe.route("run_circuit").name == "qrmi"
assert fe.route("get_task_timing").name == "qrmi"
assert fe.route("get_task_metadata").name == "qrmi"
logger.info("All driver capabilities successfully routed to qrmi")

logger.info("Call get_device_info")
fe.get_device_info()
logger.info("Call get_coupling_graph")
fe.get_coupling_graph()
logger.info("Call get_calibration_snapshot")
fe.get_calibration_snapshot()
logger.info("Call get_dynamic_backend_info")
dyn = fe.get_dynamic_backend_info()
logger.info(f"get_dynamic_backend_info = {dyn}")
logger.info("Call get_backend_info")
fe.get_backend_info()

###############################################
# Create QuantumCircuit with three parameters #
# and then serialize, compress and encode it  #
###############################################

# 3-qubit parameterized circuit using ParameterVector
th = ParameterVector('th', 3)
qc = QuantumCircuit(3, 3)
qc.ry(th[0], 0)
qc.ry(th[1], 1)
qc.ry(th[2], 2)
qc.cx(0, 1)
qc.cx(1, 2)
qc.measure([0, 1, 2], [0, 1, 2])

param_values = {
  str(th[0]): 0.1,
  str(th[1]): 0.2,
  str(th[2]): 0.3
}

###########################################################
# Create the info structure. All fields are required for  #
# compiling and running the circuit.                      #
###########################################################

# compilation_options: match the qiskit's generate_preset_pass_manager arguments, but
# the backend and target arguments will be ovewritten by QFw to make sure they match
# the provider-device-id in the descriptor (device_access.yaml)
info = {
    "circuit": {
        "data": dump_qpy(qc, compress=True),
        "format": "qpy+gzip",
    },
    "param_values": param_values,
    "compilation_options": {"optimization_level": 1},
    "shots": 128,
    "cid": "my_uuid",
}
payload = SimpleNamespace(info=info)
logger.info(f"info: {info}")

# Call run_circuit to run the payload without explicit acquire
# as it is not needed for IBM Quantum System
logger.info("Call run_circuit")
fe.run_circuit(payload)
logger.info("Call get_task_timing")
timing = fe.get_task_timing(info["cid"])
logger.info(f"get_task_timing = {timing}")
logger.info("Call get_task_metadata")
meta = fe.get_task_metadata(info["cid"])
logger.info(f"get_task_metadata = {meta}")
