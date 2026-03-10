#!/bin/bash
set -euo pipefail
set -xe

export TALSH_GPU=1
export USE_HIP=YES
export GPU_CUDA=CUDA
export PATH_ROCM=$ROCM_PATH
export PATH_HIP_INC=$ROCM_PATH/include
export PATH_HIPBLAS_INC=$ROCM_PATH/include/hipblas
export PATH_HIP_LIB=$ROCM_PATH/lib
export PATH_HIPBLAS_LIB=$ROCM_PATH/lib

# -------------------------------
# Build configuration
# -------------------------------
VERSION=${QFW_DEP_BUILD_VERSION}

SRC_ROOT=${QFW_MASTER_SETUP_BASE_DIR}/source/${VERSION}
BASE_INSTALL_DIR=${QFW_MASTER_SETUP_BASE_DIR}/install/${VERSION}/TNQVM/
BASE_BUILD_DIR=${QFW_MASTER_SETUP_BASE_DIR}/build/${VERSION}/TNQVM/

QSRC="${SRC_ROOT}/TNQVM"
PATCH_DIR="${QFW_MASTER_SETUP_BASE_DIR}/QFw/patches/tnqvm"

echo "### Using source root: ${SRC_ROOT}"
echo "### TNQVM root: ${QSRC}"
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

cd "${QSRC}/exatn/tpls/ExaTensor"
patch -p1 -i "${PATCH_DIR}/exatensor.patch"

cd "${QSRC}/exatn"
patch -p1 -i "${PATCH_DIR}/exatn.patch"
patch -p1 -i "${PATCH_DIR}/exatn_tpls.patch"

cd "${QSRC}/xacc"
patch -p1 -i "${PATCH_DIR}/plugin.patch"
patch -p1 -i "${PATCH_DIR}/xacc-cmakelist.patch"

cd "${QSRC}/xacc/tpls/cppmicroservices"
patch -p1 -i "${PATCH_DIR}/xacc-cppmicroservices.patch"

cd "${QSRC}/tnqvm"
patch -p1 -i "${PATCH_DIR}/tnqvm.patch"

cd "${QSRC}/exatn/tpls/gtest"
patch -p1 -i "${PATCH_DIR}/gtest.patch"

# -------------------------------
# Copy example source
# -------------------------------
cp "${PATCH_DIR}/circuit_runner.cpp" \
   "${QSRC}/tnqvm/examples/mpi/circuit_runner.cpp"

echo "### Patching complete"
sleep 1

echo "#### LD_LIBRARY_PATH: $LD_LIBRARY_PATH"
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

CC=gcc CXX=g++ FC=gfortran cmake \
  "${QSRC}/exatn" \
  -DGPU_CUDA=CUDA \
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

make -j install

# -------------------------------
# Build XACC
# -------------------------------
echo "#### BUILDING XACC"
cd "${BASE_BUILD_DIR}/xacc"

CC=gcc CXX=g++ FC=gfortran cmake \
  "${QSRC}/xacc" \
  -DPython_EXECUTABLE="${PYTHON_PATH}" \
  -DCMAKE_INSTALL_PREFIX="${BASE_INSTALL_DIR}/xacc" \
  -DEXATN_BUILD_TESTS=TRUE \
  -DXACC_BUILD_EXAMPLES=TRUE \
  -DBLAS_LIB=OPENBLAS \
  -DBLAS_PATH="${BLAS_LIB_DIR}" \
  -DWITH_LAPACK=YES \
  -DCMAKE_BUILD_TYPE=Debug

make -j install

# -------------------------------
# Build TNQVM
# -------------------------------
echo "#### BUILDING TNQVM"
cd "${BASE_BUILD_DIR}/tnqvm"

CC=gcc CXX=g++ FC=gfortran cmake \
  "${QSRC}/tnqvm" \
  -DTNQVM_MPI_ENABLED=TRUE \
  -DXACC_DIR="${BASE_INSTALL_DIR}/xacc" \
  -DEXATN_DIR="${BASE_INSTALL_DIR}/exatn" \
  -DEXATN_BUILD_TESTS=TRUE \
  -DBLAS_LIB=OPENBLAS \
  -DBLAS_PATH="${BLAS_LIB_DIR}" \
  -DWITH_LAPACK=YES \
  -DCMAKE_BUILD_TYPE=Debug

make -j install

cp ${BASE_BUILD_DIR}/tnqvm/examples/mpi/circuit_runner ${QFW_MASTER_SETUP_BASE_DIR}/QFw/bin/circuit_runner.tnqvm

echo "### BUILD COMPLETE"

