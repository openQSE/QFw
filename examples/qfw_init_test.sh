#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_execution_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

qfw_example_begin "init-test" "$@"
backend="$(qfw_example_backend nwqsim)"
qfw_example_setup_backend_service "${backend}"

qfw_example_srun --nodes 1 --ntasks 1 \
	"$(qfw_example_path tests/test_init_qfw.py)" "${backend}"
