#!/bin/bash

set -euo pipefail

QFW_IBM_LOCAL_DEVICE_CONFIG="${QFW_IBM_LOCAL_DEVICE_CONFIG:-}"
QFW_IBM_DEVICE="${QFW_IBM_DEVICE:-ibm-fake-lagos}"

if [ -z "$QFW_IBM_LOCAL_DEVICE_CONFIG" ]; then
	echo "ERROR: variable not set. QFW_IBM_LOCAL_DEVICE_CONFIG=<path-to-device-access-yaml>"
	exit 1
fi

echo "[$(date)] $0 on $(hostname)"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_require_runtime

export QFW_DEVICE_ACCESS_CFG="${QFW_IBM_LOCAL_DEVICE_CONFIG}"

# This example does not require QPM, it calls the frontend directly.
# Hence, we don't need to run qfw_example_setup_{local,site}_services
PYTHONUNBUFFERED=1 python3 "$(qfw_example_path tests/test_qrmi_ibm.py)" "${QFW_IBM_DEVICE}"

echo "[$(date)] $0 done on $(hostname)"
