#!/bin/bash
set -euo pipefail

# -------------------------------
# Build configuration
# -------------------------------
VERSION=${QFW_DEP_BUILD_VERSION}

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
        -DCMAKE_C_COMPILER=gcc \
        -DCMAKE_CXX_COMPILER=g++ \
        -DPython_EXECUTABLE="${PYTHON_PATH}" \
        -DHIP_ARCH=gfx90a && \
make -j && make install

if [ $? -ne 0 ]; then
    exit $?
fi

