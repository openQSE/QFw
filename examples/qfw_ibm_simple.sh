#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_execution_options "$@"
set -- ${QFW_EXAMPLE_REMAINING_ARGS[@]+"${QFW_EXAMPLE_REMAINING_ARGS[@]}"}

# Variables that can be overriden from command line
QFW_IBM_SHOTS="${QFW_IBM_SHOTS:-64}"
QFW_IBM_DEVICE="${QFW_IBM_DEVICE:-ibm-fake-lagos}"
QFW_IBM_LOCAL_DEVICE_CONFIG="${QFW_IBM_LOCAL_DEVICE_CONFIG:-}"

ibm_shim_local_manifest() {
	local runtime_dir target
	runtime_dir="${QFW_RUN_BASE_DIR:-${TMPDIR:-/tmp}/qfw-runs}/example-runtime"
	mkdir -p "${runtime_dir}"
	target="$(mktemp "${runtime_dir}/qfw-ibm-qpm-shim-services.XXXXXX.yaml")"
	cat > "${target}" <<EOF
services:
  - name: shim_ibm_local_qpm
    module: svc_lib_qpm
    load-modules: svc_lib_qpm,api_launcher
    credential-mode: required
    agent-prefix: qpm_shim
    target: group1-head
    assigned-hosts: group1
    assigned-hosts-env: QFW_QPM_ASSIGNED_HOSTS
    device-id: ${QFW_IBM_DEVICE}
EOF
	printf "%s\n" "${target}"
}

ibm_setup_backend_service() {
	local shim_manifest
	case "${QFW_EXAMPLE_SERVICE_MODE:-local}" in
		local)
			if [ -z "$QFW_IBM_LOCAL_DEVICE_CONFIG" ]; then
				echo "ERROR: variable not set. QFW_IBM_LOCAL_DEVICE_CONFIG=<path-to-device-access-yaml>"
				exit 1
			fi
			# The allocation's reservations (set by the Slurm plugin) name the
			# site QPM, not the local one started below; drop them so the
			# driver reserves through the local QPM.
			unset QFW_RESERVATIONS
			QFW_EXAMPLE_SITE_CONFIG="$(qfw_example_make_site_config "${QFW_IBM_LOCAL_DEVICE_CONFIG}")"
			# Register the manifest here, not inside ibm_shim_local_manifest:
			# that runs in $(...), so an append there would be lost.
			shim_manifest="$(ibm_shim_local_manifest)"
			QFW_EXAMPLE_TEMP_FILES+=("${shim_manifest}")
			QFW_SITE_CONFIG="${QFW_EXAMPLE_SITE_CONFIG}" \
				qfw_example_setup_local_services \
					"${shim_manifest}" \
					shim_ibm_local_qpm
			;;
		site)
			qfw_example_setup_site_services
			;;
		*)
			echo "ERROR: unsupported service mode: ${QFW_EXAMPLE_SERVICE_MODE}" >&2
			return 2
			;;
	esac
}

# This also installs an EXIT trap that automatically calls qfw_example_exit so
# that finish/teardown runs whenever the script exits.
qfw_example_begin "qfw_ibm_simple" "$@"

ibm_setup_backend_service

qfw_example_slurm_driver \
	--backend shim \
	--example ibm-simple \
	--qubits 3 \
	--shots "${QFW_IBM_SHOTS}" \
	--count 1 \
	--operation async_run \
	--target-device "${QFW_IBM_DEVICE}" \
	--nodes 1 \
	--ntasks 1 \
	-- "$(qfw_example_path tests/test_ibm_simple.py)" "${QFW_IBM_SHOTS}" shim
