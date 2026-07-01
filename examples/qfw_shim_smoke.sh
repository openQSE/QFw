#!/bin/bash

set -e

usage() {
	cat <<EOF
Usage: ./qfw_shim_smoke.sh --lib <qrmi|qdmi> [test args...]
       ./qfw_shim_smoke.sh --libs <qdmi,qrmi> [test args...]

Run the QRMI/QDMI shim smoke test. Provide either:
  --lib  <qrmi|qdmi>   exercise a single shim library path, or
  --libs <qdmi,qrmi>   run each introspection call through the listed
                       libraries in order, for a side-by-side comparison.
The server validates whether the selected library supports each requested API.
EOF
}

lib=""
libs=""
capture=""
for arg in "$@"; do
	case "${capture}" in
		lib)  lib="${arg}";  capture=""; continue ;;
		libs) libs="${arg}"; capture=""; continue ;;
	esac
	case "${arg}" in
		--lib)  capture="lib" ;;
		--libs) capture="libs" ;;
	esac
done

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
	usage
	exit 0
fi

if [[ -z "${lib}" && -z "${libs}" ]]; then
	usage >&2
	exit 1
fi

if [[ -n "${lib}" && "${lib}" != "qrmi" && "${lib}" != "qdmi" ]]; then
	echo "ERROR: --lib must be qrmi or qdmi, got '${lib}'" >&2
	exit 1
fi

cleanup() {
	qfw_teardown.sh >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Starting QFw shim smoke test with ${libs:+libs=${libs} }${lib:+lib=${lib}}"
qfw_setup.sh --services-config "$QFW_PATH/examples/qfw_shim_smoke_services.yaml"
qfw_srun.sh --load-modules api_qpm \
	"$QFW_PATH/examples/tests/test_shim_smoke.py" "$@"

trap - EXIT
echo "Stopping QFw shim smoke test"
qfw_teardown.sh
