#!/usr/bin/env bash
# Quantum Framework (QFw) environment

# ---- Base directories ----
BASE_DIR=$QFW_MASTER_SETUP_BASE_DIR
BASE_INSTALL_DIR="${BASE_DIR}/install/TNQVM_MPI_RELEASE"
BASE_BUILD_DIR="${BASE_DIR}/build/TNQVM_MPI_RELEASE"

EXATN_INSTALL_PATH="${BASE_INSTALL_DIR}/exatn"
XACC_INSTALL_PATH="${BASE_INSTALL_DIR}/xacc"
TNQVM_INSTALL_PATH="${BASE_BUILD_DIR}/tnqvm"

HOME_DIR="${HOME}"

# ---- Environment variables ----
export DEFW_PATH="${BASE_DIR}/QFw/DEFw"
export QFW_PATH="${BASE_DIR}/QFw"
export QFW_TMP_PATH="${HOME_DIR}/QFwTmp"
export QFW_SETUP_PATH="${BASE_DIR}/QFw/setup"
export QFW_BIN_PATH="${BASE_DIR}/QFw/bin"
export QFW_ENV_PATH="${BASE_DIR}/QFw/environment"

# ---- PATH setup (prepend semantics) ----
export PATH="${BASE_DIR}/QFw/setup:${PATH}"
export PATH="${BASE_DIR}/QFw/bin:${PATH}"
export PATH="${BASE_DIR}/environment:${PATH}"
export PATH="${BASE_DIR}/QFw/DEFw/src:${PATH}"

# ---- LD_LIBRARY_PATH setup (prepend semantics) ----
export LD_LIBRARY_PATH="${BASE_DIR}/QFw/DEFw/src:${LD_LIBRARY_PATH}"
export LD_LIBRARY_PATH="${EXATN_INSTALL_PATH}/lib:${LD_LIBRARY_PATH}"
export LD_LIBRARY_PATH="${XACC_INSTALL_PATH}/lib:${LD_LIBRARY_PATH}"
export LD_LIBRARY_PATH="${TNQVM_INSTALL_PATH}/plugins:${LD_LIBRARY_PATH}"

echo "Quantum Framework (QFw) environment loaded"

