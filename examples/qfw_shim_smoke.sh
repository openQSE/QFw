#!/bin/bash

set -e

usage() {
	cat <<EOF
Usage: ./qfw_shim_smoke.sh --lib <qrmi|qdmi> [test args...]

Run the QRMI/QDMI shim smoke test. The --lib argument is required so the test
explicitly exercises one shim library path. The server validates whether the
selected library supports each requested API.
EOF
}

lib=""
for arg in "$@"; do
	if [[ "${arg}" == "--lib" ]]; then
		lib="pending"
	elif [[ "${lib}" == "pending" ]]; then
		lib="${arg}"
		break
	fi
done

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
	usage
	exit 0
fi

if [[ -z "${lib}" || "${lib}" == "pending" ]]; then
	usage >&2
	exit 1
fi

if [[ "${lib}" != "qrmi" && "${lib}" != "qdmi" ]]; then
	echo "ERROR: --lib must be qrmi or qdmi, got '${lib}'" >&2
	exit 1
fi

cleanup() {
	qfw_teardown.sh >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Starting QFw shim smoke test with lib=${lib}"
qfw_setup.sh --services-config "$QFW_PATH/examples/qfw_shim_smoke_services.yaml"
qfw_srun.sh --load-modules api_qpm \
	"$QFW_PATH/examples/tests/test_shim_smoke.py" "$@"

trap - EXIT
echo "Stopping QFw shim smoke test"
qfw_teardown.sh
