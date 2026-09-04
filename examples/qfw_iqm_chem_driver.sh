#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_common_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

usage() {
	cat <<EOF
Usage: ./qfw_iqm_chem_driver.sh [--verbose] [options] [chemistry-script.py] [script args...]

Reserve through the QFw Slurm-style driver, then run a QFw-enabled chemistry
application with the driver-provided reservation id.

Options:
  --backend NAME              QPM provider/backend (default: iqm)
  --target-device DEVICE      QFw device id (default: QFW_QPU_DEVICE_ID or ornl-iqm-20q)
  --site-config PATH          Site config for the long-running QPM
  --chem-app-dir DIR          chemistry_example_aim2 checkout
  --run-dir DIR               Evidence and application runtime directory
  --owner USER                Trusted launcher user (default: USER/LOGNAME/root)
  --job-id ID                 Trusted scheduler job id
  --allocation-id ID          Trusted scheduler allocation id
  --scope-id ID               Reservation scope id
  --credential-hint TEXT      Non-secret provider credential selector
  --credential-hint-json JSON Non-secret provider credential selector metadata
  --credential-handle ID      Opaque non-secret provider credential handle
  --credential-scope ID       Optional credential lookup scope
  --nodes N                   Application node count (default: 1)
  --ntasks N                  Application task count (default: 1)
  --nodelist LIST             Application node list
  --het-group N               Heterogeneous allocation group
  --exclusive                 Pass --exclusive to qfw-srun for the app step
  --shots N                   Chemistry reservation shots (default: 128)
  --reservation-qubits N      Reservation qubit count (default: 20)
  --reservation-count N       Quantum task count for reservation (default: 1)
  --reservation-walltime-s N  Reservation walltime seconds (default: 300)
  --reservation-ttl-s N       Reservation TTL seconds (default: 600)
  --estimator-precision VAL   Estimator precision argument
  --preflight-only            Check owner credentials and exit
  --dry-run                   Generate commands without running QFw
  -h, --help                  Show this help
EOF
}

backend="${QFW_CHEM_BACKEND:-iqm}"
target_device="${QFW_QPU_DEVICE_ID:-ornl-iqm-20q}"
site_config="${QFW_SITE_CONFIG:-}"
device_access_config=""
chem_app_dir="${QFW_CHEM_APP_DIR:-}"
run_dir="${QFW_CHEM_DRIVER_RUN_DIR:-}"
owner="${QFW_CHEM_OWNER:-${USER:-${LOGNAME:-root}}}"
job_id="${QFW_CHEM_JOB_ID:-}"
allocation_id="${QFW_CHEM_ALLOCATION_ID:-}"
scope_id="${QFW_CHEM_SCOPE_ID:-}"
credential_hint="${QFW_CHEM_CREDENTIAL_HINT:-}"
credential_hint_json="${QFW_CHEM_CREDENTIAL_HINT_JSON:-}"
credential_handle="${QFW_CHEM_CREDENTIAL_HANDLE:-}"
credential_scope="${QFW_CHEM_CREDENTIAL_SCOPE:-}"
nodes="${QFW_CHEM_SLURM_NODES:-1}"
ntasks="${QFW_CHEM_SLURM_NTASKS:-1}"
nodelist="${QFW_CHEM_SLURM_NODELIST:-}"
het_group="${QFW_CHEM_HET_GROUP:-}"
exclusive="${QFW_CHEM_EXCLUSIVE:-no}"
shots="${QFW_CHEM_SHOTS:-128}"
reservation_qubits="${QFW_CHEM_RESERVATION_QUBITS:-20}"
reservation_count="${QFW_CHEM_RESERVATION_COUNT:-1}"
reservation_walltime_s="${QFW_CHEM_RESERVATION_WALLTIME_S:-300}"
reservation_ttl_s="${QFW_CHEM_RESERVATION_TTL_S:-600}"
estimator_precision="${QFW_CHEM_ESTIMATOR_PRECISION:-}"
preflight_only="no"
dry_run="no"
chem_script="example_1_He_from_pyscf.py"
chem_extra_args=()

qfw_chem_driver_need_value() {
	local option="${1:-option}"
	if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" == --* ]]; then
		echo "ERROR: ${option} requires a value" >&2
		exit 2
	fi
}

qfw_chem_driver_bool_enabled() {
	case "${1:-}" in
		1|yes|true|on|y|YES|TRUE|ON|Y) return 0 ;;
		*) return 1 ;;
	esac
}

qfw_chem_driver_require_positive_int() {
	local name="$1"
	local value="$2"
	if ! [[ "${value}" =~ ^[0-9]+$ ]] || [[ "${value}" -le 0 ]]; then
		echo "ERROR: ${name} must be a positive integer: ${value}" >&2
		exit 2
	fi
}

qfw_chem_driver_device_access_config() {
	python3 - "${site_config}" <<'PY'
import sys
from pathlib import Path

from qfw_runtime.config import resolve_path

try:
	import yaml
except ImportError as exc:
	raise SystemExit(
		f"ERROR: PyYAML is required to read the site configuration: {exc}")

site_path = Path(sys.argv[1]).expanduser().resolve()
with site_path.open("r", encoding="utf-8") as stream:
	site = yaml.safe_load(stream) or {}
value = (site.get("service") or {}).get("device-access-config")
if not value:
	raise SystemExit("site.yaml does not define service.device-access-config")
path = resolve_path(value, base=site_path.parent)
if not path.is_file():
	raise SystemExit(f"device access config is not readable: {path}")
print(path)
PY
}

qfw_chem_driver_preflight_owner() {
	local config_path="$1"
	python3 - "${config_path}" "${owner}" "${backend}" "${target_device}" \
		"${credential_hint}" "${credential_hint_json}" \
		"${credential_handle}" <<'PY'
import json
import os
import sys

try:
	import yaml
except ImportError as exc:
	raise SystemExit(
		f"ERROR: PyYAML is required for credential preflight: {exc}")

(
	config_path,
	owner,
	provider,
	target_device,
	credential_hint,
	credential_hint_json,
	credential_handle,
) = sys.argv[1:]


def resolve_relative(path, base_path):
	if os.path.isabs(path):
		return path
	return os.path.join(os.path.dirname(os.path.abspath(base_path)), path)


def load_json(path):
	with open(path, "r", encoding="utf-8") as handle:
		return json.load(handle)


def load_yaml(path):
	with open(path, "r", encoding="utf-8") as handle:
		return yaml.safe_load(handle) or {}


def select_device(config):
	qpus = config.get("qpus")
	if not isinstance(qpus, dict):
		raise ValueError("device access config does not define qpus")
	if target_device in qpus and isinstance(qpus[target_device], dict):
		return target_device, qpus[target_device]
	matches = []
	for qpu_id, record in qpus.items():
		if not isinstance(record, dict):
			continue
		provider_device_id = record.get("provider-device-id")
		aliases = record.get("aliases") or []
		if isinstance(aliases, str):
			aliases = [aliases]
		names = {str(qpu_id)}
		if provider_device_id:
			names.add(str(provider_device_id))
		names.update(str(item) for item in aliases)
		if target_device and target_device in names:
			return qpu_id, record
		if provider and str(record.get("provider", "")).lower() == provider.lower():
			matches.append((qpu_id, record))
	if len(matches) == 1:
		return matches[0]
	raise ValueError(f"device {target_device!r} was not found")


def hint_user():
	if credential_hint_json:
		parsed = json.loads(credential_hint_json)
		if isinstance(parsed, dict):
			return (
				parsed.get("user")
				or parsed.get("user_record")
				or parsed.get("credential_user")
				or parsed.get("record"))
	return None


def credential_handle_user(users):
	if not credential_handle:
		return None
	handles = users.get("handles")
	if not isinstance(handles, dict):
		return None
	entry = handles.get(str(credential_handle))
	if isinstance(entry, str):
		return entry
	if isinstance(entry, dict):
		return (
			entry.get("user")
			or entry.get("user_record")
			or entry.get("credential_user"))
	return None


def api_key_present(record, device_id, provider_device_id):
	if isinstance(record, str):
		return bool(record.strip())
	if not isinstance(record, dict):
		return False
	devices = record.get("devices")
	if isinstance(devices, dict):
		for key in (device_id, provider_device_id):
			if not key or key not in devices:
				continue
			value = devices[key]
			if isinstance(value, str):
				return bool(value.strip())
			if isinstance(value, dict):
				return bool(value.get("api_key"))
	value = record.get("api_key")
	return bool(value)


try:
	config = load_yaml(config_path)
	device_id, device = select_device(config)
	credential_db = device.get("credential-db")
	if not credential_db:
		raise ValueError(f"device {device_id!r} does not define credential-db")
	credential_db_path = resolve_relative(str(credential_db), config_path)
	db = load_json(credential_db_path)
	users = db.get("users", db)
	if not isinstance(users, dict):
		raise ValueError("credential DB users entry is invalid")
	provider_device_id = device.get("provider-device-id")
	candidates = []
	for value in (
			credential_handle_user(users),
			hint_user(),
			credential_handle,
			credential_hint,
			owner):
		if value is None:
			continue
		value = str(value).strip()
		if value and value not in candidates:
			candidates.append(value)
	for candidate in candidates:
		record = users.get(candidate)
		if record is None:
			continue
		if api_key_present(record, device_id, provider_device_id):
			print("QFW_CHEM_CREDENTIAL_PREFLIGHT " + json.dumps({
				"schema": "qfw-chem-credential-preflight-v1",
				"status": "ok",
				"owner": owner,
				"credential_user": candidate,
				"device_id": device_id,
				"provider_device_id": provider_device_id,
				"config": config_path,
				"credential_db": credential_db_path,
			}, indent=2, sort_keys=True))
			raise SystemExit(0)
	raise ValueError(
		f"credential DB does not contain an API key for owner {owner!r} "
		f"and device {device_id!r}")
except Exception as exc:
	print("QFW_CHEM_CREDENTIAL_PREFLIGHT " + json.dumps({
		"schema": "qfw-chem-credential-preflight-v1",
		"status": "error",
		"owner": owner,
		"target_device_id": target_device,
		"config": config_path,
		"error": str(exc),
	}, indent=2, sort_keys=True))
	raise SystemExit(1)
PY
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--backend)
			qfw_chem_driver_need_value "$@"
			backend="$2"
			shift 2
			;;
		--target-device)
			qfw_chem_driver_need_value "$@"
			target_device="$2"
			shift 2
			;;
		--site-config)
			qfw_chem_driver_need_value "$@"
			site_config="$2"
			shift 2
			;;
		--chem-app-dir)
			qfw_chem_driver_need_value "$@"
			chem_app_dir="$2"
			shift 2
			;;
		--run-dir)
			qfw_chem_driver_need_value "$@"
			run_dir="$2"
			shift 2
			;;
		--owner)
			qfw_chem_driver_need_value "$@"
			owner="$2"
			shift 2
			;;
		--job-id)
			qfw_chem_driver_need_value "$@"
			job_id="$2"
			shift 2
			;;
		--allocation-id)
			qfw_chem_driver_need_value "$@"
			allocation_id="$2"
			shift 2
			;;
		--scope-id)
			qfw_chem_driver_need_value "$@"
			scope_id="$2"
			shift 2
			;;
		--credential-hint)
			qfw_chem_driver_need_value "$@"
			credential_hint="$2"
			shift 2
			;;
		--credential-hint-json)
			qfw_chem_driver_need_value "$@"
			credential_hint_json="$2"
			shift 2
			;;
		--credential-handle)
			qfw_chem_driver_need_value "$@"
			credential_handle="$2"
			shift 2
			;;
		--credential-scope)
			qfw_chem_driver_need_value "$@"
			credential_scope="$2"
			shift 2
			;;
		--nodes)
			qfw_chem_driver_need_value "$@"
			nodes="$2"
			shift 2
			;;
		--ntasks)
			qfw_chem_driver_need_value "$@"
			ntasks="$2"
			shift 2
			;;
		--nodelist)
			qfw_chem_driver_need_value "$@"
			nodelist="$2"
			shift 2
			;;
		--het-group)
			qfw_chem_driver_need_value "$@"
			het_group="$2"
			shift 2
			;;
		--exclusive)
			exclusive="yes"
			shift
			;;
		--shots)
			qfw_chem_driver_need_value "$@"
			shots="$2"
			shift 2
			;;
		--reservation-qubits)
			qfw_chem_driver_need_value "$@"
			reservation_qubits="$2"
			shift 2
			;;
		--reservation-count)
			qfw_chem_driver_need_value "$@"
			reservation_count="$2"
			shift 2
			;;
		--reservation-walltime-s)
			qfw_chem_driver_need_value "$@"
			reservation_walltime_s="$2"
			shift 2
			;;
		--reservation-ttl-s)
			qfw_chem_driver_need_value "$@"
			reservation_ttl_s="$2"
			shift 2
			;;
		--estimator-precision)
			qfw_chem_driver_need_value "$@"
			estimator_precision="$2"
			shift 2
			;;
		--preflight-only)
			preflight_only="yes"
			shift
			;;
		--dry-run)
			dry_run="yes"
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		--)
			shift
			chem_extra_args+=("$@")
			break
			;;
		--*)
			echo "ERROR: unknown option: $1" >&2
			usage >&2
			exit 2
			;;
		*)
			chem_script="$1"
			shift
			chem_extra_args+=("$@")
			break
			;;
	esac
done

qfw_chem_driver_require_positive_int "--nodes" "${nodes}"
qfw_chem_driver_require_positive_int "--ntasks" "${ntasks}"
qfw_chem_driver_require_positive_int "--shots" "${shots}"
qfw_chem_driver_require_positive_int "--reservation-qubits" \
	"${reservation_qubits}"
qfw_chem_driver_require_positive_int "--reservation-count" \
	"${reservation_count}"
qfw_chem_driver_require_positive_int "--reservation-walltime-s" \
	"${reservation_walltime_s}"
qfw_chem_driver_require_positive_int "--reservation-ttl-s" \
	"${reservation_ttl_s}"

if [[ -z "${chem_app_dir}" ]]; then
	for candidate in \
		"/workspace/qfw-container-base/chemistry_example_aim2" \
		"${script_dir}/../../chemistry_example_aim2"; do
		if [[ -d "${candidate}" ]]; then
			chem_app_dir="$(cd "${candidate}" && pwd)"
			break
		fi
	done
fi
if [[ -z "${chem_app_dir}" || ! -d "${chem_app_dir}" ]]; then
	echo "ERROR: chemistry app directory not found; use --chem-app-dir" >&2
	exit 1
fi

case "${chem_script}" in
	/*) chem_script_path="${chem_script}" ;;
	*)  chem_script_path="${chem_app_dir%/}/${chem_script#./}" ;;
esac
if [[ ! -r "${chem_script_path}" ]]; then
	echo "ERROR: chemistry app script not readable: ${chem_script_path}" >&2
	exit 1
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
if [[ -z "${run_dir}" ]]; then
	run_dir="${QFW_RUN_BASE_DIR:-${TMPDIR:-/tmp}/qfw-runs}/chem-driver-${backend}-${timestamp}-$$"
fi
mkdir -p "${run_dir}/config" "${run_dir}/logs"

if [[ -z "${site_config}" ]] &&
   ! qfw_chem_driver_bool_enabled "${dry_run}" &&
   ! qfw_chem_driver_bool_enabled "${preflight_only}"; then
	echo "ERROR: --site-config is required" >&2
	exit 2
fi

if qfw_chem_driver_bool_enabled "${preflight_only}"; then
	device_access_config="$(
		qfw_chem_driver_device_access_config
	)" || {
		echo "ERROR: site device-access configuration is required for owner preflight" \
			>&2
		exit 2
	}
fi

launcher="${run_dir}/config/qfw-chem-with-reservation.py"
app_run_dir="${run_dir}/runtime"
driver_result="${run_dir}/driver.jsonl"
example_result="${run_dir}/chemistry-example.jsonl"
stdout_log="${run_dir}/logs/qfw-iqm-chem-driver.stdout.log"
stderr_log="${run_dir}/logs/qfw-iqm-chem-driver.stderr.log"

launcher_args=(
	"--qfw"
	"--backend" "${backend}"
	"--reservation-shots" "${shots}"
	"--reservation-qubits" "${reservation_qubits}"
	"--reservation-walltime-s" "${reservation_walltime_s}"
	"--reservation-ttl-s" "${reservation_ttl_s}"
)
if [[ -n "${estimator_precision}" ]]; then
	launcher_args+=("--estimator-precision" "${estimator_precision}")
fi
launcher_args+=("${chem_extra_args[@]}")
python3 - "${launcher}" "${chem_script_path}" "${launcher_args[@]}" <<'PY'
import json
import os
import stat
import sys

launcher = sys.argv[1]
script_path = sys.argv[2]
script_args = sys.argv[3:]
script = f"""#!/usr/bin/env python3
import os
import runpy
import sys

for stream in (sys.stdout, sys.stderr):
\treconfigure = getattr(stream, "reconfigure", None)
\tif reconfigure is not None:
\t\treconfigure(line_buffering=True)

from qfw_qiskit.reservation_set import (
\tparse_qfw_reservations,
\tselect_qpm_reservation,
)

reservation_id = select_qpm_reservation(
\tparse_qfw_reservations()).reservation_id

script_path = {json.dumps(script_path)}
script_args = {json.dumps(script_args)}
script_dir = os.path.dirname(os.path.abspath(script_path))
if script_dir not in sys.path:
\tsys.path.insert(0, script_dir)
sys.argv = [script_path] + script_args + ["--reservation-id", reservation_id]
runpy.run_path(script_path, run_name="__main__")
"""
with open(launcher, "w", encoding="utf-8") as stream:
	stream.write(script)
mode = os.stat(launcher).st_mode
os.chmod(launcher, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
PY
chmod +x "${launcher}"

driver_args=(
	--run-dir "${app_run_dir}"
	--nodes "${nodes}"
	--ntasks "${ntasks}"
	--backend "${backend}"
	--example qfw-iqm-chemistry
	--qubits "${reservation_qubits}"
	--shots "${shots}"
	--count "${reservation_count}"
	--operation async_run
	--walltime "${reservation_walltime_s}"
	--ttl "${reservation_ttl_s}"
	--target-device "${target_device}"
	--owner "${owner}"
	--workload-kind hybrid
	--workload-json '{"application":"chemistry_example_aim2","frontend":"qfw_estimator"}'
)
if [[ -n "${job_id}" ]]; then
	driver_args+=(--job-id "${job_id}")
fi
if [[ -n "${allocation_id}" ]]; then
	driver_args+=(--allocation-id "${allocation_id}")
fi
if [[ -n "${scope_id}" ]]; then
	driver_args+=(--scope-id "${scope_id}")
fi
if [[ -n "${credential_hint}" ]]; then
	driver_args+=(--credential-hint "${credential_hint}")
fi
if [[ -n "${credential_hint_json}" ]]; then
	driver_args+=(--credential-hint-json "${credential_hint_json}")
fi
if [[ -n "${credential_handle}" ]]; then
	driver_args+=(--credential-handle "${credential_handle}")
fi
if [[ -n "${credential_scope}" ]]; then
	driver_args+=(--credential-scope "${credential_scope}")
fi
if [[ -n "${nodelist}" ]]; then
	driver_args+=(--nodelist "${nodelist}")
fi
if [[ -n "${het_group}" ]]; then
	driver_args+=(--het-group "${het_group}")
fi
if qfw_chem_driver_bool_enabled "${exclusive}"; then
	driver_args+=(--exclusive)
fi

echo "Run directory: ${run_dir}"
echo "Backend: ${backend}"
echo "Target device: ${target_device}"
echo "Owner: ${owner}"
if [[ -n "${device_access_config}" ]]; then
	echo "Device access config: ${device_access_config}"
fi
echo "Chemistry script: ${chem_script_path}"
echo "Launcher: ${launcher}"

if qfw_chem_driver_bool_enabled "${preflight_only}"; then
	qfw_chem_driver_preflight_owner "${device_access_config}"
	exit 0
fi

if qfw_chem_driver_bool_enabled "${dry_run}"; then
	printf "DRY RUN qfw-setup --site-config %q --run-dir %q\n" \
		"${site_config}" "${app_run_dir}"
	printf "DRY RUN QFW_EXAMPLE_RESULT_FILE=%q QFW_SLURM_DRIVER_RESULT_FILE=%q %q" \
		"${example_result}" "${driver_result}" \
		"$(qfw_example_path qfw_slurm_driver.sh)"
	printf " %q" "${driver_args[@]}"
	printf " -- %q\n" "${launcher}"
	exit 0
fi

qfw_example_require_runtime
qfw-setup \
	--site-config "${site_config}" \
	--run-dir "${app_run_dir}"

driver_rc=0
set +e
QFW_EXAMPLE_RESULT_FILE="${example_result}" \
QFW_SLURM_DRIVER_RESULT_FILE="${driver_result}" \
	"$(qfw_example_path qfw_slurm_driver.sh)" \
		"${driver_args[@]}" \
		-- "${launcher}" \
	>"${stdout_log}" 2>"${stderr_log}"
driver_rc=$?
qfw-teardown --run-dir "${app_run_dir}" \
	>>"${stdout_log}" 2>>"${stderr_log}"
teardown_rc=$?
set -e

echo "Driver stdout: ${stdout_log}"
echo "Driver stderr: ${stderr_log}"
echo "Driver result: ${driver_result}"
echo "Chemistry result: ${example_result}"

if [[ "${driver_rc}" -ne 0 ]]; then
	exit "${driver_rc}"
fi
exit "${teardown_rc}"
