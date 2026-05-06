#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${QFW_SETUP_PATH:-}" ]]; then
	export QFW_SETUP_PATH="${SCRIPT_DIR}"
fi

source "${QFW_SETUP_PATH}/qfw_allocation.sh"
qfw_detect_allocation --force || exit 1

echo "${QFW_GROUPS}"

exit 0
