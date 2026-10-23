#!/bin/bash

# IBM QSA test using a QPMd started locally.
# Credential required

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_execution_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]+"${QFW_EXAMPLE_REMAINING_ARGS[@]}"}"

QFW_SHARED_ROOT="${QFW_SHARED_ROOT:-/workspace/qfw-container-base}"

if [ $# != 1 ]; then
	echo "Usage: $0 <path-to-device-access-yaml>"
	exit 1
fi

device_config="$1"
shots="${QFW_QSA_SHOTS:-128}"
device="${QFW_QSA_DEVICE:-ibm-fake-lagos}"

qsa_shim_manifest() {
	local runtime_dir target
	runtime_dir="${QFW_RUN_BASE_DIR:-${TMPDIR:-/tmp}/qfw-runs}/example-runtime"
	mkdir -p "${runtime_dir}"
	target="$(mktemp "${runtime_dir}/qsa-shim-services.XXXXXX.yaml")"
	cat > "${target}" <<EOF
services:
  - name: shim-qsa
    module: svc_lib_qpm
    load-modules: svc_lib_qpm,api_launcher
    credential-mode: required
    agent-prefix: qpm_shim
    target: group1-head
    assigned-hosts: group1
    assigned-hosts-env: QFW_QPM_ASSIGNED_HOSTS
    device-id: ${device}
EOF
	printf "%s\n" "${target}"
}

qfw_example_begin "qsa-qpm-local" "$@"
QFW_EXAMPLE_SITE_CONFIG="$(qfw_example_make_site_config ${device_config})"
QFW_SITE_CONFIG="${QFW_EXAMPLE_SITE_CONFIG}" \
	qfw_example_setup_local_services \
		"$(qsa_shim_manifest)" \
		shim-qsa

qfw_example_slurm_driver \
	--backend shim \
	--example qsa-qpm-local \
	--qubits 3 \
	--shots "${shots}" \
	--count 1 \
	--operation async_run \
	--target-device "${device}" \
	--owner "${QFW_QSA_OWNER:-user-a}" \
	--nodes 1 \
	--ntasks 1 \
	-- "$(qfw_example_path tests/test_ibm_qpm.py)" "${shots}" shim
