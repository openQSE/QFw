#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XACC_REPOSITORY="https://github.com/eclipse/xacc.git"
EXATN_REPOSITORY="https://github.com/ornl-qci/exatn.git"
TNQVM_REPOSITORY="https://github.com/ornl-qci/tnqvm.git"
XACC_REF="d1edaa7ae53edc7e335f46d33160f93d6020aaa3"
EXATN_REF="c528f7ec7323ab5f4485b85efcefa016c32e1a2d"
TNQVM_REF="a70eed190a4448dbe3dae94189d066ac694dc798"
PYBIND_REF="v3.0.2"
BOOST_URL="https://archives.boost.io/release/1.75.0/source/"\
"boost_1_75_0.tar.bz2"
WORK_DIR=""
INSTALL_PREFIX=""
MPI_PREFIX=""
BLAS_LIB_DIR=""
BUILD_JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')"
PYTHON_BIN="$(command -v python3 || true)"
CC_BIN="${CC:-gcc}"
CXX_BIN="${CXX:-g++}"
FC_BIN="${FC:-gfortran}"
ROCM_MODE="auto"
ROCM_ROOT="${ROCM_PATH:-}"

usage() {
	cat <<EOF
Usage: ${0##*/} --work-dir PATH --prefix PATH --mpi-prefix PATH [options]

Build and install ExaTN, XACC, TNQVM, and QFw's TNQVM circuit runner.

Required:
  --work-dir PATH       Dependency source and build workspace
  --prefix PATH         Combined TNQVM stack installation prefix
  --mpi-prefix PATH     OpenMPI installation prefix

Options:
  --blas-lib-dir PATH   OpenBLAS library directory (auto-detected by default)
  --jobs N              Parallel build jobs (default: ${BUILD_JOBS})
  --python PATH         Python interpreter (default: ${PYTHON_BIN:-python3})
  --cc PATH             C compiler (default: ${CC_BIN})
  --cxx PATH            C++ compiler (default: ${CXX_BIN})
  --fc PATH             Fortran compiler (default: ${FC_BIN})
  --rocm auto|on|off    ROCm selection (default: auto)
  --rocm-root PATH      ROCm installation root
  --xacc-ref REF        XACC revision (default: ${XACC_REF})
  --exatn-ref REF       ExaTN revision (default: ${EXATN_REF})
  --tnqvm-ref REF       TNQVM revision (default: ${TNQVM_REF})
  -h, --help            Show this help
EOF
}

fail() {
	printf '%s: %s\n' "${0##*/}" "$*" >&2
	exit 1
}

while (($#)); do
	case "$1" in
		--work-dir) WORK_DIR="${2:?missing value for --work-dir}"; shift 2 ;;
		--prefix) INSTALL_PREFIX="${2:?missing value for --prefix}"; shift 2 ;;
		--mpi-prefix) MPI_PREFIX="${2:?missing value for --mpi-prefix}"; shift 2 ;;
		--blas-lib-dir) BLAS_LIB_DIR="${2:?missing value for --blas-lib-dir}"; shift 2 ;;
		--jobs) BUILD_JOBS="${2:?missing value for --jobs}"; shift 2 ;;
		--python) PYTHON_BIN="${2:?missing value for --python}"; shift 2 ;;
		--cc) CC_BIN="${2:?missing value for --cc}"; shift 2 ;;
		--cxx) CXX_BIN="${2:?missing value for --cxx}"; shift 2 ;;
		--fc) FC_BIN="${2:?missing value for --fc}"; shift 2 ;;
		--rocm) ROCM_MODE="${2:?missing value for --rocm}"; shift 2 ;;
		--rocm-root) ROCM_ROOT="${2:?missing value for --rocm-root}"; shift 2 ;;
		--xacc-ref) XACC_REF="${2:?missing value for --xacc-ref}"; shift 2 ;;
		--exatn-ref) EXATN_REF="${2:?missing value for --exatn-ref}"; shift 2 ;;
		--tnqvm-ref) TNQVM_REF="${2:?missing value for --tnqvm-ref}"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) fail "unknown argument: $1" ;;
	esac
done

[[ -n "${WORK_DIR}" ]] || fail "--work-dir is required"
[[ -n "${INSTALL_PREFIX}" ]] || fail "--prefix is required"
[[ -n "${MPI_PREFIX}" ]] || fail "--mpi-prefix is required"
[[ "${BUILD_JOBS}" =~ ^[1-9][0-9]*$ ]] || fail "--jobs must be positive"
[[ "${ROCM_MODE}" =~ ^(auto|on|off)$ ]] || fail "invalid --rocm value"
[[ -n "${PYTHON_BIN}" && -x "${PYTHON_BIN}" ]] || fail "invalid Python interpreter"
[[ -x "${MPI_PREFIX}/bin/mpirun" ]] || fail "OpenMPI not found under ${MPI_PREFIX}"

if [[ -z "${BLAS_LIB_DIR}" ]]; then
	BLAS_LIB_DIR="$(pkg-config --variable=libdir openblas 2>/dev/null || true)"
fi
[[ -n "${BLAS_LIB_DIR}" && -d "${BLAS_LIB_DIR}" ]] || \
	fail "unable to locate OpenBLAS; use --blas-lib-dir"

detect_rocm() {
	local root="${ROCM_ROOT:-/opt/rocm}"
	[[ -x "${root}/bin/hipcc" ]] || return 1
	[[ -d "${root}/include/hip" ]] || return 1
	[[ -e "${root}/lib/libhipblas.so" || -e "${root}/lib64/libhipblas.so" ]] || return 1
	ROCM_ROOT="${root}"
}

ROCM_ENABLED=false
case "${ROCM_MODE}" in
	on)
		detect_rocm || fail "--rocm on requires hipcc, HIP headers, and hipBLAS"
		ROCM_ENABLED=true
		;;
	auto)
		if detect_rocm; then ROCM_ENABLED=true; fi
		;;
esac

SOURCE_ROOT="${WORK_DIR}/source"
BUILD_ROOT="${WORK_DIR}/build"
XACC_SOURCE="${SOURCE_ROOT}/xacc"
EXATN_SOURCE="${SOURCE_ROOT}/exatn"
TNQVM_SOURCE="${SOURCE_ROOT}/tnqvm"
XACC_PREFIX="${INSTALL_PREFIX}/xacc"
EXATN_PREFIX="${INSTALL_PREFIX}/exatn"
# TNQVM is an XACC plugin. Install it into the XACC prefix so XACC discovers
# the accelerator and visitor plugins from its canonical plugins directory.
TNQVM_PREFIX="${XACC_PREFIX}"

for source in "${XACC_SOURCE}" "${EXATN_SOURCE}" "${TNQVM_SOURCE}"; do
	[[ ! -e "${source}" ]] || fail \
		"source directory already exists; use an empty --work-dir: ${source}"
done

mkdir -p "${SOURCE_ROOT}" "${BUILD_ROOT}" "${INSTALL_PREFIX}/bin"
git clone --recursive "${XACC_REPOSITORY}" "${XACC_SOURCE}"
git -C "${XACC_SOURCE}" checkout --detach "${XACC_REF}"
git -C "${XACC_SOURCE}" submodule update --init --recursive
git clone --recursive "${EXATN_REPOSITORY}" "${EXATN_SOURCE}"
git -C "${EXATN_SOURCE}" checkout --detach "${EXATN_REF}"
git -C "${EXATN_SOURCE}" submodule update --init --recursive
git clone "${TNQVM_REPOSITORY}" "${TNQVM_SOURCE}"
git -C "${TNQVM_SOURCE}" checkout --detach "${TNQVM_REF}"

# The pinned ExaTN pybind11 revision predates Python 3.11. Replace only that
# vendored dependency with the validated release used by the prior builder.
rm -rf "${EXATN_SOURCE}/tpls/pybind11"
git clone --depth 1 --branch "${PYBIND_REF}" \
	https://github.com/pybind/pybind11.git "${EXATN_SOURCE}/tpls/pybind11"

apply_patch_file() {
	local target="$1"
	local patch_file="$2"
	patch --directory="${target}" --strip=1 --forward \
		--input="${patch_file}"
}

PATCH_ROOT="${SCRIPT_DIR}/patches"
apply_patch_file "${EXATN_SOURCE}/tpls/cppmicroservices" \
	"${PATCH_ROOT}/common/exatn-cppmicroservices.patch"
apply_patch_file "${XACC_SOURCE}" \
	"${PATCH_ROOT}/common/xacc-plugin.patch"
apply_patch_file "${XACC_SOURCE}" \
	"${PATCH_ROOT}/common/xacc-cmake.patch"
apply_patch_file "${XACC_SOURCE}/tpls/cppmicroservices" \
	"${PATCH_ROOT}/common/xacc-cppmicroservices.patch"
apply_patch_file "${EXATN_SOURCE}/tpls/gtest" \
	"${PATCH_ROOT}/common/exatn-gtest.patch"

if ${ROCM_ENABLED}; then
	apply_patch_file "${EXATN_SOURCE}/tpls/ExaTensor" \
		"${PATCH_ROOT}/rocm/exatensor.patch"
	apply_patch_file "${EXATN_SOURCE}" \
		"${PATCH_ROOT}/rocm/exatn.patch"
	apply_patch_file "${EXATN_SOURCE}" \
		"${PATCH_ROOT}/rocm/exatn-tpls.patch"
	apply_patch_file "${TNQVM_SOURCE}" \
		"${PATCH_ROOT}/rocm/tnqvm.patch"
else
	apply_patch_file "${EXATN_SOURCE}" \
		"${PATCH_ROOT}/cpu/exatn.patch"
	apply_patch_file "${EXATN_SOURCE}" \
		"${PATCH_ROOT}/cpu/exatn-tpls.patch"
	apply_patch_file "${XACC_SOURCE}" \
		"${PATCH_ROOT}/cpu/xacc-gcc14.patch"
	apply_patch_file "${TNQVM_SOURCE}" \
		"${PATCH_ROOT}/cpu/tnqvm.patch"
fi

install -m 0644 "${SCRIPT_DIR}/circuit_runner.cpp" \
	"${TNQVM_SOURCE}/examples/mpi/circuit_runner.cpp"

export TALSH_GPU=1
export PATH="${MPI_PREFIX}/bin:${PATH}"
export LD_LIBRARY_PATH="${MPI_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
GPU_CUDA=NOCUDA
if ${ROCM_ENABLED}; then
	ROCM_LIB_DIR="${ROCM_ROOT}/lib"
	[[ -e "${ROCM_LIB_DIR}/libhipblas.so" ]] || ROCM_LIB_DIR="${ROCM_ROOT}/lib64"
	export ROCM_PATH="${ROCM_ROOT}"
	export PATH_ROCM="${ROCM_ROOT}"
	export USE_HIP=YES
	export PATH_HIP_INC="${ROCM_ROOT}/include"
	export PATH_HIPBLAS_INC="${ROCM_ROOT}/include/hipblas"
	export PATH_HIP_LIB="${ROCM_LIB_DIR}"
	export PATH_HIPBLAS_LIB="${ROCM_LIB_DIR}"
	GPU_CUDA=CUDA
else
	export USE_HIP=NO
fi

cmake -S "${EXATN_SOURCE}" -B "${BUILD_ROOT}/exatn" \
	-DCMAKE_BUILD_TYPE=Release \
	-DCMAKE_INSTALL_PREFIX="${EXATN_PREFIX}" \
	-DCMAKE_C_COMPILER="${CC_BIN}" \
	-DCMAKE_CXX_COMPILER="${CXX_BIN}" \
	-DCMAKE_Fortran_COMPILER="${FC_BIN}" \
	-DGPU_CUDA="${GPU_CUDA}" \
	-DPython_EXECUTABLE="${PYTHON_BIN}" \
	-DMPI_BIN_PATH="${MPI_PREFIX}/bin" \
	-DMPI_LIB=OPENMPI \
	-DMPI_ROOT_DIR="${MPI_PREFIX}" \
	-DEXATN_BUILD_TESTS=FALSE \
	-DBLAS_LIB=OPENBLAS \
	-DBLAS_PATH="${BLAS_LIB_DIR}" \
	-DWITH_LAPACK=YES
cmake --build "${BUILD_ROOT}/exatn" --parallel "${BUILD_JOBS}" --target install

cmake -S "${XACC_SOURCE}" -B "${BUILD_ROOT}/xacc" \
	-DCMAKE_BUILD_TYPE=Release \
	-DCMAKE_INSTALL_PREFIX="${XACC_PREFIX}" \
	-DCMAKE_C_COMPILER="${CC_BIN}" \
	-DCMAKE_CXX_COMPILER="${CXX_BIN}" \
	-DCMAKE_Fortran_COMPILER="${FC_BIN}" \
	-DPython_EXECUTABLE="${PYTHON_BIN}" \
	-DBOOST_URL="${BOOST_URL}" \
	-DXACC_BUILD_EXAMPLES=FALSE \
	-DBLAS_LIB=OPENBLAS \
	-DBLAS_PATH="${BLAS_LIB_DIR}" \
	-DWITH_LAPACK=YES
cmake --build "${BUILD_ROOT}/xacc" --parallel "${BUILD_JOBS}" --target install

cmake -S "${TNQVM_SOURCE}" -B "${BUILD_ROOT}/tnqvm" \
	-DCMAKE_BUILD_TYPE=Release \
	-DCMAKE_INSTALL_PREFIX="${TNQVM_PREFIX}" \
	-DCMAKE_C_COMPILER="${CC_BIN}" \
	-DCMAKE_CXX_COMPILER="${CXX_BIN}" \
	-DCMAKE_Fortran_COMPILER="${FC_BIN}" \
	-DTNQVM_MPI_ENABLED=TRUE \
	-DTNQVM_BUILD_TESTS=FALSE \
	-DTNQVM_BUILD_EXAMPLES=TRUE \
	-DXACC_DIR="${XACC_PREFIX}" \
	-DExaTN_DIR="${EXATN_PREFIX}" \
	-DEXATN_DIR="${EXATN_PREFIX}" \
	-DBLAS_LIB=OPENBLAS \
	-DBLAS_PATH="${BLAS_LIB_DIR}" \
	-DWITH_LAPACK=YES
cmake --build "${BUILD_ROOT}/tnqvm" --parallel "${BUILD_JOBS}" --target install

install -d "${XACC_PREFIX}/bin"
install -m 0755 "${BUILD_ROOT}/tnqvm/examples/mpi/circuit_runner" \
	"${XACC_PREFIX}/bin/circuit_runner.tnqvm"
ln -sfn ../xacc/bin/circuit_runner.tnqvm \
	"${INSTALL_PREFIX}/bin/circuit_runner.tnqvm"

test -f "${XACC_PREFIX}/plugins/libtnqvm.so"
test -x "${XACC_PREFIX}/bin/circuit_runner.tnqvm"

printf 'TNQVM installed in %s (ROCm: %s)\n' \
	"${INSTALL_PREFIX}" "${ROCM_ENABLED}"
