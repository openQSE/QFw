#!/bin/bash

set -euo pipefail

for arg in "$@"; do
	case "${arg}" in
		-h|--help) echo "Check docs/recipes/run-example-ibm-qrmi.md"; exit 0 ;;
		--) break ;;
	esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_execution_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]+"${QFW_EXAMPLE_REMAINING_ARGS[@]}"}"

QFW_SHARED_ROOT="${QFW_SHARED_ROOT:-/workspace/qfw-container-base}"

shots="${1:-64}"
device="${QFW_QSA_DEVICE:-ibm-fake-lagos}"

qfw_example_begin "qsa-qpm-site" "$@"
QFW_EXAMPLE_SITE_CONFIG="$(qfw_example_make_site_config \
	"$(qfw_example_path qfw_shim_device_access.yaml)")"
QFW_SITE_CONFIG="${QFW_EXAMPLE_SITE_CONFIG}" \
    qfw_example_setup_site_services qsa-qpm-site

qfw_example_slurm_driver \
	--backend shim \
	--example qsa-qpm-site \
	--qubits 3 \
	--shots "${shots}" \
	--count 1 \
	--operation async_run \
	--target-device "${device}" \
	--owner "${QFW_QSA_OWNER:-user-a}" \
	--nodes 1 \
	--ntasks 1 \
	-- "$(qfw_example_path tests/test_ibm_qpm.py)" "${shots}" shim
