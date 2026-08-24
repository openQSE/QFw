if(NOT QFW_SOURCE_DIR)
	message(FATAL_ERROR "QFW_SOURCE_DIR is required")
endif()
if(NOT QFW_BINARY_DIR)
	message(FATAL_ERROR "QFW_BINARY_DIR is required")
endif()
if(NOT QFW_PYTHON)
	message(FATAL_ERROR "QFW_PYTHON is required")
endif()

find_program(QFW_BASH bash REQUIRED)

set(qfw_generated_bin "${QFW_BINARY_DIR}/generated/bin")
set(qfw_run_base "${QFW_BINARY_DIR}/source-runtime-smoke")
file(REMOVE_RECURSE "${qfw_run_base}")

execute_process(
	COMMAND "${QFW_BASH}" -c
		"set -e
		export PS1='source \\W> '
		export MANPATH='/qfw-test-man'
		source '${qfw_generated_bin}/qfw-activate'
		test \"\${PS1}\" = '(qfw) source \\W> '
		declare -F qfw-deactivate >/dev/null
		! declare -F qfw_deactivate >/dev/null
		test \"\${QFW_PREFIX}\" = '${QFW_SOURCE_DIR}'
		test \"\${QFW_BIN_PATH}\" = '${qfw_generated_bin}'
		test \"\${QFW_LIBEXEC_DIR}\" = '${QFW_BINARY_DIR}/generated/libexec/qfw'
		test \"\${QFW_SHARE_DIR}\" = '${QFW_SOURCE_DIR}/share/qfw'
		test \"\${QFW_SITE_CONFIG}\" = '${QFW_SOURCE_DIR}/share/qfw/config/site.yaml'
		test \"\${MANPATH}\" = '${QFW_SOURCE_DIR}/man:/qfw-test-man'
		command -v qfw-setup >/dev/null
		command -v qfw-status >/dev/null
		command -v qfw-dir-svc >/dev/null
		command -v qfw-qpm-svc >/dev/null
		'${QFW_PYTHON}' -c 'import qfw_runtime; print(\"source activation import ok\")'
		qfw-deactivate
		test \"\${PS1}\" = 'source \\W> '
		test -z \"\${QFW_PREFIX+x}\"
		test \"\${MANPATH}\" = '/qfw-test-man'
		test -z \"\${DEFW_PREFIX+x}\"
		test -z \"\${_QFW_ACTIVE+x}\""
	RESULT_VARIABLE activation_rc)
if(NOT activation_rc EQUAL 0)
	message(FATAL_ERROR "QFw source activation smoke failed")
endif()

execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"QFW_PREFIX=${QFW_SOURCE_DIR}"
		"DEFW_PREFIX=${QFW_SOURCE_DIR}/DEFw"
		"QFW_RUN_BASE_DIR=${qfw_run_base}"
		"PYTHONPATH=${QFW_SOURCE_DIR}/setup"
		"${QFW_PYTHON}" -m qfw_runtime.commands qfw-setup
			--profile local
			--dry-run
			--run-id source-smoke
	RESULT_VARIABLE setup_rc)
if(NOT setup_rc EQUAL 0)
	message(FATAL_ERROR "QFw source qfw-setup dry-run failed")
endif()

set(source_state "${qfw_run_base}/source-smoke/state/runtime-state.json")
if(NOT EXISTS "${source_state}")
	message(FATAL_ERROR "QFw source qfw-setup did not write runtime state")
endif()
file(READ "${source_state}" source_state_text)
string(FIND "${source_state_text}" "allocation-local" source_local_index)
if(source_local_index EQUAL -1)
	message(FATAL_ERROR "QFw source runtime state missed local resolver scope")
endif()
string(FIND "${source_state_text}" "QFW_LOCAL_DIRSVC_ENDPOINT" source_endpoint_index)
if(source_endpoint_index EQUAL -1)
	message(FATAL_ERROR "QFw source runtime state missed local endpoint")
endif()

execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"QFW_RUN_BASE_DIR=${qfw_run_base}"
		"PYTHONPATH=${QFW_SOURCE_DIR}/setup"
		"${QFW_PYTHON}" -m qfw_runtime.commands qfw-status
			--run-dir "${qfw_run_base}/source-smoke"
			--json
	RESULT_VARIABLE status_rc
	OUTPUT_VARIABLE status_output)
if(NOT status_rc EQUAL 0)
	message(FATAL_ERROR "QFw source qfw-status failed")
endif()
string(FIND "${status_output}" "\"state\": \"ready\"" status_ready_index)
if(status_ready_index EQUAL -1)
	message(FATAL_ERROR "QFw source qfw-status did not report ready")
endif()

execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"QFW_PREFIX=${QFW_SOURCE_DIR}"
		"DEFW_PREFIX=${QFW_SOURCE_DIR}/DEFw"
		"QFW_SHARED_ROOT=${qfw_run_base}"
		"QFW_SITE_DIRSVC_ENDPOINTS=127.0.0.1:8090"
		"PYTHONPATH=${QFW_SOURCE_DIR}/setup"
		"${QFW_PYTHON}" -m qfw_runtime.commands qfw-dirsvc-start
			--dry-run
			--run-dir "${qfw_run_base}/source-smoke"
			--name source-dirsvc
			--pid-file "${qfw_run_base}/source-smoke/state/source-dirsvc.pid"
			--ready-file "${qfw_run_base}/source-smoke/state/source-dirsvc-ready.json"
	RESULT_VARIABLE dirsvc_rc)
if(NOT dirsvc_rc EQUAL 0)
	message(FATAL_ERROR "QFw source qfw-dirsvc-start dry-run failed")
endif()

execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"QFW_PREFIX=${QFW_SOURCE_DIR}"
		"DEFW_PREFIX=${QFW_SOURCE_DIR}/DEFw"
		"QFW_SHARED_ROOT=${qfw_run_base}"
		"QFW_SITE_DIRSVC_ENDPOINTS=127.0.0.1:8090"
		"PYTHONPATH=${QFW_SOURCE_DIR}/setup"
		"${QFW_PYTHON}" -m qfw_runtime.commands qfw-service-start
			--dry-run
			--service-id nwqsim
			--module svc_nwqsim_qpm
			--load-modules svc_nwqsim_qpm,api_launcher
			--run-dir "${qfw_run_base}/source-smoke"
			--pid-file "${qfw_run_base}/source-smoke/state/nwqsim.pid"
			--ready-file "${qfw_run_base}/source-smoke/state/nwqsim-ready.json"
	RESULT_VARIABLE service_rc)
if(NOT service_rc EQUAL 0)
	message(FATAL_ERROR "QFw source qfw-service-start dry-run failed")
endif()
