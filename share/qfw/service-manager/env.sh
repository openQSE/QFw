#!/usr/bin/env bash
# Copy to /etc/openqse/qfw/env.sh and adjust the prefixes for the site.

export QFW_PREFIX="${QFW_PREFIX:-/opt/openqse/qfw/current}"
export DEFW_PREFIX="${DEFW_PREFIX:-/opt/openqse/defw/current}"
export QFW_SITE_CONFIG="${QFW_SITE_CONFIG:-/etc/openqse/qfw/site.yaml}"
# Set this to storage visible at the same pathname from QPM and application
# nodes when site.yaml publishes a dynamic directory-service connection file.
export QFW_SHARED_ROOT="${QFW_SHARED_ROOT:-/shared/openqse/qfw}"

if [[ -r "${QFW_PREFIX}/bin/qfw-activate" ]]; then
	# shellcheck source=/dev/null
	source "${QFW_PREFIX}/bin/qfw-activate"
fi
