#!/usr/bin/env bash

qfw_require_tmp_base() {
	if [[ -z "${QFW_TMP_PATH:-}" ]]; then
		echo "ERROR: QFW_TMP_PATH is not set; source qfw_activate first" >&2
		return 1
	fi
	mkdir -p "${QFW_TMP_PATH}"
}

qfw_set_run_tmp_from_id() {
	if [[ -z "${QFW_RUN_ID:-}" ]]; then
		echo "ERROR: QFW_RUN_ID is not set" >&2
		return 1
	fi
	export QFW_RUN_TMP_PATH="${QFW_TMP_PATH}/${QFW_RUN_ID}"
	mkdir -p "${QFW_RUN_TMP_PATH}"
}

qfw_create_run_tmp() {
	qfw_require_tmp_base || return 1
	if [[ -z "${QFW_RUN_ID:-}" ]]; then
		export QFW_RUN_ID="$(date +%Y%m%d-%H%M%S)"
	fi
	qfw_set_run_tmp_from_id || return 1
	printf '%s\n' "${QFW_RUN_ID}" > "${QFW_TMP_PATH}/current"
	printf '%s\n' "${QFW_RUN_ID}" > "${QFW_TMP_PATH}/latest"
}

qfw_use_current_run_tmp() {
	qfw_require_tmp_base || return 1
	if [[ -z "${QFW_RUN_ID:-}" ]]; then
		if [[ ! -f "${QFW_TMP_PATH}/current" ]]; then
			echo "ERROR: no active QFw run found under ${QFW_TMP_PATH}" >&2
			echo "Run qfw_setup.sh first, or export QFW_RUN_ID." >&2
			return 1
		fi
		read -r QFW_RUN_ID < "${QFW_TMP_PATH}/current"
		export QFW_RUN_ID
	fi
	qfw_set_run_tmp_from_id
}

qfw_clear_current_run_tmp() {
	qfw_require_tmp_base || return 1
	if [[ -f "${QFW_TMP_PATH}/current" ]]; then
		local current_id
		read -r current_id < "${QFW_TMP_PATH}/current"
		if [[ "${current_id}" == "${QFW_RUN_ID:-}" ]]; then
			rm -f "${QFW_TMP_PATH}/current"
		fi
	fi
}
