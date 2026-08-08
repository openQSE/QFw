#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "chemistry" "$@"
qfw_example_setup

# takes the name of the chemistry app script to run
qfw_example_srun "$(qfw_example_path "tests/chemistry_example_aim2/$1")"
