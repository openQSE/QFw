#!/bin/bash

mkdir -p $1; prte --host $2 --report-uri $1/dvm-uri --daemonize

