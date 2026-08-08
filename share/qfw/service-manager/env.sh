#!/usr/bin/env bash
# Copy to /etc/openqse/qfw/env.sh and adjust the prefixes for the site.

export QFW_PREFIX="${QFW_PREFIX:-/opt/openqse/qfw/current}"
export DEFW_PREFIX="${DEFW_PREFIX:-/opt/openqse/defw/current}"
export QFW_SITE_CONFIG="${QFW_SITE_CONFIG:-/etc/openqse/qfw/site.yaml}"
export QFW_SERVICE_RUNTIME_CONFIG="${QFW_SERVICE_RUNTIME_CONFIG:-/etc/openqse/qfw/services/service-runtime.yaml}"
export QFW_DEVICE_ACCESS_CFG="${QFW_DEVICE_ACCESS_CFG:-/etc/openqse/qfw/device/device-access.yaml}"

if [[ -r "${QFW_PREFIX}/bin/qfw-activate" ]]; then
	# shellcheck source=/dev/null
	source "${QFW_PREFIX}/bin/qfw-activate"
fi
