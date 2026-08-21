#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_common_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

qfw_example_begin "mpi-smoke" "$@"
qfw_example_setup_local_services qfw_mpi_smoke_services.yaml mpi-smoke
qfw_example_srun_with_modules api_mpi_smoke \
	--nodes 1 \
	--ntasks 1 \
	"$(qfw_example_path tests/test_mpi_smoke.py)"
