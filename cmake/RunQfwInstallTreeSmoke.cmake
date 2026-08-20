if(NOT QFW_BINARY_DIR)
	message(FATAL_ERROR "QFW_BINARY_DIR is required")
endif()
if(NOT QFW_INSTALL_PREFIX)
	message(FATAL_ERROR "QFW_INSTALL_PREFIX is required")
endif()
if(NOT QFW_PYTHON)
	message(FATAL_ERROR "QFW_PYTHON is required")
endif()
if(NOT QFW_PYTHON_INSTALL_DIR)
	message(FATAL_ERROR "QFW_PYTHON_INSTALL_DIR is required")
endif()

find_program(QFW_BASH bash REQUIRED)

function(qfw_free_port out_var)
	get_property(next_port GLOBAL PROPERTY QFW_NEXT_FAKE_PORT)
	if(NOT next_port)
		set(next_port 19000)
	endif()
	foreach(_attempt RANGE 1 200)
		math(EXPR selected_port "${next_port} + 1")
		execute_process(
			COMMAND "${QFW_PYTHON}" -c
				"import socket, sys
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(('127.0.0.1', int(sys.argv[1])))
finally:
    sock.close()
" "${selected_port}"
			RESULT_VARIABLE port_rc
			OUTPUT_QUIET
			ERROR_QUIET)
		set(next_port "${selected_port}")
		if(port_rc EQUAL 0)
			set_property(GLOBAL PROPERTY QFW_NEXT_FAKE_PORT
				"${selected_port}")
			set(${out_var} "${selected_port}" PARENT_SCOPE)
			return()
		endif()
	endforeach()
	message(FATAL_ERROR "Unable to allocate a free QFw smoke-test port")
endfunction()

file(REMOVE_RECURSE "${QFW_INSTALL_PREFIX}")
execute_process(
	COMMAND "${CMAKE_COMMAND}" --install "${QFW_BINARY_DIR}"
		--prefix "${QFW_INSTALL_PREFIX}"
	RESULT_VARIABLE install_rc)
if(NOT install_rc EQUAL 0)
	message(FATAL_ERROR "QFw install-tree smoke install failed")
endif()

foreach(forbidden_config
		lib/qfw/services/dev-config
		lib/qfw/services/config
		share/qfw/config/device/device-access.yaml)
	if(EXISTS "${QFW_INSTALL_PREFIX}/${forbidden_config}")
		message(FATAL_ERROR
			"development or credential config installed: ${forbidden_config}")
	endif()
endforeach()

set(qfw_install_pythonpath
	"${QFW_INSTALL_PREFIX}/${QFW_PYTHON_INSTALL_DIR}")
execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"PYTHONPATH=${qfw_install_pythonpath}"
		"${QFW_PYTHON}" -S -c
		"import qhw_data, qhw_iqm, qhw_admission, qhw_scheduler
from qhw_iqm import normalize_device
print(qhw_data.__file__)
print(qhw_iqm.__file__)
print(qhw_admission.__file__)
print(qhw_scheduler.__file__)
print(normalize_device.__name__)"
	RESULT_VARIABLE qhw_import_rc
	OUTPUT_VARIABLE qhw_import_out
	ERROR_VARIABLE qhw_import_err)
if(NOT qhw_import_rc EQUAL 0)
	message(FATAL_ERROR
		"installed QFw qhw runtime imports failed\n"
		"stdout:\n${qhw_import_out}\n"
		"stderr:\n${qhw_import_err}")
endif()

foreach(command_name
			qfw-activate
			defw-python
		qfw-setup
		qfw-srun
		qfw-teardown
		qfw-dirsvc-start
		qfw-service-start)
	if(NOT EXISTS "${QFW_INSTALL_PREFIX}/bin/${command_name}")
		message(FATAL_ERROR "missing installed command: ${command_name}")
	endif()
endforeach()

foreach(service_manager_file
		service-manager/env.sh
		service-manager/qfw-qpm.env
		service-manager/systemd/qfw-dirsvc@.service
		service-manager/systemd/qfw-qpm@.service)
	if(NOT EXISTS "${QFW_INSTALL_PREFIX}/share/qfw/${service_manager_file}")
		message(FATAL_ERROR
			"missing installed service-manager file: ${service_manager_file}")
	endif()
endforeach()
file(READ
	"${QFW_INSTALL_PREFIX}/share/qfw/service-manager/systemd/qfw-dirsvc@.service"
	dirsvc_unit_text)
string(FIND "${dirsvc_unit_text}" "QFW_DIRSVC_NAME"
	dirsvc_unit_name_env_index)
string(FIND "${dirsvc_unit_text}" "--name" dirsvc_unit_name_arg_index)
string(FIND "${dirsvc_unit_text}"
	"qfw-dirsvc-start --site-config \"\${QFW_SITE_CONFIG}\""
	dirsvc_unit_site_config_index)
if(NOT dirsvc_unit_name_env_index EQUAL -1 OR
   NOT dirsvc_unit_name_arg_index EQUAL -1 OR
   dirsvc_unit_site_config_index EQUAL -1)
	message(FATAL_ERROR
		"QFw dirsvc service-manager unit overrides site directory settings")
endif()

foreach(example_file
		examples/README.md
		examples/qfw_example_common.sh
		examples/qfw_iqm_chem_site_run.sh
		examples/qfw_long_running_qpm.batch
		examples/qfw_long_running_qpm.sh
		examples/qfw_mpi_smoke.sh
		examples/qfw_mpi_smoke_services.yaml
		examples/qfw_qiskit_simple.sh
		examples/qfw_shim_smoke.sh
		examples/qfw_shim_device_access.yaml
		examples/qfw_shim_smoke_services.yaml
		examples/qfw_slurm_driver.sh
		examples/qfw_supermarq.batch
		examples/tests/test_init_qfw.py)
	if(NOT EXISTS "${QFW_INSTALL_PREFIX}/share/qfw/${example_file}")
		message(FATAL_ERROR
			"missing installed example file: ${example_file}")
	endif()
endforeach()

foreach(custom_manifest_wrapper
		qfw_mpi_smoke.sh
		qfw_shim_smoke.sh)
	file(READ
		"${QFW_INSTALL_PREFIX}/share/qfw/examples/${custom_manifest_wrapper}"
		custom_manifest_wrapper_text)
	string(FIND "${custom_manifest_wrapper_text}" "--services-config"
		legacy_services_config_index)
	string(FIND "${custom_manifest_wrapper_text}" "--load-modules"
		legacy_load_modules_index)
	string(FIND "${custom_manifest_wrapper_text}"
		"qfw_example_setup_local_services" local_services_helper_index)
	if(NOT legacy_services_config_index EQUAL -1 OR
	   NOT legacy_load_modules_index EQUAL -1 OR
	   local_services_helper_index EQUAL -1)
		message(FATAL_ERROR
			"installed custom-manifest wrapper uses legacy runtime flags: "
			"${custom_manifest_wrapper}")
	endif()
endforeach()

file(READ
	"${QFW_INSTALL_PREFIX}/share/qfw/service-manager/systemd/qfw-qpm@.service"
	qpm_unit_text)
string(FIND "${qpm_unit_text}"
	"EnvironmentFile=/etc/openqse/qfw/services/qpm/%i.env"
	qpm_unit_env_file_index)
string(FIND "${qpm_unit_text}" "--site-config \"\${QFW_SITE_CONFIG}\""
	qpm_unit_site_config_index)
string(FIND "${qpm_unit_text}" "--service-manifest"
	qpm_unit_manifest_override_index)
string(FIND "${qpm_unit_text}" "--service-runtime-config"
	qpm_unit_runtime_override_index)
string(FIND "${qpm_unit_text}" "--device-access-config"
	qpm_unit_device_override_index)
if(qpm_unit_env_file_index EQUAL -1 OR qpm_unit_site_config_index EQUAL -1 OR
	NOT qpm_unit_manifest_override_index EQUAL -1 OR
	NOT qpm_unit_runtime_override_index EQUAL -1 OR
	NOT qpm_unit_device_override_index EQUAL -1)
	message(FATAL_ERROR "QFw QPM service-manager unit violates site-config contract")
endif()

set(qfw_run_base "${QFW_BINARY_DIR}/install-runtime-smoke")
file(REMOVE_RECURSE "${qfw_run_base}")
file(MAKE_DIRECTORY "${qfw_run_base}")
set(fake_module_dir "${qfw_run_base}/fake-python")
set(fake_service_script "${qfw_run_base}/fake_defwp_service.py")
set(plain_tcp_script "${qfw_run_base}/plain_tcp_listener.py")
file(MAKE_DIRECTORY "${fake_module_dir}")
file(WRITE "${fake_module_dir}/defw.py"
"import json
import os


class DirectoryClient:
    def _records(self):
        if os.environ.get('QFW_FAKE_NO_LISTEN') == '1':
            raise RuntimeError('fake endpoint is not ready')
        path = os.environ.get('QFW_FAKE_DIRSVC_REGISTRY')
        if not path or not os.path.exists(path):
            return []
        with open(path, 'r', encoding='utf-8') as stream:
            return json.load(stream)

    def query_directory(self, include_inactive=False):
        return self._records()

    def query(self):
        return self._records()

    def resolve_services(self, **filters):
        service_id = filters.get('service_id')
        records = self._records()
        if not service_id:
            return records
        matched = []
        for record in records:
            service = record.get('service_record', record)
            if service.get('service_id') == service_id:
                matched.append(record)
        return matched

    def resolve_service(self, **filters):
        return self.resolve_services(**filters)


class EndpointClient:
    def is_ready(self):
        return os.environ.get('QFW_FAKE_NO_LISTEN') != '1'


def connect_to_binding(_record):
    return DirectoryClient()


def connect_to_endpoint(_endpoint, _binding=None):
    return EndpointClient()
")
file(WRITE "${fake_service_script}"
"import json
import os
import signal
import socket
import time

running = True


def stop(_signum, _frame):
    global running
    running = False


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

listen_socket = None
listen_port = int(os.environ.get('DEFW_LISTEN_PORT') or '0')
if listen_port > 0 and os.environ.get('QFW_FAKE_NO_LISTEN') != '1':
    listen_host = os.environ.get('DEFW_PARENT_HOSTNAME') or '127.0.0.1'
    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_socket.bind((listen_host, listen_port))
    listen_socket.listen(1)
    listen_socket.settimeout(0.1)

registry = os.environ.get('QFW_FAKE_DIRSVC_REGISTRY')
ready_file = os.environ.get('QFW_SERVICE_READY_FILE')
service_id = (
    os.environ.get('QFW_QPM_SERVICE_ID') or
    os.environ.get('DEFW_AGENT_NAME') or
    ''
)
delay = float(os.environ.get('QFW_FAKE_REGISTRATION_DELAY_SECONDS', '0'))
registered = False
started = time.monotonic()

while running:
    if registry and service_id and not registered and \
            time.monotonic() - started >= delay:
        records = []
        if os.path.exists(registry):
            with open(registry, 'r', encoding='utf-8') as stream:
                records = json.load(stream)
        records.append({
            'service_record': {
                'service_id': service_id,
                'service_name': 'QPM',
                'service_type': 'qfw.qpm',
            },
            'selected_binding': {
                'binding_name': 'execution',
            },
        })
        tmp_path = registry + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as stream:
            json.dump(records, stream)
        os.replace(tmp_path, registry)
        registered = True
        if ready_file:
            with open(ready_file, 'w', encoding='utf-8') as stream:
                json.dump({'ready': True, 'service_id': service_id}, stream)
                stream.write('\\n')
    if listen_socket is not None:
        try:
            conn, _addr = listen_socket.accept()
            conn.close()
        except socket.timeout:
            pass
    time.sleep(0.1)
if listen_socket is not None:
    listen_socket.close()
")
file(WRITE "${plain_tcp_script}"
"import os
import signal
import socket
import sys
import time

running = True


def stop(_signum, _frame):
    global running
    running = False


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

host = sys.argv[1]
port = int(sys.argv[2])
ready = sys.argv[3]
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((host, port))
sock.listen(1)
sock.settimeout(0.1)
with open(ready, 'w', encoding='utf-8') as stream:
    stream.write('ready\\n')
while running:
    try:
        conn, _addr = sock.accept()
    except socket.timeout:
        continue
    conn.close()
sock.close()
")

file(WRITE "${QFW_INSTALL_PREFIX}/bin/defwp"
"#!/usr/bin/env bash
set -euo pipefail
if [[ \"\${1:-}\" == \"--py-version\" ]]; then
	'${QFW_PYTHON}' -c 'import sys; print(f\"{sys.version_info[0]}.{sys.version_info[1]}.0\")'
	exit 0
fi
if [[ \"\${DEFW_SHELL_TYPE:-}\" == \"daemon\" ]]; then
	exec '${QFW_PYTHON}' '${fake_service_script}'
fi
exit 0
")
file(WRITE "${QFW_INSTALL_PREFIX}/bin/defwp-wrapper"
"#!/usr/bin/env bash
set -euo pipefail
if [[ \"\${DEFW_SHELL_TYPE:-}\" == \"daemon\" ]]; then
	exec '${QFW_PYTHON}' '${fake_service_script}'
fi
if [[ -n \"\${DEFW_APP_SMOKE_OUTPUT:-}\" ]]; then
	{
		echo wrapper
		printf '%s\n' \"\$@\"
	} >> \"\${DEFW_APP_SMOKE_OUTPUT}\"
fi
exec '${QFW_PYTHON}' \"\$@\"
")
file(CHMOD
	"${QFW_INSTALL_PREFIX}/bin/defwp"
	"${QFW_INSTALL_PREFIX}/bin/defwp-wrapper"
	PERMISSIONS
		OWNER_READ OWNER_WRITE OWNER_EXECUTE
		GROUP_READ GROUP_EXECUTE
		WORLD_READ WORLD_EXECUTE)

execute_process(
	COMMAND "${QFW_BASH}" -c
		"set -e
		export PS1='original> '
		export DEFW_LOAD_NO_INIT=api_existing_helper
		source '${QFW_INSTALL_PREFIX}/bin/qfw-activate'
		test \"\${PS1}\" = '(qfw) original> '
		declare -F qfw-deactivate >/dev/null
		! declare -F qfw_deactivate >/dev/null
		test \"\${QFW_PREFIX}\" = '${QFW_INSTALL_PREFIX}'
		test \"\${QFW_BIN_PATH}\" = '${QFW_INSTALL_PREFIX}/bin'
		test \"\${QFW_LIBEXEC_DIR}\" = '${QFW_INSTALL_PREFIX}/libexec/qfw'
		test \"\${QFW_SHARE_DIR}\" = '${QFW_INSTALL_PREFIX}/share/qfw'
		test \"\${DEFW_PREFIX}\" = '${QFW_INSTALL_PREFIX}'
		test \"\${DEFW_LOAD_NO_INIT}\" = 'api_existing_helper,api_qpm_common'
		command -v qfw-setup >/dev/null
		export DEFW_ONLY_LOAD_MODULE=api_qpm_common
		'${QFW_PYTHON}' -c 'import defw, qfw_runtime, importlib.util; assert importlib.util.find_spec(\"qfw_qiskit\") is not None'
		qfw-deactivate
		test \"\${PS1}\" = 'original> '
		test -z \"\${QFW_PREFIX+x}\"
		test \"\${DEFW_LOAD_NO_INIT}\" = api_existing_helper"
	RESULT_VARIABLE activation_rc)
	if(NOT activation_rc EQUAL 0)
		message(FATAL_ERROR "QFw install activation smoke failed")
	endif()

	set(qfw_activation_venv "${qfw_run_base}/activation-venv")
	set(qfw_switch_venv "${qfw_run_base}/switch-venv")
	foreach(qfw_fake_venv IN ITEMS
			"${qfw_activation_venv}"
			"${qfw_switch_venv}")
		file(MAKE_DIRECTORY "${qfw_fake_venv}/bin")
		file(WRITE "${qfw_fake_venv}/bin/activate"
			"#!/usr/bin/env bash\n"
			"if declare -F deactivate >/dev/null 2>&1; then\n"
			"\tdeactivate nondestructive\n"
			"fi\n"
			"deactivate() {\n"
			"\tif [[ -n \"\${_OLD_VIRTUAL_PATH+x}\" ]]; then\n"
			"\t\texport PATH=\"\${_OLD_VIRTUAL_PATH}\"\n"
			"\t\tunset _OLD_VIRTUAL_PATH\n"
			"\tfi\n"
			"\tunset VIRTUAL_ENV\n"
			"\tif [[ -n \"\${_OLD_VIRTUAL_PS1+x}\" ]]; then\n"
			"\t\tPS1=\"\${_OLD_VIRTUAL_PS1}\"\n"
			"\t\tunset _OLD_VIRTUAL_PS1\n"
			"\tfi\n"
			"\tif [[ \"\${1:-}\" != \"nondestructive\" ]]; then\n"
			"\t\tunset -f deactivate\n"
			"\tfi\n"
			"}\n"
			"_OLD_VIRTUAL_PATH=\"\${PATH:-}\"\n"
			"export VIRTUAL_ENV='${qfw_fake_venv}'\n"
			"export PATH=\"\${VIRTUAL_ENV}/bin:\${PATH:-}\"\n"
			"if [[ -z \"\${VIRTUAL_ENV_DISABLE_PROMPT:-}\" ]]; then\n"
			"\t_OLD_VIRTUAL_PS1=\"\${PS1-}\"\n"
			"\tPS1=\"(fake-venv) \${PS1-}\"\n"
			"fi\n")
		file(WRITE "${qfw_fake_venv}/bin/python"
			"#!/usr/bin/env bash\n"
			"exec '${QFW_PYTHON}' \"\$@\"\n")
		file(CHMOD
			"${qfw_fake_venv}/bin/activate"
			"${qfw_fake_venv}/bin/python"
			PERMISSIONS
				OWNER_READ OWNER_WRITE OWNER_EXECUTE
				GROUP_READ GROUP_EXECUTE
				WORLD_READ WORLD_EXECUTE)
	endforeach()

	execute_process(
		COMMAND "${QFW_BASH}" -c
			"set -e
			export PS1='original> '
			source '${qfw_activation_venv}/bin/activate'
			test \"\${PS1}\" = '(fake-venv) original> '
			source '${QFW_INSTALL_PREFIX}/bin/qfw-activate'
			test \"\${PS1}\" = '(qfw) (fake-venv) original> '
			test \"\${VIRTUAL_ENV}\" = '${qfw_activation_venv}'
			test \"\$(command -v python)\" = '${qfw_activation_venv}/bin/python'
			test \"\${QFW_PREFIX}\" = '${QFW_INSTALL_PREFIX}'
			qfw-deactivate
			test \"\${PS1}\" = '(fake-venv) original> '
			test \"\${VIRTUAL_ENV}\" = '${qfw_activation_venv}'

			source '${QFW_INSTALL_PREFIX}/bin/qfw-activate' --venv '${qfw_switch_venv}' 2> '${qfw_run_base}/qfw-activate-prompt-switch.err'
			test \"\${PS1}\" = '(qfw) original> '
			test -z \"\${_OLD_VIRTUAL_PS1+x}\"
			test \"\${VIRTUAL_ENV}\" = '${qfw_switch_venv}'
			grep -q 'switching virtual environment' '${qfw_run_base}/qfw-activate-prompt-switch.err'
			qfw-deactivate
			test \"\${PS1}\" = 'original> '
			test \"\${VIRTUAL_ENV}\" = '${qfw_switch_venv}'
			deactivate
			test \"\${PS1}\" = 'original> '
			test -z \"\${VIRTUAL_ENV+x}\"

			source '${QFW_INSTALL_PREFIX}/bin/qfw-activate' --venv '${qfw_activation_venv}'
			test \"\${PS1}\" = '(qfw) original> '
			test -z \"\${_OLD_VIRTUAL_PS1+x}\"
			test \"\${VIRTUAL_ENV}\" = '${qfw_activation_venv}'
			test \"\$(command -v python)\" = '${qfw_activation_venv}/bin/python'
			test \"\${QFW_PREFIX}\" = '${QFW_INSTALL_PREFIX}'
			qfw-deactivate
			test \"\${PS1}\" = 'original> '
			test \"\${VIRTUAL_ENV}\" = '${qfw_activation_venv}'

			source '${QFW_INSTALL_PREFIX}/bin/qfw-activate' --venv '${qfw_switch_venv}' 2> '${qfw_run_base}/qfw-activate-switch.err'
			test \"\${PS1}\" = '(qfw) original> '
			test -z \"\${_OLD_VIRTUAL_PS1+x}\"
			test \"\${VIRTUAL_ENV}\" = '${qfw_switch_venv}'
			test \"\$(command -v python)\" = '${qfw_switch_venv}/bin/python'
			grep -q 'switching virtual environment' '${qfw_run_base}/qfw-activate-switch.err'
			qfw-deactivate
			test \"\${PS1}\" = 'original> '
			test \"\${VIRTUAL_ENV}\" = '${qfw_switch_venv}'
			deactivate
			test -z \"\${VIRTUAL_ENV+x}\"

			source '${QFW_INSTALL_PREFIX}/bin/qfw-activate' --venv='${qfw_activation_venv}'
			test \"\${PS1}\" = '(qfw) original> '
			test \"\${VIRTUAL_ENV}\" = '${qfw_activation_venv}'
			qfw-deactivate
			test \"\${PS1}\" = 'original> '
			deactivate"
		RESULT_VARIABLE activation_venv_rc)
	if(NOT activation_venv_rc EQUAL 0)
		message(FATAL_ERROR "QFw install activation venv smoke failed")
	endif()

	execute_process(
		COMMAND "${QFW_BASH}" -c
		"set -e
		unset QFW_PATH
		source '${QFW_INSTALL_PREFIX}/bin/qfw-activate'
		test -z \"\${QFW_PATH+x}\"
		source '${QFW_INSTALL_PREFIX}/share/qfw/examples/qfw_example_common.sh'
		qfw_example_require_runtime
		test \"\$(qfw_example_examples_dir)\" = '${QFW_INSTALL_PREFIX}/share/qfw/examples'
		test \"\$(qfw_example_path tests/test_init_qfw.py)\" = '${QFW_INSTALL_PREFIX}/share/qfw/examples/tests/test_init_qfw.py'
		export QFW_RUN_BASE_DIR='${qfw_run_base}'
		qfw_example_setup --profile local --dry-run --run-id install-example-helper
		qfw_example_teardown
		qfw-deactivate"
	RESULT_VARIABLE example_helper_rc)
if(NOT example_helper_rc EQUAL 0)
	message(FATAL_ERROR "QFw installed example helper smoke failed")
endif()

execute_process(
	COMMAND "${QFW_BASH}" -c
		"set -e
		source '${QFW_INSTALL_PREFIX}/bin/qfw-activate'
		source '${QFW_INSTALL_PREFIX}/share/qfw/examples/qfw_example_common.sh'
		export QFW_RUN_BASE_DIR='${qfw_run_base}'
		QFW_EXAMPLE_NAME=custom-manifest-smoke
		runtime_config=\$(qfw_example_make_local_runtime_config '${QFW_INSTALL_PREFIX}/share/qfw/examples/qfw_mpi_smoke_services.yaml' mpi-smoke)
		test -f \"\${runtime_config}\"
		grep -q 'qfw_mpi_smoke_services.yaml' \"\${runtime_config}\"
		qfw-setup --runtime-config \"\${runtime_config}\" --dry-run --run-id custom-manifest-example
		grep -q 'qfw_mpi_smoke_services.yaml' '${qfw_run_base}/custom-manifest-example/state/runtime-state.json'
		grep -q 'mpi-smoke' '${qfw_run_base}/custom-manifest-example/state/runtime-state.json'
		qfw-teardown --run-dir '${qfw_run_base}/custom-manifest-example'
		qfw-deactivate"
	RESULT_VARIABLE custom_manifest_example_rc)
if(NOT custom_manifest_example_rc EQUAL 0)
	message(FATAL_ERROR
		"QFw installed custom-manifest example smoke failed")
endif()

execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"QFW_RUN_BASE_DIR=${qfw_run_base}"
		"${QFW_INSTALL_PREFIX}/bin/qfw-setup"
			--profile local
			--dry-run
			--run-id install-smoke
	RESULT_VARIABLE setup_rc)
if(NOT setup_rc EQUAL 0)
	message(FATAL_ERROR "QFw install qfw-setup dry-run failed")
endif()

set(installed_state "${qfw_run_base}/install-smoke/state/runtime-state.json")
if(NOT EXISTS "${installed_state}")
	message(FATAL_ERROR "QFw install qfw-setup did not write runtime state")
endif()
file(READ "${installed_state}" installed_state_text)
string(FIND "${installed_state_text}" "QFW_QPM_RESOLVER_SCOPE_ORDER" resolver_index)
if(resolver_index EQUAL -1)
	message(FATAL_ERROR "QFw install runtime state missed resolver order")
endif()
string(FIND "${installed_state_text}" "\"setup_complete\": true" complete_index)
if(complete_index EQUAL -1)
	message(FATAL_ERROR "QFw install dry-run setup did not mark complete state")
endif()

set(prefix_site "${qfw_run_base}/prefix-site.yaml")
file(WRITE "${prefix_site}"
"install:
  qfw-prefix: ${QFW_INSTALL_PREFIX}/site-qfw
  defw-prefix: ${QFW_INSTALL_PREFIX}/site-defw
directory:
  site:
    name: prefix-dirsvc
    endpoint: 127.0.0.1:1
    connect-timeout-seconds: 0
")
execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"QFW_RUN_BASE_DIR=${qfw_run_base}"
		"${QFW_INSTALL_PREFIX}/bin/qfw-setup"
			--site-config "${prefix_site}"
			--runtime-config "${QFW_INSTALL_PREFIX}/share/qfw/config/runtime.yaml"
			--dry-run
			--run-id prefix-smoke
	RESULT_VARIABLE prefix_setup_rc)
if(NOT prefix_setup_rc EQUAL 0)
	message(FATAL_ERROR "QFw install qfw-setup prefix dry-run failed")
endif()
file(READ "${qfw_run_base}/prefix-smoke/state/runtime-state.json" prefix_state_text)
string(FIND "${prefix_state_text}" "${QFW_INSTALL_PREFIX}/site-qfw" prefix_qfw_index)
string(FIND "${prefix_state_text}" "${QFW_INSTALL_PREFIX}/site-defw" prefix_defw_index)
if(prefix_qfw_index EQUAL -1 OR prefix_defw_index EQUAL -1)
	message(FATAL_ERROR "QFw install runtime state ignored site install prefixes")
endif()

execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"QFW_RUN_BASE_DIR=${qfw_run_base}"
		"${QFW_INSTALL_PREFIX}/bin/qfw-setup"
			--profile hybrid
			--dry-run
			--run-id hybrid-smoke
	RESULT_VARIABLE hybrid_setup_rc)
if(NOT hybrid_setup_rc EQUAL 0)
	message(FATAL_ERROR "QFw install qfw-setup hybrid dry-run failed")
endif()
file(READ "${qfw_run_base}/hybrid-smoke/state/runtime-state.json" hybrid_state_text)
string(FIND "${hybrid_state_text}" "\"scope\": \"allocation-local\"" hybrid_local_index)
string(FIND "${hybrid_state_text}" "\"scope\": \"site\"" hybrid_site_index)
if(hybrid_local_index EQUAL -1 OR hybrid_site_index EQUAL -1)
	message(FATAL_ERROR "QFw hybrid runtime state missed directory requirements")
endif()

set(app_marker "${qfw_run_base}/app-marker.txt")
set(wrapper_marker "${qfw_run_base}/wrapper-marker.txt")
set(app_path "${qfw_run_base}/app.py")
file(WRITE "${app_path}"
"import os
with open(os.environ['QFW_APP_MARKER'], 'w', encoding='utf-8') as stream:
    stream.write(os.environ.get('QFW_RUN_ID', '') + '\\n')
    stream.write(os.environ.get('QFW_QPM_RESOLVER_SCOPE_ORDER', '') + '\\n')
")

execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"QFW_RUN_BASE_DIR=${qfw_run_base}"
			"QFW_APP_MARKER=${app_marker}"
			"DEFW_APP_SMOKE_OUTPUT=${wrapper_marker}"
			"${QFW_INSTALL_PREFIX}/bin/qfw-srun"
				--run-dir "${qfw_run_base}/install-smoke"
				"${app_path}"
	RESULT_VARIABLE srun_rc)
if(NOT srun_rc EQUAL 0)
	message(FATAL_ERROR "QFw install qfw-srun smoke failed")
endif()
if(NOT EXISTS "${app_marker}")
	message(FATAL_ERROR "QFw install qfw-srun did not run application")
endif()
file(READ "${app_marker}" app_output)
string(FIND "${app_output}" "install-smoke" app_run_index)
if(app_run_index EQUAL -1)
	message(FATAL_ERROR "QFw install qfw-srun missed runtime environment")
endif()
if(NOT EXISTS "${wrapper_marker}")
	message(FATAL_ERROR "QFw install defw-python wrapper was not invoked")
endif()

set(bad_site "${qfw_run_base}/bad-site.yaml")
set(bad_run_dir "${qfw_run_base}/bad-setup")
set(bad_app_marker "${qfw_run_base}/bad-app-marker.txt")
set(bad_app_path "${qfw_run_base}/bad-app.py")
file(WRITE "${bad_site}"
"install:
  qfw-prefix: ${QFW_INSTALL_PREFIX}
  defw-prefix: ${QFW_INSTALL_PREFIX}
directory:
  site:
    name: bad-dirsvc
    endpoint: 127.0.0.1:1
    connect-timeout-seconds: 0
")
file(WRITE "${bad_app_path}"
"import os
with open(os.environ['QFW_BAD_APP_MARKER'], 'w', encoding='utf-8') as stream:
    stream.write('ran\\n')
")
execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"QFW_RUN_BASE_DIR=${qfw_run_base}"
		"${QFW_INSTALL_PREFIX}/bin/qfw-setup"
			--site-config "${bad_site}"
			--runtime-config "${QFW_INSTALL_PREFIX}/share/qfw/config/runtime.yaml"
			--run-id bad-setup
			--run-dir "${bad_run_dir}"
	RESULT_VARIABLE bad_setup_rc)
if(bad_setup_rc EQUAL 0)
	message(FATAL_ERROR "QFw setup succeeded with an unreachable directory")
endif()
execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"QFW_BAD_APP_MARKER=${bad_app_marker}"
		"${QFW_INSTALL_PREFIX}/bin/qfw-srun"
			--run-dir "${bad_run_dir}"
			"${bad_app_path}"
	RESULT_VARIABLE bad_srun_rc)
if(bad_srun_rc EQUAL 0 OR EXISTS "${bad_app_marker}")
	message(FATAL_ERROR "QFw qfw-srun launched from incomplete setup state")
endif()

qfw_free_port(tcp_listener_port)
set(tcp_site "${qfw_run_base}/tcp-listener-site.yaml")
set(tcp_run_dir "${qfw_run_base}/tcp-listener-setup")
set(tcp_ready_file "${qfw_run_base}/tcp-listener-ready.txt")
file(WRITE "${tcp_site}"
"install:
  qfw-prefix: ${QFW_INSTALL_PREFIX}
  defw-prefix: ${QFW_INSTALL_PREFIX}
directory:
  site:
    name: tcp-listener
    endpoint: 127.0.0.1:${tcp_listener_port}
    connect-timeout-seconds: 1
")
execute_process(
	COMMAND "${QFW_BASH}" -c
		"set -e
		'${QFW_PYTHON}' '${plain_tcp_script}' 127.0.0.1 '${tcp_listener_port}' '${tcp_ready_file}' &
		listener_pid=\$!
		for _attempt in \$(seq 1 50); do
			[[ -f '${tcp_ready_file}' ]] && break
			sleep 0.1
		done
		[[ -f '${tcp_ready_file}' ]]
		set +e
		QFW_RUN_BASE_DIR='${qfw_run_base}' '${QFW_INSTALL_PREFIX}/bin/qfw-setup' --site-config '${tcp_site}' --runtime-config '${QFW_INSTALL_PREFIX}/share/qfw/config/runtime.yaml' --run-id tcp-listener-setup --run-dir '${tcp_run_dir}'
		setup_rc=\$?
		kill -TERM \${listener_pid} 2>/dev/null
		wait \${listener_pid} 2>/dev/null
		exit \${setup_rc}"
	RESULT_VARIABLE tcp_listener_setup_rc)
if(NOT tcp_listener_setup_rc EQUAL 0)
	message(FATAL_ERROR "QFw setup rejected a reachable directory listener")
endif()
if(NOT EXISTS "${tcp_run_dir}/state/runtime-state.json")
	message(FATAL_ERROR "QFw setup did not write TCP listener runtime state")
endif()

qfw_free_port(dirsvc_port)
qfw_free_port(service_port)
set(service_smoke_dir "${qfw_run_base}/service-smoke")
set(service_registry "${service_smoke_dir}/registry.json")
set(service_site "${service_smoke_dir}/site.yaml")
file(MAKE_DIRECTORY "${service_smoke_dir}")
file(WRITE "${service_site}"
"install:
  qfw-prefix: ${QFW_INSTALL_PREFIX}
  defw-prefix: ${QFW_INSTALL_PREFIX}
directory:
  site:
    name: live-dirsvc
    endpoint: 127.0.0.1:${dirsvc_port}
    connect-timeout-seconds: 5
")
qfw_free_port(site_default_dirsvc_port)
set(site_defaults_dir "${qfw_run_base}/site-defaults-smoke")
set(site_defaults_site "${site_defaults_dir}/site.yaml")
file(MAKE_DIRECTORY "${site_defaults_dir}")
file(WRITE "${site_defaults_site}"
"install:
  qfw-prefix: ${QFW_INSTALL_PREFIX}
  defw-prefix: ${QFW_INSTALL_PREFIX}
directory:
  site:
    name: site-default-dirsvc
    endpoint: 0.0.0.0:${site_default_dirsvc_port}
    connect-timeout-seconds: 7
")
execute_process(
	COMMAND
		"${QFW_INSTALL_PREFIX}/bin/qfw-dirsvc-start"
			--site-config "${site_defaults_site}"
			--run-dir "${site_defaults_dir}"
			--dry-run
			--pid-file "${site_defaults_dir}/dirsvc.pid"
			--ready-file "${site_defaults_dir}/dirsvc-ready.json"
	RESULT_VARIABLE site_defaults_dirsvc_rc)
if(NOT site_defaults_dirsvc_rc EQUAL 0)
	message(FATAL_ERROR
		"QFw qfw-dirsvc-start site defaults dry-run failed")
endif()
file(READ "${site_defaults_dir}/dirsvc-ready.json" site_dirsvc_ready_text)
string(FIND "${site_dirsvc_ready_text}"
	"\"name\": \"site-default-dirsvc\"" site_dirsvc_name_index)
string(FIND "${site_dirsvc_ready_text}"
	"\"endpoint\": \"0.0.0.0:${site_default_dirsvc_port}\""
	site_dirsvc_endpoint_index)
string(FIND "${site_dirsvc_ready_text}"
	"\"startup_timeout\": 7" site_dirsvc_timeout_index)
if(site_dirsvc_name_index EQUAL -1 OR
   site_dirsvc_endpoint_index EQUAL -1 OR
   site_dirsvc_timeout_index EQUAL -1)
	message(FATAL_ERROR
		"QFw qfw-dirsvc-start ignored site-configured directory defaults")
endif()
execute_process(
	COMMAND
		"${QFW_INSTALL_PREFIX}/bin/qfw-service-start"
			--service-id site-default-qpm
			--module svc_nwqsim_qpm
			--site-config "${site_defaults_site}"
			--run-dir "${site_defaults_dir}"
			--dry-run
			--pid-file "${site_defaults_dir}/site-default-qpm.pid"
			--ready-file "${site_defaults_dir}/site-default-qpm-ready.json"
	RESULT_VARIABLE site_defaults_service_rc)
if(NOT site_defaults_service_rc EQUAL 0)
	message(FATAL_ERROR
		"QFw qfw-service-start site defaults dry-run failed")
endif()
file(READ
	"${site_defaults_dir}/site-default-qpm-ready.json"
	site_service_ready_text)
string(FIND "${site_service_ready_text}"
	"\"dirsvc_name\": \"site-default-dirsvc\"" site_service_name_index)
string(FIND "${site_service_ready_text}"
	"\"dirsvc_endpoint\": \"0.0.0.0:${site_default_dirsvc_port}\""
	site_service_endpoint_index)
string(FIND "${site_service_ready_text}"
	"\"startup_timeout\": 7" site_service_timeout_index)
if(site_service_name_index EQUAL -1 OR
   site_service_endpoint_index EQUAL -1 OR
   site_service_timeout_index EQUAL -1)
	message(FATAL_ERROR
		"QFw qfw-service-start ignored site-configured directory defaults")
endif()
execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"PYTHONPATH=${fake_module_dir}"
		"QFW_RUN_BASE_DIR=${qfw_run_base}"
		"${QFW_INSTALL_PREFIX}/bin/qfw-dirsvc-start"
			--background
			--run-dir "${service_smoke_dir}"
			--name live-dirsvc
			--listen-port "${dirsvc_port}"
			--timeout 5
			--pid-file "${service_smoke_dir}/dirsvc.pid"
			--ready-file "${service_smoke_dir}/dirsvc-ready.json"
	RESULT_VARIABLE dirsvc_rc)
if(NOT dirsvc_rc EQUAL 0)
	message(FATAL_ERROR "QFw install qfw-dirsvc-start readiness failed")
endif()
if(NOT EXISTS "${service_smoke_dir}/dirsvc-ready.json")
	message(FATAL_ERROR "QFw dirsvc readiness file was not written")
endif()

execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"PYTHONPATH=${fake_module_dir}"
		"QFW_FAKE_DIRSVC_REGISTRY=${service_registry}"
		"QFW_FAKE_REGISTRATION_DELAY_SECONDS=0.5"
		"QFW_RUN_BASE_DIR=${qfw_run_base}"
		"${QFW_INSTALL_PREFIX}/bin/qfw-service-start"
			--background
			--service-id smoke-qpm
			--module svc_nwqsim_qpm
			--site-config "${service_site}"
			--run-dir "${service_smoke_dir}"
			--listen-port "${service_port}"
			--timeout 5
			--pid-file "${service_smoke_dir}/smoke-qpm.pid"
			--ready-file "${service_smoke_dir}/smoke-qpm-ready.json"
	RESULT_VARIABLE service_rc)
if(NOT service_rc EQUAL 0)
	message(FATAL_ERROR "QFw install qfw-service-start readiness failed")
endif()
if(NOT EXISTS "${service_smoke_dir}/smoke-qpm-ready.json")
	message(FATAL_ERROR "QFw service readiness file was not written")
endif()
file(READ "${service_smoke_dir}/smoke-qpm-ready.json" service_ready_text)
string(FIND "${service_ready_text}" "\"register_with_dirsvc\": true" service_register_index)
if(service_register_index EQUAL -1)
	message(FATAL_ERROR "QFw service readiness missed registration state")
endif()

qfw_free_port(wrong_service_port)
set(wrong_service_dir "${qfw_run_base}/wrong-service-smoke")
set(wrong_service_registry "${wrong_service_dir}/registry.json")
file(MAKE_DIRECTORY "${wrong_service_dir}")
file(WRITE "${wrong_service_registry}"
"[
  {
    \"service_record\": {
      \"service_id\": \"other-qpm\",
      \"service_name\": \"QPM\",
      \"service_type\": \"qfw.qpm\"
    },
    \"selected_binding\": {
      \"binding_name\": \"execution\"
    }
  }
]
")
execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"PYTHONPATH=${fake_module_dir}"
		"QFW_FAKE_DIRSVC_REGISTRY=${wrong_service_registry}"
		"QFW_FAKE_REGISTRATION_DELAY_SECONDS=10"
		"${QFW_INSTALL_PREFIX}/bin/qfw-service-start"
			--background
			--service-id wanted-qpm
			--module svc_nwqsim_qpm
			--site-config "${service_site}"
			--run-dir "${wrong_service_dir}"
			--listen-port "${wrong_service_port}"
			--timeout 1
			--pid-file "${wrong_service_dir}/wanted-qpm.pid"
			--ready-file "${wrong_service_dir}/wanted-qpm-ready.json"
	RESULT_VARIABLE wrong_service_rc)
if(wrong_service_rc EQUAL 0)
	message(FATAL_ERROR "QFw service readiness accepted another QPM record")
endif()
if(EXISTS "${wrong_service_dir}/wanted-qpm.pid" OR
   EXISTS "${wrong_service_dir}/wanted-qpm-ready.json")
	message(FATAL_ERROR "QFw wrong-service timeout left pid or readiness state")
endif()

qfw_free_port(direct_tcp_port)
set(direct_tcp_dir "${qfw_run_base}/direct-tcp-smoke")
set(direct_tcp_ready "${direct_tcp_dir}/tcp-ready.txt")
file(MAKE_DIRECTORY "${direct_tcp_dir}")
execute_process(
	COMMAND "${QFW_BASH}" -c
		"set -e
		'${QFW_PYTHON}' '${plain_tcp_script}' 127.0.0.1 '${direct_tcp_port}' '${direct_tcp_ready}' &
		listener_pid=\$!
		for _attempt in \$(seq 1 50); do
			[[ -f '${direct_tcp_ready}' ]] && break
			sleep 0.1
		done
		[[ -f '${direct_tcp_ready}' ]]
		set +e
		'${QFW_INSTALL_PREFIX}/bin/qfw-service-start' --background --operation-mode direct --service-id direct-tcp-qpm --module svc_nwqsim_qpm --run-dir '${direct_tcp_dir}' --listen-port '${direct_tcp_port}' --timeout 1 --pid-file '${direct_tcp_dir}/direct-tcp-qpm.pid' --ready-file '${direct_tcp_dir}/direct-tcp-qpm-ready.json'
		service_rc=\$?
		kill -TERM \${listener_pid} 2>/dev/null
		wait \${listener_pid} 2>/dev/null
		if [[ -f '${direct_tcp_dir}/direct-tcp-qpm.pid' ]]; then
			kill -TERM \$(cat '${direct_tcp_dir}/direct-tcp-qpm.pid') 2>/dev/null
		fi
		exit \${service_rc}"
	RESULT_VARIABLE direct_tcp_rc)
if(direct_tcp_rc EQUAL 0)
	message(FATAL_ERROR "QFw direct service readiness accepted raw TCP")
endif()
if(EXISTS "${direct_tcp_dir}/direct-tcp-qpm.pid" OR
   EXISTS "${direct_tcp_dir}/direct-tcp-qpm-ready.json")
	message(FATAL_ERROR "QFw direct raw-TCP timeout left lifecycle state")
endif()

qfw_free_port(direct_not_ready_port)
set(direct_not_ready_dir "${qfw_run_base}/direct-not-ready-smoke")
file(MAKE_DIRECTORY "${direct_not_ready_dir}")
execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"PYTHONPATH=${fake_module_dir}"
		"QFW_FAKE_NO_LISTEN=1"
		"${QFW_INSTALL_PREFIX}/bin/qfw-service-start"
			--background
			--operation-mode direct
			--service-id direct-not-ready-qpm
			--module svc_nwqsim_qpm
			--run-dir "${direct_not_ready_dir}"
			--listen-port "${direct_not_ready_port}"
			--timeout 1
			--pid-file "${direct_not_ready_dir}/direct-not-ready-qpm.pid"
			--ready-file "${direct_not_ready_dir}/direct-not-ready-qpm-ready.json"
	RESULT_VARIABLE direct_not_ready_rc)
if(direct_not_ready_rc EQUAL 0)
	message(FATAL_ERROR "QFw direct service readiness accepted failed is_ready")
endif()
if(EXISTS "${direct_not_ready_dir}/direct-not-ready-qpm.pid" OR
   EXISTS "${direct_not_ready_dir}/direct-not-ready-qpm-ready.json")
	message(FATAL_ERROR "QFw direct failed-readiness timeout left lifecycle state")
endif()
execute_process(
	COMMAND "${QFW_BASH}" -c
		"kill -TERM \$(cat '${service_smoke_dir}/smoke-qpm.pid') \$(cat '${service_smoke_dir}/dirsvc.pid') 2>/dev/null || true")

qfw_free_port(timeout_port)
set(timeout_dir "${qfw_run_base}/timeout-smoke")
file(MAKE_DIRECTORY "${timeout_dir}")
execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"PYTHONPATH=${fake_module_dir}"
		"QFW_FAKE_NO_LISTEN=1"
		"${QFW_INSTALL_PREFIX}/bin/qfw-dirsvc-start"
			--background
			--run-dir "${timeout_dir}"
			--name timeout-dirsvc
			--listen-port "${timeout_port}"
			--timeout 1
			--pid-file "${timeout_dir}/dirsvc.pid"
			--ready-file "${timeout_dir}/dirsvc-ready.json"
	RESULT_VARIABLE timeout_rc)
if(timeout_rc EQUAL 0)
	message(FATAL_ERROR "QFw dirsvc startup succeeded without readiness")
endif()
if(EXISTS "${timeout_dir}/dirsvc.pid" OR EXISTS "${timeout_dir}/dirsvc-ready.json")
	message(FATAL_ERROR "QFw dirsvc timeout left pid or readiness state")
endif()

qfw_free_port(signal_port)
set(signal_dir "${qfw_run_base}/signal-smoke")
file(MAKE_DIRECTORY "${signal_dir}")
execute_process(
	COMMAND "${QFW_BASH}" -c
		"set -e
		PYTHONPATH='${fake_module_dir}' '${QFW_INSTALL_PREFIX}/bin/qfw-dirsvc-start' --run-dir '${signal_dir}' --name signal-dirsvc --listen-port '${signal_port}' --timeout 5 --pid-file '${signal_dir}/dirsvc.pid' --ready-file '${signal_dir}/dirsvc-ready.json' &
		cmd_pid=\$!
		for _attempt in \$(seq 1 50); do
			[[ -f '${signal_dir}/dirsvc-ready.json' ]] && break
			sleep 0.1
		done
		[[ -f '${signal_dir}/dirsvc-ready.json' ]]
		kill -TERM \${cmd_pid}
		set +e
		wait \${cmd_pid}
		set -e
		[[ ! -e '${signal_dir}/dirsvc.pid' ]]
		[[ ! -e '${signal_dir}/dirsvc-ready.json' ]]"
	RESULT_VARIABLE signal_rc)
if(NOT signal_rc EQUAL 0)
	message(FATAL_ERROR "QFw dirsvc foreground signal cleanup failed")
endif()

set(partial_run_dir "${qfw_run_base}/partial-setup")
set(partial_registry "${qfw_run_base}/partial-registry.json")
set(partial_manifest "${qfw_run_base}/partial-services.yaml")
set(partial_runtime "${qfw_run_base}/partial-runtime.yaml")
set(partial_site "${qfw_run_base}/partial-site.yaml")
file(WRITE "${partial_manifest}"
"services:
  - name: first
    module: svc_nwqsim_qpm
  - name: second
")
file(WRITE "${partial_runtime}"
"resolver:
  scope-order:
    - local
local-services:
  start-prte: false
  start-dirsvc: false
  start-qpm: true
  dirsvc:
    name: partial-dirsvc
    bind-host: 127.0.0.1
    port: 1
    connect-timeout-seconds: 1
  service-manifest: ${partial_manifest}
  services:
    - first
    - second
")
file(WRITE "${partial_site}"
"install:
  qfw-prefix: ${QFW_INSTALL_PREFIX}
  defw-prefix: ${QFW_INSTALL_PREFIX}
directory:
  site:
    name: partial-site
    endpoint: 127.0.0.1:1
    connect-timeout-seconds: 1
")
execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"PYTHONPATH=${fake_module_dir}"
		"QFW_FAKE_DIRSVC_REGISTRY=${partial_registry}"
		"QFW_RUN_BASE_DIR=${qfw_run_base}"
		"${QFW_INSTALL_PREFIX}/bin/qfw-setup"
			--site-config "${partial_site}"
			--runtime-config "${partial_runtime}"
			--run-id partial-setup
			--run-dir "${partial_run_dir}"
	RESULT_VARIABLE partial_setup_rc)
if(partial_setup_rc EQUAL 0)
	message(FATAL_ERROR "QFw partial local setup succeeded after service failure")
endif()
execute_process(
	COMMAND "${QFW_BASH}" -c
		"set -e
		test -s '${partial_run_dir}/state/first.pid'
		first_pid=\$(cat '${partial_run_dir}/state/first.pid')
		for _attempt in \$(seq 1 30); do
			if ! kill -0 \${first_pid} 2>/dev/null; then
				exit 0
			fi
			sleep 0.1
		done
		exit 1"
	RESULT_VARIABLE partial_cleanup_rc)
if(NOT partial_cleanup_rc EQUAL 0)
	message(FATAL_ERROR "QFw partial local setup did not clean started services")
endif()

execute_process(
	COMMAND
		"${CMAKE_COMMAND}" -E env
		"QFW_RUN_BASE_DIR=${qfw_run_base}"
		"${QFW_INSTALL_PREFIX}/bin/qfw-teardown"
			--run-dir "${qfw_run_base}/install-smoke"
	RESULT_VARIABLE teardown_rc)
if(NOT teardown_rc EQUAL 0)
	message(FATAL_ERROR "QFw install qfw-teardown failed")
endif()
if(EXISTS "${qfw_run_base}/install-smoke")
	message(FATAL_ERROR "QFw install qfw-teardown did not clean run state")
endif()
