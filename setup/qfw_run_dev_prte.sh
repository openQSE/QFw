#!/bin/bash

if [[ -z "${_QFW_ACTIVE:-}" ]]; then
	source $QFW_SETUP_PATH/qfw_activate --skip-patches
fi

root_args=()
if [ "$(id -u)" -eq 0 ]; then
	root_args+=(--allow-run-as-root)
fi

mkdir -p "$1"
prte "${root_args[@]}" --host "$2" --report-uri "$1/dvm-uri" --daemonize
