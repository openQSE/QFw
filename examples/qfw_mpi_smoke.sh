#!/bin/bash

set -xe

cleanup() {
	qfw_teardown.sh || true
}
trap cleanup EXIT

qfw_setup.sh --services-config "$QFW_PATH/examples/qfw_mpi_smoke_services.yaml"
qfw_srun.sh --load-modules api_mpi_smoke \
	"$QFW_PATH/examples/tests/test_mpi_smoke.py"

trap - EXIT
qfw_teardown.sh
