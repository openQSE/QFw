#!/bin/bash

if [[ -z "${_QFW_ACTIVE:-}" ]]; then
	source $QFW_SETUP_PATH/qfw_activate --skip-patches
fi

mkdir -p $1; prte --host $2 --report-uri $1/dvm-uri --daemonize

