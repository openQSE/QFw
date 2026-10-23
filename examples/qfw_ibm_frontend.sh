#!/bin/bash

set -euo pipefail

if [ $# != 1 ]; then
	echo "Usage: $0 <path-to-device-access-yaml>"
	exit 1
fi

device_config="$1"

echo "[`date`] $0 on `hostname`"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

QFW_SHARED_ROOT="${QFW_SHARED_ROOT:-/workspace/qfw-container-base}"

source ${QFW_INSTALL_PREFIX}/bin/qfw-activate --venv "${QFW_VENV}"

export QFW_DEVICE_ACCESS_CFG="${device_config}"

# This example does not require QPM, it calls the frontend directly.
# Hence, we don't need to run qfw_example_setup_{local,site}_services
PYTHONUNBUFFERED=1 python3 $(qfw_example_path tests/test_ibm_frontend.py)

echo "[`date`] $0 done on `hostname`"
