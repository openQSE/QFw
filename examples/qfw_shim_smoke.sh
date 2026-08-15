#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

usage() {
	cat <<EOF
Usage: ./qfw_shim_smoke.sh --lib <qrmi|qdmi> [--call <api>] [test args...]
       ./qfw_shim_smoke.sh --libs <qdmi,qrmi> [--call <api>] [test args...]

Run the QRMI/QDMI shim smoke test. Provide either:
  --lib  <qrmi|qdmi>   exercise a single shim library path, or
  --libs <qdmi,qrmi>   run each introspection call through the listed
                       libraries in order, for a side-by-side comparison.

Optional:
  --call <api>          run one QPM API instead of the default smoke sequence.
                       Useful calls include test, get_backend_info,
                       get_device_info, get_coupling_graph,
                       get_calibration_snapshot, async_run, and
                       get_task_metadata.

The server validates whether the selected library supports each requested API.
EOF
}

lib=""
libs=""
shots=100
device_id="ornl-iqm-20q"
capture=""
for arg in "$@"; do
	case "${capture}" in
		lib)  lib="${arg}";  capture=""; continue ;;
		libs) libs="${arg}"; capture=""; continue ;;
		shots) shots="${arg}"; capture=""; continue ;;
		device_id) device_id="${arg}"; capture=""; continue ;;
	esac
	case "${arg}" in
		--lib)  capture="lib" ;;
		--libs) capture="libs" ;;
		--shots) capture="shots" ;;
		--device-id) capture="device_id" ;;
	esac
done

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
	usage
	exit 0
fi

if [[ -z "${lib}" && -z "${libs}" ]]; then
	usage >&2
	exit 1
fi

if [[ -n "${lib}" && "${lib}" != "qrmi" && "${lib}" != "qdmi" ]]; then
	echo "ERROR: --lib must be qrmi or qdmi, got '${lib}'" >&2
	exit 1
fi

echo "Starting QFw shim smoke test with ${libs:+libs=${libs} }${lib:+lib=${lib}}"
qfw_example_begin "shim-smoke" "$@"
QFW_EXAMPLE_SITE_CONFIG="$(qfw_example_make_site_config \
	"$(qfw_example_path qfw_shim_device_access.yaml)")"
QFW_SITE_CONFIG="${QFW_EXAMPLE_SITE_CONFIG}" \
	qfw_example_setup_local_services qfw_shim_smoke_services.yaml \
		shim-ornl-20q
DEFW_ONLY_LOAD_MODULE=api_qpm_execution,api_qpm_telemetry,api_qpm_control \
	QFW_SHIM_SMOKE_SHUTDOWN_QPM=no \
	qfw_example_slurm_driver \
		--backend shim \
		--example qfw_shim_smoke \
		--qubits 1 \
		--shots "${shots}" \
		--count 1 \
		--operation async_run \
		--nodes 1 \
		--ntasks 1 \
		--target-device "${device_id}" \
		-- "$(qfw_example_path tests/test_shim_smoke.py)" "$@"

echo "Stopping QFw shim smoke test"
