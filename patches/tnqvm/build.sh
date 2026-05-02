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
      -z "${PYTHON_PATH:-}" ||
      -z "${OMPI_DIR:-}" ]]; then
  load_qfw_build_env
fi

export TALSH_GPU=1

find_rocm_root() {
  local candidate
  local path_list

  for path_list in "${ROCM_PATH:-}" "${QFW_MASTER_SETUP_DEV_INSTALL:-}" /opt/rocm; do
    IFS=: read -ra rocm_candidates <<< "${path_list}"
    for candidate in "${rocm_candidates[@]}"; do
      [[ -n "${candidate}" ]] || continue

      if [[ -e "${candidate}/lib/libhipblas.so" ||
            -e "${candidate}/lib64/libhipblas.so" ]]; then
        printf '%s\n' "${candidate}"
        return 0
      fi
    done
  done

  return 1
}

TNQVM_ROCM_BUILD=0
ROCM_ROOT=$(find_rocm_root || true)

if [[ -n "${ROCM_ROOT}" &&
      "${QFW_TNQVM_USE_HIP:-YES}" != "NO" ]]; then
  ROCM_LIB_DIR="${ROCM_ROOT}/lib"
  if [[ ! -e "${ROCM_LIB_DIR}/libhipblas.so" ]]; then
    ROCM_LIB_DIR="${ROCM_ROOT}/lib64"
  fi

  TNQVM_ROCM_BUILD=1
  export ROCM_PATH="${ROCM_ROOT}"
  export PATH_ROCM="${ROCM_ROOT}"
  export USE_HIP=YES
  export GPU_CUDA="${QFW_TNQVM_GPU_CUDA:-CUDA}"
  export PATH_HIP_INC="${ROCM_ROOT}/include"
  export PATH_HIPBLAS_INC="${ROCM_ROOT}/include/hipblas"
  export PATH_HIP_LIB="${ROCM_LIB_DIR}"
  export PATH_HIPBLAS_LIB="${ROCM_LIB_DIR}"
elif [[ "${QFW_RUNTIME_MODE:-frontier}" == "container" ]]; then
  if [[ "${QFW_TNQVM_USE_HIP:-NO}" != "NO" ]]; then
    echo "### Requested TNQVM HIP build, but ROCm was not detected"
  fi
  export USE_HIP=NO
  export GPU_CUDA=NOCUDA
else
  echo "### ROCm was not detected; building TNQVM without ROCm/HIP patches"
  export USE_HIP=NO
  export GPU_CUDA=NOCUDA
fi

CC_BIN=${QFW_MASTER_SETUP_CC:-gcc}
CXX_BIN=${QFW_MASTER_SETUP_CXX:-g++}
FC_BIN=${QFW_MASTER_SETUP_FC:-gfortran}
BUILD_JOBS=${QFW_BUILD_JOBS:-${QFW_MASTER_SETUP_BUILD_JOBS:-2}}
if ! [[ "${BUILD_JOBS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "QFW_BUILD_JOBS must be a positive integer, got '${BUILD_JOBS}'" >&2
  exit 1
fi
export CMAKE_BUILD_PARALLEL_LEVEL="${BUILD_JOBS}"

# -------------------------------
# Build configuration
# -------------------------------
: "${QFW_MASTER_SETUP_BASE_DIR:?QFW_MASTER_SETUP_BASE_DIR must be set by qfw_configure}"
: "${QFW_DEP_BUILD_VERSION:?QFW_DEP_BUILD_VERSION must be set by qfw_configure}"
: "${PYTHON_PATH:?PYTHON_PATH must be set by qfw_configure}"
: "${OMPI_DIR:?OMPI_DIR must be set by qfw_configure}"
: "${BLAS_LIB_DIR:?BLAS_LIB_DIR must be set by qfw_configure or pkg-config}"

VERSION=${QFW_DEP_BUILD_VERSION}

SRC_ROOT=${QFW_MASTER_SETUP_BASE_DIR}/source/${VERSION}
BASE_INSTALL_DIR=${QFW_MASTER_SETUP_BASE_DIR}/install/${VERSION}/TNQVM/
BASE_BUILD_DIR=${QFW_MASTER_SETUP_BASE_DIR}/build/${VERSION}/TNQVM/

QSRC="${SRC_ROOT}/TNQVM"
PATCH_DIR="${QFW_MASTER_SETUP_BASE_DIR}/QFw/patches/tnqvm"
CONTAINER_PATCH_DIR="${PATCH_DIR}/container"

echo "### Using source root: ${SRC_ROOT}"
echo "### TNQVM root: ${QSRC}"
echo "### Build parallelism: ${BUILD_JOBS}"
sleep 1

# -------------------------------
# Clone repositories
# -------------------------------
mkdir -p "${QSRC}"
cd "${QSRC}"

if [ ! -d xacc ]; then
  git clone --recursive https://github.com/eclipse/xacc
fi

if [ ! -d tnqvm ]; then
  git clone https://github.com/ornl-qci/tnqvm
fi

if [ ! -d exatn ]; then
  git clone --recursive https://github.com/ornl-qci/exatn.git
fi

# -----------------------------------------
# update pybind11 to work with python3.11+
# ----------------------------------------
PYBIND_TAG="v3.0.2"
rm -Rf "${QSRC}/exatn/tpls/pybind11"
cd "${QSRC}/exatn/tpls/"
git clone --depth 1 --branch "$PYBIND_TAG" https://github.com/pybind/pybind11.git

# -------------------------------
# Apply patches
# -------------------------------
echo "### Applying patches"

cd "${QSRC}/exatn/tpls/cppmicroservices"
patch -p1 -i "${PATCH_DIR}/cppmicroservices.patch"

cd "${QSRC}/exatn"
if [[ "${TNQVM_ROCM_BUILD}" == "1" ]]; then
  cd "${QSRC}/exatn/tpls/ExaTensor"
  patch -p1 -i "${PATCH_DIR}/exatensor.patch"

  cd "${QSRC}/exatn"
  patch -p1 -i "${PATCH_DIR}/exatn.patch"
  patch -p1 -i "${PATCH_DIR}/exatn_tpls.patch"
else
  patch -p1 -i "${CONTAINER_PATCH_DIR}/exatn_common.patch"
  patch -p1 -i "${CONTAINER_PATCH_DIR}/exatn_tpls.patch"
fi

cd "${QSRC}/xacc"
patch -p1 -i "${PATCH_DIR}/plugin.patch"
if [[ "${TNQVM_ROCM_BUILD}" != "1" ]]; then
  patch -p1 -i "${CONTAINER_PATCH_DIR}/xacc_gcc14.patch"
fi
patch -p1 -i "${PATCH_DIR}/xacc-cmakelist.patch"

cd "${QSRC}/xacc/tpls/cppmicroservices"
patch -p1 -i "${PATCH_DIR}/xacc-cppmicroservices.patch"

cd "${QSRC}/tnqvm"
if [[ "${TNQVM_ROCM_BUILD}" == "1" ]]; then
  patch -p1 -i "${PATCH_DIR}/tnqvm.patch"
else
  patch -p1 -i "${CONTAINER_PATCH_DIR}/tnqvm_common.patch"
fi

cd "${QSRC}/exatn/tpls/gtest"
patch -p1 -i "${PATCH_DIR}/gtest.patch"

# -------------------------------
# Copy example source
# -------------------------------
cp "${PATCH_DIR}/circuit_runner.cpp" \
   "${QSRC}/tnqvm/examples/mpi/circuit_runner.cpp"

echo "### Patching complete"
sleep 1

echo "#### LD_LIBRARY_PATH: ${LD_LIBRARY_PATH:-}"
echo "#### PATH: $PATH"
sleep 2

# -------------------------------
# Clean install and build dirs
# -------------------------------
echo "#### CLEANING INSTALL DIRECTORY"
mkdir -p "${BASE_INSTALL_DIR}"

mkdir -p \
  "${BASE_BUILD_DIR}/exatn" \
  "${BASE_BUILD_DIR}/xacc" \
  "${BASE_BUILD_DIR}/tnqvm"

# -------------------------------
# Build EXATN
# -------------------------------
echo "#### BUILDING EXATN"
cd "${BASE_BUILD_DIR}/exatn"

CC="${CC_BIN}" CXX="${CXX_BIN}" FC="${FC_BIN}" cmake \
  "${QSRC}/exatn" \
  -DGPU_CUDA="${GPU_CUDA}" \
  -DPython_EXECUTABLE="${PYTHON_PATH}" \
  -DMPI_BIN_PATH="${OMPI_DIR}/bin" \
  -DMPI_LIB=OPENMPI \
  -DMPI_ROOT_DIR="${OMPI_DIR}" \
  -DCMAKE_INSTALL_PREFIX="${BASE_INSTALL_DIR}/exatn" \
  -DEXATN_BUILD_TESTS=TRUE \
  -DBLAS_LIB=OPENBLAS \
  -DBLAS_PATH="${BLAS_LIB_DIR}" \
  -DWITH_LAPACK=YES \
  -DCMAKE_PREFIX_PATH="${OMPI_DIR}" \
  -DCMAKE_BUILD_TYPE=Debug

make -j "${BUILD_JOBS}" install

# -------------------------------
# Build XACC
# -------------------------------
echo "#### BUILDING XACC"
cd "${BASE_BUILD_DIR}/xacc"

CC="${CC_BIN}" CXX="${CXX_BIN}" FC="${FC_BIN}" cmake \
  "${QSRC}/xacc" \
  -DPython_EXECUTABLE="${PYTHON_PATH}" \
  -DCMAKE_INSTALL_PREFIX="${BASE_INSTALL_DIR}/xacc" \
  -DEXATN_BUILD_TESTS=TRUE \
  -DXACC_BUILD_EXAMPLES=TRUE \
  -DBLAS_LIB=OPENBLAS \
  -DBLAS_PATH="${BLAS_LIB_DIR}" \
  -DWITH_LAPACK=YES \
  -DCMAKE_BUILD_TYPE=Debug

make -j "${BUILD_JOBS}" install

# -------------------------------
# Build TNQVM
# -------------------------------
echo "#### BUILDING TNQVM"
cd "${BASE_BUILD_DIR}/tnqvm"

CC="${CC_BIN}" CXX="${CXX_BIN}" FC="${FC_BIN}" cmake \
  "${QSRC}/tnqvm" \
  -DTNQVM_MPI_ENABLED=TRUE \
  -DXACC_DIR="${BASE_INSTALL_DIR}/xacc" \
  -DEXATN_DIR="${BASE_INSTALL_DIR}/exatn" \
  -DEXATN_BUILD_TESTS=TRUE \
  -DBLAS_LIB=OPENBLAS \
  -DBLAS_PATH="${BLAS_LIB_DIR}" \
  -DWITH_LAPACK=YES \
  -DCMAKE_BUILD_TYPE=Debug

make -j "${BUILD_JOBS}" install

cp "${BASE_BUILD_DIR}/tnqvm/examples/mpi/circuit_runner" \
   "${QFW_MASTER_SETUP_BASE_DIR}/QFw/bin/circuit_runner.tnqvm"

echo "### BUILD COMPLETE"
