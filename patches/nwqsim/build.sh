#!/bin/bash
set -euo pipefail
set -xe

load_qfw_build_env() {
  local script_dir
  local qfw_root
  local build_env

  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  qfw_root="$(cd "${script_dir}/../.." && pwd)"
  build_env="${qfw_root}/setup/qfw_build_env.sh"

  if [[ -f "${build_env}" ]]; then
    # shellcheck source=/dev/null
    source "${build_env}"
  fi
}

if [[ -z "${QFW_MASTER_SETUP_BASE_DIR:-}" ||
      -z "${QFW_DEP_BUILD_VERSION:-}" ||
      -z "${PYTHON_PATH:-}" ]]; then
  load_qfw_build_env
fi

# -------------------------------
# Build configuration
# -------------------------------
: "${QFW_DEP_BUILD_VERSION:?QFW_DEP_BUILD_VERSION must be set by qfw_configure}"
: "${QFW_MASTER_SETUP_BASE_DIR:?QFW_MASTER_SETUP_BASE_DIR must be set by qfw_configure}"

VERSION=${QFW_DEP_BUILD_VERSION}
CC_BIN=${QFW_MASTER_SETUP_CC:-gcc}
CXX_BIN=${QFW_MASTER_SETUP_CXX:-g++}
HIP_ARCH=${QFW_MASTER_SETUP_HIP_ARCH:-gfx90a}
PYTHON_BIN=${PYTHON_PATH:-$(command -v python3)}
BUILD_JOBS=${QFW_BUILD_JOBS:-${QFW_MASTER_SETUP_BUILD_JOBS:-2}}
if ! [[ "${BUILD_JOBS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "QFW_BUILD_JOBS must be a positive integer, got '${BUILD_JOBS}'" >&2
  exit 1
fi
export CMAKE_BUILD_PARALLEL_LEVEL="${BUILD_JOBS}"

SRC_ROOT=${QFW_MASTER_SETUP_BASE_DIR}/source
BASE_INSTALL_DIR=${QFW_MASTER_SETUP_BASE_DIR}/install/${VERSION}/NWQSIM/
BASE_BUILD_DIR=${QFW_MASTER_SETUP_BASE_DIR}/build/${VERSION}/NWQSIM/

QSRC="${SRC_ROOT}/${VERSION}/NWQSIM"

echo "### Using source root: ${SRC_ROOT}"
echo "### NWQ-Sim root: ${QSRC}"
echo "### Build parallelism: ${BUILD_JOBS}"
sleep 1

echo "#### PATH: $PATH"
echo "#### LD_LIBRARY_PATH: ${LD_LIBRARY_PATH:-}"
sleep 1

# -------------------------------
# Clone repositories
# -------------------------------
mkdir -p "${QSRC}"
cd "${QSRC}"

if [ ! -d NWQ-Sim ]; then
  git clone https://github.com/pnnl/NWQ-Sim.git
  cd "${QSRC}/NWQ-Sim"
  git submodule update --init --recursive
fi

echo "#### CLEANING UP INSTALL DIRECTORY"
mkdir -p "${BASE_INSTALL_DIR}"

echo "#### SETTING UP BUILD DIRECTORIES"
mkdir -p "${BASE_BUILD_DIR}"

echo "#### BUILDING NWQ-Sim: ${QSRC}/NWQ-Sim"

cmake_args=(
  "${QSRC}/NWQ-Sim"
  -DCMAKE_INSTALL_PREFIX="${BASE_INSTALL_DIR}"
  -DCMAKE_C_COMPILER="${CC_BIN}"
  -DCMAKE_CXX_COMPILER="${CXX_BIN}"
  -DPython_EXECUTABLE="${PYTHON_BIN}"
)

if [[ "${QFW_RUNTIME_MODE:-frontier}" == "container" ]]; then
  cmake_args+=(
    -DNWQSIM_ENABLE_CUDA="${QFW_NWQSIM_ENABLE_CUDA:-OFF}"
    -DNWQSIM_ENABLE_HIP="${QFW_NWQSIM_ENABLE_HIP:-OFF}"
  )
else
  export MY_HIP_ARCH="${MY_HIP_ARCH:-${HIP_ARCH}}"
  cmake_args+=(-DHIP_ARCH="${HIP_ARCH}")

  if [[ -n "${QFW_NWQSIM_ENABLE_CUDA:-}" ]]; then
    cmake_args+=(-DNWQSIM_ENABLE_CUDA="${QFW_NWQSIM_ENABLE_CUDA}")
  fi

  if [[ -n "${QFW_NWQSIM_ENABLE_HIP:-}" ]]; then
    cmake_args+=(-DNWQSIM_ENABLE_HIP="${QFW_NWQSIM_ENABLE_HIP}")
  fi
fi

cd "${BASE_BUILD_DIR}"
cmake "${cmake_args[@]}"
make -j "${BUILD_JOBS}"
make install

cp "${BASE_BUILD_DIR}/qasm/nwq_qasm" \
   "${QFW_MASTER_SETUP_BASE_DIR}/QFw/bin/circuit_runner.nwqsim"
