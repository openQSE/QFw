#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_execution_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

usage() {
	cat <<EOF
Usage: ./qfw_job_stream.sh [--service-mode local|site] [--backend NAME] [options]

Stream Qiskit jobs through one QFw backend: a mix of GHZ and random circuits
over a range of qubit counts, at an interval, from one or more concurrent
workers, for a number of jobs or a length of time. The load generator for
the telemetry dashboards, and a steady load for a QPM.

Options (after the common ones):
  --jobs N            Number of jobs, 0 to run for --duration (default: 20)
  --duration SEC      Stop after SEC seconds (default: no limit)
  --interval SEC      Pause between a worker's jobs (default: 1.0)
  --workers N         Concurrent workers, each with its own backend (default: 1)
  --qubits MIN-MAX    Qubit count or inclusive range per job (default: 2-5)
  --shots N           Shots per job (default: 1024)
  --circuits LIST     Kinds cycled through: ghz, random, or mixed (default: ghz,random)
  --seed N            Seed for the plan and the random circuits
  --stop-on-error     End the stream at the first failed job
  --tolerate-failures Exit 0 when some jobs failed but at least one completed
  -h, --help          Show this help

The reservation is sized from these: its task count is the number of jobs
and its walltime covers the run with a margin. With the telemetry stack up
(QFW_TELEMETRY=otlp), every job shows on the QFw Jobs dashboard as it runs.
EOF
}

jobs=20
duration=0
interval=1.0
workers=1
qubits="2-5"
shots=1024
circuits="ghz,random"
app_args=()

need_value() {
	if [[ $# -lt 2 || -z "${2:-}" ]]; then
		echo "ERROR: $1 requires a value" >&2
		exit 2
	fi
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--jobs) need_value "$@"; jobs="$2"; shift 2 ;;
		--duration) need_value "$@"; duration="$2"; shift 2 ;;
		--interval) need_value "$@"; interval="$2"; shift 2 ;;
		--workers) need_value "$@"; workers="$2"; shift 2 ;;
		--qubits) need_value "$@"; qubits="$2"; shift 2 ;;
		--shots) need_value "$@"; shots="$2"; shift 2 ;;
		--circuits) need_value "$@"; circuits="$2"; shift 2 ;;
		--seed) need_value "$@"; app_args+=(--seed "$2"); shift 2 ;;
		--stop-on-error|--tolerate-failures) app_args+=("$1"); shift ;;
		-h|--help) usage; exit 0 ;;
		*)
			echo "ERROR: unknown option: $1" >&2
			usage >&2
			exit 2
			;;
	esac
done

backend="$(qfw_example_backend nwqsim)"

# The reservation covers the whole stream. Its task count is the number of
# jobs (or what the duration and interval allow), and its walltime is the
# run's expected length plus a margin, so a long stream does not outlive it.
read -r reserve_count reserve_walltime reserve_qubits < <(python3 - \
	"${jobs}" "${duration}" "${interval}" "${workers}" "${qubits}" <<'PY'
import math
import sys

jobs, duration, interval, workers, qubits = sys.argv[1:]
jobs = int(jobs)
duration = float(duration)
interval = float(interval)
workers = max(1, int(workers))
top = int(qubits.split("-")[-1])
per_job = interval + 10.0
if jobs > 0:
	count = jobs
	seconds = math.ceil(jobs / workers) * per_job
	if duration > 0:
		seconds = min(seconds, duration)
else:
	count = max(1, math.ceil(duration / max(interval, 0.1)) * workers)
	seconds = duration
print(count, int(math.ceil(seconds)) + 300, top)
PY
)

qfw_example_begin "${QFW_EXAMPLE_NAME_OVERRIDE:-job-stream}" "$@"
qfw_example_setup_backend_service "${backend}"

qfw_example_slurm_driver \
	--backend "${backend}" \
	--example job-stream \
	--qubits "${reserve_qubits}" \
	--shots "${shots}" \
	--count "${reserve_count}" \
	--operation async_run \
	--walltime "${reserve_walltime}" \
	--nodes 1 \
	--ntasks 1 \
	-- "$(qfw_example_path tests/test_job_stream.py)" \
	--backend "${backend}" \
	--jobs "${jobs}" \
	--duration "${duration}" \
	--interval "${interval}" \
	--workers "${workers}" \
	--qubits "${qubits}" \
	--shots "${shots}" \
	--circuits "${circuits}" \
	"${app_args[@]}"
