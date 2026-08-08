#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "init-test" "$@"
qfw_example_setup

qfw_example_srun "$(qfw_example_path tests/test_init_qfw.py)"
