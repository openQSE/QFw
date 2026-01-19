#!/bin/bash

source $QFW_SETUP_PATH/qfw_activate

mkdir -p $1; prte --host $2 --report-uri $1/dvm-uri --daemonize

