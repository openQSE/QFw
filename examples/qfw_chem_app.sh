#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "chemistry" "$@"

usage() {
	cat <<EOF
Usage: ./qfw_chem_app.sh [--backend <nwqsim|tnqvm|iqm>] [--chem-app-dir <dir>] <script.py> [script args...]

Environment overrides:
  QFW_CHEM_BACKEND=<provider>     Default backend provider. Defaults to nwqsim.
  QFW_CHEM_APP_DIR=<dir>          Chemistry app directory.
  QFW_CHEM_SETUP_MODE=<local|site>
                                  local starts a job-owned backend service;
                                  site uses the active site runtime. Defaults
                                  to local.
EOF
}

backend="${QFW_CHEM_BACKEND:-nwqsim}"
chem_app_dir="${QFW_CHEM_APP_DIR:-}"
setup_mode="${QFW_CHEM_SETUP_MODE:-local}"

while [[ $# -gt 0 ]]; do
	case "$1" in
		--backend)
			backend="$2"
			shift 2
			;;
		--chem-app-dir)
			chem_app_dir="$2"
			shift 2
			;;
		--setup-mode)
			setup_mode="$2"
			shift 2
			;;
		-h|--help)
			usage
			exit 0
			;;
		--)
			shift
			break
			;;
		*)
			break
			;;
	esac
done

if [[ $# -lt 1 ]]; then
	usage >&2
	exit 2
fi

chem_script="$1"
shift

if [[ -z "${chem_app_dir}" ]]; then
	if [[ -d "${script_dir}/../../chemistry_example_aim2" ]]; then
		chem_app_dir="$(cd "${script_dir}/../../chemistry_example_aim2" && pwd)"
	elif [[ -d "$(qfw_example_path "tests/chemistry_example_aim2")" ]]; then
		chem_app_dir="$(qfw_example_path "tests/chemistry_example_aim2")"
	else
		echo "ERROR: chemistry app directory not found; set QFW_CHEM_APP_DIR" >&2
		exit 1
	fi
fi

case "${chem_script}" in
	/*) chem_script_path="${chem_script}" ;;
	*)  chem_script_path="${chem_app_dir%/}/${chem_script#./}" ;;
esac

if [[ ! -r "${chem_script_path}" ]]; then
	echo "ERROR: chemistry app script not readable: ${chem_script_path}" >&2
	exit 1
fi

case "${setup_mode}" in
	local)
		qfw_example_setup_backend_service "${backend}"
		;;
	site)
		qfw_example_setup
		;;
	*)
		echo "ERROR: unsupported QFW_CHEM_SETUP_MODE '${setup_mode}'" >&2
		exit 2
		;;
esac

qfw_example_srun "${chem_script_path}" --qfw --backend "${backend}" "$@"
