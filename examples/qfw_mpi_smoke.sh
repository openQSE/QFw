#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "mpi-smoke" "$@"
qfw_example_setup --services-config "$QFW_PATH/examples/qfw_mpi_smoke_services.yaml"
qfw_example_srun --load-modules api_mpi_smoke \
	"$QFW_PATH/examples/tests/test_mpi_smoke.py"
