#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_common_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

qfw_example_begin "supermarq" "$@"
qfw_example_setup_backend_service "$7"

# test_supermarq.py takes
#   run: sync or async
#   iterations: number of iterations to run the test
#   startquibt: The number of qubits to start with. Increases by one if
#               increas is true
#   shots: number of shots
#   increase: increase the number of qubits per iteration
#   method: ghz or vqe
#   backend: The backend type to use: tnqvm, nwqsim or qb
#
operation="async_run"
if [[ "$1" == "sync" ]]; then
	operation="sync_run"
fi

reservation_qubits="$3"
case "$5" in
	1|yes|true|on|y|YES|TRUE|ON|Y)
		reservation_qubits=$(( $3 + $2 - 1 ))
		;;
esac
reservation_count="${QFW_SUPERMARQ_RESERVATION_COUNT:-$2}"
parameters_json="$(
	python3 - "$2" "$3" "$4" "$5" "$6" <<'PY'
import json
import sys

iterations, startqbit, shots, increase, method = sys.argv[1:]
print(json.dumps({
	"iterations": int(iterations),
	"startqbit": int(startqbit),
	"shots": int(shots),
	"increase": increase,
	"method": method,
}))
PY
)"
workload_json="$(
	python3 - "$6" <<'PY'
import json
import sys

print(json.dumps({"method": sys.argv[1]}))
PY
)"

QFW_SUPERMARQ_SHUTDOWN_QPM=no \
	qfw_example_slurm_driver \
	--backend "$7" \
	--example qfw_supermarq \
	--qubits "${reservation_qubits}" \
	--shots "$4" \
	--count "${reservation_count}" \
	--operation "${operation}" \
	--nodes 1 \
	--ntasks 1 \
	--parameters-json "${parameters_json}" \
	--workload-json "${workload_json}" \
	-- "$(qfw_example_path tests/test_supermarq.py)" --run "$1" \
		--iterations "$2" --startqbit "$3" --shots "$4" \
		--increase "$5" --method "$6" --backend "$7"
