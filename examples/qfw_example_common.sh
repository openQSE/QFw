#!/bin/bash

qfw_example_begin() {
	QFW_EXAMPLE_NAME="$1"
	shift || true
	QFW_EXAMPLE_ARGS=("$@")
	QFW_EXAMPLE_START_EPOCH="$(date +%s)"
	QFW_EXAMPLE_SETUP_STARTED=0
	QFW_EXAMPLE_TEARDOWN_DONE=0
	export QFW_EXAMPLE_NAME
	trap 'qfw_example_exit "$?"' EXIT
	qfw_example_emit "start" "running" 0 0
}

qfw_example_require_runtime() {
	if [[ -z "${QFW_PATH:-}" ]]; then
		echo "ERROR: QFW_PATH is not set. Source qfw_activate first." >&2
		return 1
	fi
	if ! command -v qfw_setup.sh >/dev/null 2>&1; then
		echo "ERROR: qfw_setup.sh is not in PATH. Source qfw_activate first." >&2
		return 1
	fi
	if ! command -v qfw_srun.sh >/dev/null 2>&1; then
		echo "ERROR: qfw_srun.sh is not in PATH. Source qfw_activate first." >&2
		return 1
	fi
	if ! command -v qfw_teardown.sh >/dev/null 2>&1; then
		echo "ERROR: qfw_teardown.sh is not in PATH. Source qfw_activate first." >&2
		return 1
	fi
}

qfw_example_setup() {
	qfw_example_require_runtime
	QFW_EXAMPLE_SETUP_STARTED=1
	qfw_setup.sh "$@"
}

qfw_example_srun() {
	qfw_srun.sh "$@"
}

qfw_example_teardown() {
	if [[ "${QFW_EXAMPLE_SETUP_STARTED:-0}" == "1" &&
	      "${QFW_EXAMPLE_TEARDOWN_DONE:-0}" == "0" ]]; then
		QFW_EXAMPLE_TEARDOWN_DONE=1
		qfw_teardown.sh
	fi
}

qfw_example_exit() {
	local rc="$1"
	trap - EXIT
	qfw_example_finish "${rc}"
	exit "${rc}"
}

qfw_example_finish() {
	local rc="$1"
	local teardown_rc=0
	if [[ "${QFW_EXAMPLE_SETUP_STARTED:-0}" == "1" &&
	      "${QFW_EXAMPLE_TEARDOWN_DONE:-0}" == "0" ]]; then
		QFW_EXAMPLE_TEARDOWN_DONE=1
		qfw_teardown.sh || teardown_rc=$?
	fi
	local now duration status
	now="$(date +%s)"
	duration=$((now - QFW_EXAMPLE_START_EPOCH))
	status="ok"
	if [[ "${rc}" -ne 0 || "${teardown_rc}" -ne 0 ]]; then
		status="error"
	fi
	qfw_example_emit "finish" "${status}" "${rc}" "${duration}" "${teardown_rc}"
}

qfw_example_emit() {
	local event="$1"
	local status="$2"
	local rc="$3"
	local duration="$4"
	local teardown_rc="${5:-0}"
	python3 - "$event" "$status" "$rc" "$duration" "$teardown_rc" \
		"${QFW_EXAMPLE_RESULT_FILE:-}" "${QFW_EXAMPLE_NAME:-unknown}" \
		"${QFW_EXAMPLE_ARGS[@]}" <<'PY'
import json
import os
import sys
import time

event, status, rc, duration, teardown_rc, path, name, *args = sys.argv[1:]
record = {
	"schema": "qfw-example-wrapper-v1",
	"kind": "wrapper",
	"event": event,
	"example": name,
	"status": status,
	"rc": int(rc),
	"teardown_rc": int(teardown_rc),
	"duration_sec": float(duration),
	"timestamp_ns": time.time_ns(),
	"args": args,
}
line = "QFW_EXAMPLE_RESULT " + json.dumps(record, sort_keys=True)
print(line)
if path:
	directory = os.path.dirname(path)
	if directory:
		os.makedirs(directory, exist_ok=True)
	with open(path, "a", encoding="utf-8") as handle:
		handle.write(json.dumps(record, sort_keys=True) + "\n")
PY
}
