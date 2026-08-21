#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_common_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

usage() {
	cat <<EOF
Usage: ./qfw_slurm_driver.sh [--verbose] [driver options] -- <application> [args...]

Reserve QPM capacity, launch an application with the reservation context, and
release the reservation. This script is the example stand-in for the future
Slurm/SPANK integration and works with local and site QFw runtimes.

Required driver options:
  --backend NAME          QPM provider/backend selector
  --example NAME          Workload/example name
  --qubits N              Maximum qubit count for the reservation

Common driver options:
  --shots N               Shot count for reservation metadata (default: 1024)
  --count N               Number of quantum tasks reserved (default: 1)
  --depth N               Circuit depth for admission metadata (default: 1)
  --one-q-gates N         One-qubit gate count for admission metadata
  --two-q-gates N         Two-qubit gate count for admission metadata
  --measurements N        Measured qubit count (default: --qubits)
  --workload-kind NAME    Workload kind, for example quantum or hybrid
  --operation NAME        Operation name, for example async_run or sync_run
  --walltime SEC          Reservation walltime seconds (default: 300)
  --ttl SEC               Reservation TTL seconds (default: 600)
  --timeout SEC           QPM resolution timeout (default: 40)
  --target-device ID      Optional target device id
  --scope-id ID           Optional reservation scope id
  --owner USER            Trusted launcher user for the reservation
  --job-id ID             Trusted scheduler job id
  --allocation-id ID      Trusted scheduler allocation id
  --credential-hint TEXT  Non-secret provider credential selector
  --credential-hint-json JSON
                          Non-secret provider credential selector metadata
  --credential-handle ID  Opaque non-secret provider credential handle
  --credential-scope ID   Optional credential lookup scope
  --analytics-json JSON   Optional JSON object with descriptive run metadata
  --parameters-json JSON  Optional JSON object merged into request parameters
  --workload-json JSON    Optional JSON object merged into request workload
  --run-context-json JSON Optional JSON object merged into request run_context
  --task-class-json JSON  Optional JSON object merged into request task_class

qfw-srun launch options:
  --run-dir DIR           QFw runtime directory for reserve, app, and release
  --nodes N               Application node count
  --ntasks N              Application task count
  --nodelist LIST         Application node list
  --het-group N           Heterogeneous allocation group for app/control steps
  --exclusive             Pass --exclusive to the application qfw-srun step

The reservation and release control-plane calls always run as one task. When
--nodelist or --het-group is supplied, the control-plane steps use the same
placement so local and site workflows behave consistently.
EOF
}

qfw_slurm_need_value() {
	local option="${1:-option}"
	if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" == --* ]]; then
		echo "ERROR: ${option} requires a value" >&2
		exit 2
	fi
}

qfw_slurm_emit() {
	local event="$1"
	local status="$2"
	local rc="$3"
	local reservation_id="${4:-}"
	python3 - "${event}" "${status}" "${rc}" "${reservation_id}" \
		"${QFW_SLURM_DRIVER_RESULT_FILE:-}" \
		"${backend:-}" "${example:-}" "${qubits:-}" "${shots:-}" \
		"${count:-}" "${depth:-}" "${one_q_gates:-}" \
		"${two_q_gates:-}" "${measurements:-}" "${workload_kind:-}" \
		"${operation:-}" "${owner:-}" "${job_id:-}" \
		"${allocation_id:-}" "${scope_id:-}" "${target_device:-}" <<'PY'
import json
import os
import sys
import time

(
	event,
	status,
	rc,
	reservation_id,
	path,
	backend,
	example,
	qubits,
	shots,
	count,
	depth,
	one_q_gates,
	two_q_gates,
	measurements,
	workload_kind,
	operation,
	owner,
	job_id,
	allocation_id,
	scope_id,
	target_device,
) = sys.argv[1:]
measurement_count = measurements or qubits
record = {
	"schema": "qfw-slurm-driver-v1",
	"kind": "slurm-driver",
	"event": event,
	"status": status,
	"rc": int(rc),
	"backend": backend,
	"example": example,
	"qubits": int(qubits) if qubits else None,
	"shots": int(shots) if shots else None,
	"count": int(count) if count else None,
	"depth": int(depth) if depth else None,
	"one_q_gate_count": int(one_q_gates) if one_q_gates else None,
	"two_q_gate_count": int(two_q_gates) if two_q_gates else None,
	"measurement_count": int(measurement_count) if measurement_count else None,
	"workload_kind": workload_kind,
	"operation": operation,
	"owner": owner or None,
	"job_id": job_id or None,
	"allocation_id": allocation_id or None,
	"scope_id": scope_id or None,
	"target_device_id": target_device or None,
	"reservation_id": reservation_id or None,
	"slurm_job_id": os.environ.get("SLURM_JOB_ID"),
	"timestamp_ns": time.time_ns(),
}
line = "QFW_SLURM_DRIVER_RESULT " + json.dumps(record, sort_keys=True)
print(line)
if path:
	directory = os.path.dirname(path)
	if directory:
		os.makedirs(directory, exist_ok=True)
	with open(path, "a", encoding="utf-8") as handle:
		handle.write(json.dumps(record, sort_keys=True) + "\n")
PY
}

qfw_slurm_parse_reservation_id() {
	python3 -c '
import json
import sys

reservation_id = None
for line in sys.stdin:
	if not line.startswith("QFW_EXAMPLE_RESERVATION "):
		continue
	record = json.loads(line.split(" ", 1)[1])
	if record.get("kind") != "reserve":
		continue
	decision = record.get("decision") or {}
	reservation_id = decision.get("reservation_id")
if not reservation_id:
	raise SystemExit("ERROR: reservation_id not found in reservation output")
print(reservation_id)
'
}

qfw_slurm_default_allocation_id() {
	for name in SLURM_JOB_ID SLURM_JOBID QFW_ALLOCATION_ID; do
		if [[ -n "${!name:-}" ]]; then
			printf "%s\n" "${!name}"
			return 0
		fi
	done
	printf "qfw-example-%s\n" "$$"
}

backend=""
example=""
qubits=""
shots=1024
count=1
depth=1
one_q_gates=0
two_q_gates=0
measurements=""
workload_kind="quantum"
operation="async_run"
walltime=300
ttl=600
timeout=40
target_device=""
scope_id=""
owner=""
job_id=""
allocation_id=""
credential_hint=""
credential_hint_json=""
credential_handle=""
credential_scope=""
analytics_json=""
parameters_json=""
workload_json=""
run_context_json=""
task_class_json=""
run_dir=""
nodes=""
ntasks=""
nodelist=""
het_group=""
exclusive="no"

while [[ $# -gt 0 ]]; do
	case "$1" in
		--backend)
			qfw_slurm_need_value "$@"
			backend="$2"
			shift 2
			;;
		--example)
			qfw_slurm_need_value "$@"
			example="$2"
			shift 2
			;;
		--qubits)
			qfw_slurm_need_value "$@"
			qubits="$2"
			shift 2
			;;
		--shots)
			qfw_slurm_need_value "$@"
			shots="$2"
			shift 2
			;;
		--count)
			qfw_slurm_need_value "$@"
			count="$2"
			shift 2
			;;
		--depth)
			qfw_slurm_need_value "$@"
			depth="$2"
			shift 2
			;;
		--one-q-gates)
			qfw_slurm_need_value "$@"
			one_q_gates="$2"
			shift 2
			;;
		--two-q-gates)
			qfw_slurm_need_value "$@"
			two_q_gates="$2"
			shift 2
			;;
		--measurements)
			qfw_slurm_need_value "$@"
			measurements="$2"
			shift 2
			;;
		--workload-kind)
			qfw_slurm_need_value "$@"
			workload_kind="$2"
			shift 2
			;;
		--operation)
			qfw_slurm_need_value "$@"
			operation="$2"
			shift 2
			;;
		--walltime)
			qfw_slurm_need_value "$@"
			walltime="$2"
			shift 2
			;;
		--ttl)
			qfw_slurm_need_value "$@"
			ttl="$2"
			shift 2
			;;
		--timeout)
			qfw_slurm_need_value "$@"
			timeout="$2"
			shift 2
			;;
		--target-device)
			qfw_slurm_need_value "$@"
			target_device="$2"
			shift 2
			;;
		--scope-id)
			qfw_slurm_need_value "$@"
			scope_id="$2"
			shift 2
			;;
		--owner)
			qfw_slurm_need_value "$@"
			owner="$2"
			shift 2
			;;
		--job-id)
			qfw_slurm_need_value "$@"
			job_id="$2"
			shift 2
			;;
		--allocation-id)
			qfw_slurm_need_value "$@"
			allocation_id="$2"
			shift 2
			;;
		--credential-hint)
			qfw_slurm_need_value "$@"
			credential_hint="$2"
			shift 2
			;;
		--credential-hint-json)
			qfw_slurm_need_value "$@"
			credential_hint_json="$2"
			shift 2
			;;
		--credential-handle)
			qfw_slurm_need_value "$@"
			credential_handle="$2"
			shift 2
			;;
		--credential-scope)
			qfw_slurm_need_value "$@"
			credential_scope="$2"
			shift 2
			;;
		--analytics-json)
			qfw_slurm_need_value "$@"
			analytics_json="$2"
			shift 2
			;;
		--parameters-json)
			qfw_slurm_need_value "$@"
			parameters_json="$2"
			shift 2
			;;
		--workload-json)
			qfw_slurm_need_value "$@"
			workload_json="$2"
			shift 2
			;;
		--run-context-json)
			qfw_slurm_need_value "$@"
			run_context_json="$2"
			shift 2
			;;
		--task-class-json)
			qfw_slurm_need_value "$@"
			task_class_json="$2"
			shift 2
			;;
		--run-dir)
			qfw_slurm_need_value "$@"
			run_dir="$2"
			shift 2
			;;
		--nodes)
			qfw_slurm_need_value "$@"
			nodes="$2"
			shift 2
			;;
		--ntasks)
			qfw_slurm_need_value "$@"
			ntasks="$2"
			shift 2
			;;
		--nodelist)
			qfw_slurm_need_value "$@"
			nodelist="$2"
			shift 2
			;;
		--het-group)
			qfw_slurm_need_value "$@"
			het_group="$2"
			shift 2
			;;
		--exclusive)
			exclusive="yes"
			shift
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
			echo "ERROR: unknown driver option: $1" >&2
			usage >&2
			exit 2
			;;
	esac
done

if [[ -z "${backend}" || -z "${example}" || -z "${qubits}" ]]; then
	echo "ERROR: --backend, --example, and --qubits are required" >&2
	usage >&2
	exit 2
fi
if [[ $# -lt 1 ]]; then
	echo "ERROR: application command is required after --" >&2
	usage >&2
	exit 2
fi

if [[ -z "${allocation_id}" ]]; then
	allocation_id="$(qfw_slurm_default_allocation_id)"
fi
if [[ -z "${job_id}" ]]; then
	job_id="${allocation_id}"
fi
if [[ -z "${owner}" ]]; then
	owner="${USER:-${LOGNAME:-qfw-example}}"
fi

qfw_example_require_runtime

qfw_srun_base=()
if [[ -n "${run_dir}" ]]; then
	qfw_srun_base+=(--run-dir "${run_dir}")
fi

qfw_srun_control=("${qfw_srun_base[@]}" --nodes 1 --ntasks 1)
if [[ -n "${nodelist}" ]]; then
	qfw_srun_control+=(--nodelist "${nodelist}")
fi
if [[ -n "${het_group}" ]]; then
	qfw_srun_control+=(--het-group "${het_group}")
fi

qfw_srun_app=("${qfw_srun_base[@]}")
if [[ -n "${nodes}" ]]; then
	qfw_srun_app+=(--nodes "${nodes}")
fi
if [[ -n "${ntasks}" ]]; then
	qfw_srun_app+=(--ntasks "${ntasks}")
fi
if [[ -n "${nodelist}" ]]; then
	qfw_srun_app+=(--nodelist "${nodelist}")
fi
if [[ -n "${het_group}" ]]; then
	qfw_srun_app+=(--het-group "${het_group}")
fi
if [[ "${exclusive}" == "yes" ]]; then
	qfw_srun_app+=(--exclusive)
fi

reserve_command=(
	"$(qfw_example_path tests/qfw_example_reservation_driver.py)"
	reserve
	--backend "${backend}"
	--example "${example}"
	--qubits "${qubits}"
	--shots "${shots}"
	--count "${count}"
	--depth "${depth}"
	--one-q-gates "${one_q_gates}"
	--two-q-gates "${two_q_gates}"
	--workload-kind "${workload_kind}"
	--operation "${operation}"
	--walltime "${walltime}"
	--ttl "${ttl}"
	--timeout "${timeout}"
)
if [[ -n "${measurements}" ]]; then
	reserve_command+=(--measurements "${measurements}")
fi
if [[ -n "${target_device}" ]]; then
	reserve_command+=(--target-device "${target_device}")
fi
if [[ -n "${scope_id}" ]]; then
	reserve_command+=(--scope-id "${scope_id}")
fi
if [[ -n "${owner}" ]]; then
	reserve_command+=(--owner "${owner}")
fi
if [[ -n "${job_id}" ]]; then
	reserve_command+=(--job-id "${job_id}")
fi
if [[ -n "${allocation_id}" ]]; then
	reserve_command+=(--allocation-id "${allocation_id}")
fi
if [[ -n "${credential_hint}" ]]; then
	reserve_command+=(--credential-hint "${credential_hint}")
fi
if [[ -n "${credential_hint_json}" ]]; then
	reserve_command+=(--credential-hint-json "${credential_hint_json}")
fi
if [[ -n "${credential_handle}" ]]; then
	reserve_command+=(--credential-handle "${credential_handle}")
fi
if [[ -n "${credential_scope}" ]]; then
	reserve_command+=(--credential-scope "${credential_scope}")
fi
if [[ -n "${analytics_json}" ]]; then
	reserve_command+=(--analytics-json "${analytics_json}")
fi
if [[ -n "${parameters_json}" ]]; then
	reserve_command+=(--parameters-json "${parameters_json}")
fi
if [[ -n "${workload_json}" ]]; then
	reserve_command+=(--workload-json "${workload_json}")
fi
if [[ -n "${run_context_json}" ]]; then
	reserve_command+=(--run-context-json "${run_context_json}")
fi
if [[ -n "${task_class_json}" ]]; then
	reserve_command+=(--task-class-json "${task_class_json}")
fi

release_command=(
	"$(qfw_example_path tests/qfw_example_reservation_driver.py)"
	release
	--backend "${backend}"
	--timeout "${timeout}"
)

reservation_id=""
release_done=0

qfw_slurm_release() {
	if [[ -z "${reservation_id}" || "${release_done}" == "1" ]]; then
		return 0
	fi
	local output rc
	set +e
	output="$(
		qfw_example_srun "${qfw_srun_control[@]}" \
			"${release_command[@]}" \
			--reservation-id "${reservation_id}"
	)"
	rc=$?
	set -e
	printf "%s\n" "${output}" >&2
	release_done=1
	return "${rc}"
}

qfw_slurm_exit() {
	local rc="$1"
	trap - EXIT
	local release_rc=0
	if [[ -n "${reservation_id}" && "${release_done}" != "1" ]]; then
		qfw_slurm_release || release_rc=$?
	fi
	local final_rc="${rc}"
	if [[ "${final_rc}" -eq 0 && "${release_rc}" -ne 0 ]]; then
		final_rc="${release_rc}"
	fi
	local status="ok"
	if [[ "${final_rc}" -ne 0 ]]; then
		status="error"
	fi
	qfw_slurm_emit "finish" "${status}" "${final_rc}" "${reservation_id}"
	exit "${final_rc}"
}

trap 'qfw_slurm_exit "$?"' EXIT
qfw_slurm_emit "start" "running" 0 ""

reserve_output="$(
	qfw_example_srun "${qfw_srun_control[@]}" "${reserve_command[@]}"
)"
printf "%s\n" "${reserve_output}" >&2
reservation_id="$(
	printf "%s\n" "${reserve_output}" | qfw_slurm_parse_reservation_id
)"
qfw_slurm_emit "reserved" "ok" 0 "${reservation_id}"

set +e
(
	export QFW_RESERVATION_ID="${reservation_id}"
	qfw_example_srun "${qfw_srun_app[@]}" "$@"
)
app_rc=$?
set -e

release_rc=0
qfw_slurm_release || release_rc=$?
release_done=1

trap - EXIT
final_rc="${app_rc}"
if [[ "${final_rc}" -eq 0 && "${release_rc}" -ne 0 ]]; then
	final_rc="${release_rc}"
fi
status="ok"
if [[ "${final_rc}" -ne 0 ]]; then
	status="error"
fi
qfw_slurm_emit "finish" "${status}" "${final_rc}" "${reservation_id}"
exit "${final_rc}"
