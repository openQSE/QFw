#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_common_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

usage() {
	cat <<EOF
Usage: ./qfw_pulser.sh [--verbose] [--libs <list>] [--device-id <id>]
                       [--shots N] [--atoms N] [--build-on <device>]

Run one Pulser program on a Pasqal device through the shim QPM, with Pulser's
QPUBackend and qfw_pulser.QFwConnection, through each shim library listed,
and print the results side by side. The application environment needs
pulser-core.

Options:
  --libs <list>       comma-separated shim libraries to run the program
                      through, in order: qrmi (default), qdmi, or both.
  --device-id <id>    the device in qfw_pulser_device_access.yaml (default
                      pasqal-emu-free, Pasqal Cloud's EMU_FREE; pasqal-fresnel
                      is the FRESNEL QPU).
  --shots N           shots per job (default 100).
  --atoms N           atoms in the register (default 4). A device may need
                      more on its layout (42 on FRESNEL's 120 traps); pass that
                      many, since the reservation is made for N.
  --build-on <name>   for an emulator, the real device whose specs the program
                      is built on (default FRESNEL_CAN1, whose 60-trap layout
                      takes any number of atoms).

Environment:
  QFW_PASQAL_PROJECT_ID    Pasqal Cloud project the jobs are billed to.
  QFW_API_KEY              Pasqal Cloud token the QPM service opens the
                           device with.
EOF
}

libs="qrmi"
device_id="pasqal-emu-free"
shots=100
atoms=4
build_on="FRESNEL_CAN1"
while [[ $# -gt 0 ]]; do
	case "$1" in
		-h|--help) usage; exit 0 ;;
		--libs) libs="$2"; shift 2 ;;
		--device-id) device_id="$2"; shift 2 ;;
		--shots) shots="$2"; shift 2 ;;
		--atoms) atoms="$2"; shift 2 ;;
		--build-on) build_on="$2"; shift 2 ;;
		*) echo "ERROR: unknown option '$1'" >&2; usage >&2; exit 1 ;;
	esac
done
IFS=, read -r -a lib_list <<< "${libs}"
for lib in "${lib_list[@]}"; do
	if [[ "${lib}" != "qrmi" && "${lib}" != "qdmi" ]]; then
		echo "ERROR: Pasqal devices run through qrmi or qdmi, got '${lib}'" >&2
		exit 1
	fi
done

pulser_config_for_device() {
	# A copy of the example's device-access config with the project filled
	# in, and a copy of its service manifest bound to the device asked for.
	# Both land beside the generated site config.
	local runtime_dir device_access services
	runtime_dir="${QFW_RUN_BASE_DIR:-${TMPDIR:-/tmp}/qfw-runs}/example-runtime"
	mkdir -p "${runtime_dir}"
	device_access="$(mktemp "${runtime_dir}/pulser-device-access.XXXXXX.yaml")"
	services="$(mktemp "${runtime_dir}/pulser-services.XXXXXX.yaml")"
	sed -e "s|QFW_PASQAL_PROJECT_ID|${QFW_PASQAL_PROJECT_ID:-}|" \
		"$(qfw_example_path qfw_pulser_device_access.yaml)" > "${device_access}"
	sed -E "s|^([[:space:]]*device-id:).*|\1 ${1}|" \
		"$(qfw_example_path qfw_pulser_services.yaml)" > "${services}"
	printf "%s %s\n" "${device_access}" "${services}"
}

echo "Starting QFw Pulser example on ${device_id} with libs=${libs}"
qfw_example_begin "pulser" "$@"
read -r device_access services < <(pulser_config_for_device "${device_id}")
# The two copies go once the services are torn down, which still reads them.
trap 'rc=$?; trap - EXIT; qfw_example_finish "${rc}";
	rm -f "${device_access}" "${services}"; exit "${rc}"' EXIT
QFW_EXAMPLE_SITE_CONFIG="$(qfw_example_make_site_config "${device_access}")"
QFW_SITE_CONFIG="${QFW_EXAMPLE_SITE_CONFIG}" \
	qfw_example_setup_local_services "${services}" shim-pasqal
DEFW_ONLY_LOAD_MODULE=api_qpm_execution,api_qpm_telemetry,api_qpm_control \
	qfw_example_slurm_driver \
		--backend pasqal \
		--example qfw_pulser \
		--qubits "${atoms}" \
		--shots "${shots}" \
		--count "$((2 * ${#lib_list[@]}))" \
		--operation async_run \
		--walltime 3600 \
		--ttl 7200 \
		--nodes 1 \
		--ntasks 1 \
		--target-device "${device_id}" \
		-- "$(qfw_example_path tests/test_pulser.py)" \
		"${libs}" "${shots}" "${atoms}" "${build_on}"

echo "Stopping QFw Pulser example"
