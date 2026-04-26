#!/bin/bash
set -euo pipefail
set -xe

# -------------------------------
# Build configuration
# -------------------------------
VERSION=${QFW_DEP_BUILD_VERSION}
CC_BIN=${QFW_MASTER_SETUP_CC:-gcc}
CXX_BIN=${QFW_MASTER_SETUP_CXX:-g++}
HIP_ARCH=${QFW_MASTER_SETUP_HIP_ARCH:-gfx90a}

SRC_ROOT=${QFW_MASTER_SETUP_BASE_DIR}/source
BASE_INSTALL_DIR=${QFW_MASTER_SETUP_BASE_DIR}/install/${VERSION}/NWQSIM/
BASE_BUILD_DIR=${QFW_MASTER_SETUP_BASE_DIR}/build/${VERSION}/NWQSIM/

QSRC="${SRC_ROOT}/${VERSION}/NWQSIM"

echo "### Using source root: ${SRC_ROOT}"
echo "### TNQVM root: ${QSRC}"
sleep 1

echo "#### PATH: $PATH"
echo "#### LD_LIBRARY_PATH: $LD_LIBRARY_PATH"
sleep 1

# -------------------------------
# Clone repositories
# -------------------------------
mkdir -p "${QSRC}"
cd "${QSRC}"

if [ ! -d NWQ-Sim ]; then
  git clone https://github.com/pnnl/NWQ-Sim.git
  cd ${QSRC}/NWQ-Sim
  git submodule update --init --recursive
fi

echo "#### CLEANING UP INSTALL DIRECTORY"
mkdir -p $BASE_INSTALL_DIR

echo "#### SETTING UP BUILD DIRECTORIES"
mkdir -p "${BASE_BUILD_DIR}"

if [ $? -ne 0 ]; then
    exit $?
fi

echo "#### BUILDING NWQ-Sim: ${QSRC}/NWQ-Sim"

cd ${BASE_BUILD_DIR} && \
cmake ${QSRC}/NWQ-Sim \
        -DCMAKE_INSTALL_PREFIX=${BASE_INSTALL_DIR} \
        -DCMAKE_C_COMPILER="${CC_BIN}" \
        -DCMAKE_CXX_COMPILER="${CXX_BIN}" \
        -DHIP_ARCH="${HIP_ARCH}" \
        -DPython_EXECUTABLE="${PYTHON_PATH}" && \
make -j && make install

cp ${BASE_BUILD_DIR}/qasm/nwq_qasm ${QFW_MASTER_SETUP_BASE_DIR}/QFw/bin/circuit_runner.nwqsim

if [ $? -ne 0 ]; then
    exit $?
fi
