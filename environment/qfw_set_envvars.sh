#!/usr/bin/env bash
# Quantum Framework (QFw) environment

# ---- Base directories ----
BASE_DIR=$QFW_MASTER_SETUP_BASE_DIR
if [[ -z "${QFW_DEP_BUILD_VERSION:-}" ]]; then
	echo "ERROR: QFW_DEP_BUILD_VERSION is not set. Activate QFw through qfw_activate before sourcing qfw_set_envvars.sh." >&2
	return 1 2>/dev/null || exit 1
fi
BASE_INSTALL_DIR="${BASE_DIR}/install/${QFW_DEP_BUILD_VERSION}"
BASE_BUILD_DIR="${BASE_DIR}/build/${QFW_DEP_BUILD_VERSION}"

TNQVM_BASE_INSTALL_DIR="${BASE_INSTALL_DIR}/TNQVM"
TNQVM_BASE_BUILD_DIR="${BASE_BUILD_DIR}/TNQVM"
NWQSIM_BASE_INSTALL_DIR="${BASE_INSTALL_DIR}/NWQSIM"
NWQSIM_BASE_BUILD_DIR="${BASE_BUILD_DIR}/NWQSIM"

EXATN_INSTALL_PATH="${TNQVM_BASE_INSTALL_DIR}/exatn"
XACC_INSTALL_PATH="${TNQVM_BASE_INSTALL_DIR}/xacc"
TNQVM_INSTALL_PATH="${TNQVM_BASE_BUILD_DIR}/tnqvm"

# ---- Environment variables ----
export DEFW_PATH="${BASE_DIR}/QFw/DEFw"
export QFW_PATH="${BASE_DIR}/QFw"
export QFW_SETUP_PATH="${BASE_DIR}/QFw/setup"
export QFW_BIN_PATH="${BASE_DIR}/QFw/bin"
export QFW_ENV_PATH="${BASE_DIR}/QFw/environment"
export TNQVM_BASE_INSTALL_DIR
export TNQVM_BASE_BUILD_DIR
export NWQSIM_BASE_INSTALL_DIR
export NWQSIM_BASE_BUILD_DIR
export DEFW_EXTERNAL_SERVICES_PATH="${QFW_PATH}/services"
export DEFW_EXTERNAL_SERVICE_APIS_PATH="${QFW_PATH}/service-apis"

if [[ -n "${QFW_MASTER_SETUP_TMP_DIR:-}" ]]; then
	QFW_TMP_PATH="${QFW_MASTER_SETUP_TMP_DIR}"
elif [[ "${QFW_RUNTIME_MODE:-frontier}" == "container" ]]; then
	QFW_TMP_PATH="${BASE_DIR}/tmp"
else
	QFW_TMP_PATH="${HOME}/QFwTmp"
fi
export QFW_TMP_PATH
mkdir -p "${QFW_TMP_PATH}"

# ---- PATH setup (prepend semantics) ----
export PATH="${BASE_DIR}/QFw/setup:${PATH}"
export PATH="${BASE_DIR}/QFw/bin:${PATH}"
export PATH="${BASE_DIR}/environment:${PATH}"
export PATH="${BASE_DIR}/QFw/DEFw/src:${PATH}"
export PYTHONPATH="${QFW_PATH}/services:${QFW_PATH}/service-apis${PYTHONPATH+:$PYTHONPATH}"

# ---- LD_LIBRARY_PATH setup (prepend semantics) ----
export LD_LIBRARY_PATH="${BASE_DIR}/QFw/DEFw/src:${LD_LIBRARY_PATH}"
export LD_LIBRARY_PATH="${EXATN_INSTALL_PATH}/lib:${LD_LIBRARY_PATH}"
export LD_LIBRARY_PATH="${XACC_INSTALL_PATH}/lib:${LD_LIBRARY_PATH}"
export LD_LIBRARY_PATH="${TNQVM_INSTALL_PATH}/plugins:${LD_LIBRARY_PATH}"

echo "Quantum Framework (QFw) environment loaded"
