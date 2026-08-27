#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NWQSIM_REPOSITORY="https://github.com/pnnl/NWQ-Sim.git"
NWQSIM_REF="b35763d846e6512ed817d3f88ac8ce79a7e82a7e"
WORK_DIR=""
INSTALL_PREFIX=""
BUILD_JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')"
PYTHON_BIN="$(command -v python3 || true)"
CC_BIN="${CC:-gcc}"
CXX_BIN="${CXX:-g++}"
ROCM_MODE="auto"
ROCM_ROOT="${ROCM_PATH:-}"
HIP_ARCH="${MY_HIP_ARCH:-${HIP_ARCH:-gfx90a}}"

usage() {
	cat <<EOF
Usage: ${0##*/} --work-dir PATH --prefix PATH [options]

Build and install NWQ-Sim independently of the QFw installation.

Required:
  --work-dir PATH       Dependency source and build workspace
  --prefix PATH         NWQ-Sim installation prefix

Options:
  --ref REF             NWQ-Sim git revision (default: ${NWQSIM_REF})
  --jobs N              Parallel build jobs (default: ${BUILD_JOBS})
  --python PATH         Python interpreter (default: ${PYTHON_BIN:-python3})
  --cc PATH             C compiler (default: ${CC_BIN})
  --cxx PATH            C++ compiler (default: ${CXX_BIN})
  --rocm auto|on|off    ROCm selection (default: auto)
  --rocm-root PATH      ROCm installation root
  --hip-arch ARCH       HIP architecture (default: ${HIP_ARCH})
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
		--ref) NWQSIM_REF="${2:?missing value for --ref}"; shift 2 ;;
		--jobs) BUILD_JOBS="${2:?missing value for --jobs}"; shift 2 ;;
		--python) PYTHON_BIN="${2:?missing value for --python}"; shift 2 ;;
		--cc) CC_BIN="${2:?missing value for --cc}"; shift 2 ;;
		--cxx) CXX_BIN="${2:?missing value for --cxx}"; shift 2 ;;
		--rocm) ROCM_MODE="${2:?missing value for --rocm}"; shift 2 ;;
		--rocm-root) ROCM_ROOT="${2:?missing value for --rocm-root}"; shift 2 ;;
		--hip-arch) HIP_ARCH="${2:?missing value for --hip-arch}"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) fail "unknown argument: $1" ;;
	esac
done

[[ -n "${WORK_DIR}" ]] || fail "--work-dir is required"
[[ -n "${INSTALL_PREFIX}" ]] || fail "--prefix is required"
[[ "${BUILD_JOBS}" =~ ^[1-9][0-9]*$ ]] || fail "--jobs must be positive"
[[ "${ROCM_MODE}" =~ ^(auto|on|off)$ ]] || fail "invalid --rocm value"
[[ -n "${PYTHON_BIN}" && -x "${PYTHON_BIN}" ]] || fail "invalid Python interpreter"

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

SOURCE_DIR="${WORK_DIR}/source/NWQ-Sim"
BUILD_DIR="${WORK_DIR}/build"
if [[ -e "${SOURCE_DIR}" ]]; then
	fail "source directory already exists; use an empty --work-dir: ${SOURCE_DIR}"
fi

mkdir -p "${WORK_DIR}/source" "${BUILD_DIR}" "${INSTALL_PREFIX}/bin"
git clone --recursive "${NWQSIM_REPOSITORY}" "${SOURCE_DIR}"
git -C "${SOURCE_DIR}" checkout --detach "${NWQSIM_REF}"
git -C "${SOURCE_DIR}" submodule update --init --recursive

cmake_args=(
	-S "${SOURCE_DIR}"
	-B "${BUILD_DIR}"
	-DCMAKE_BUILD_TYPE=Release
	-DCMAKE_INSTALL_PREFIX="${INSTALL_PREFIX}"
	-DCMAKE_C_COMPILER="${CC_BIN}"
	-DCMAKE_CXX_COMPILER="${CXX_BIN}"
	-DPython_EXECUTABLE="${PYTHON_BIN}"
)
if ${ROCM_ENABLED}; then
	export ROCM_PATH="${ROCM_ROOT}"
	export MY_HIP_ARCH="${HIP_ARCH}"
	cmake_args+=(
		-DNWQSIM_ENABLE_CUDA=OFF
		-DNWQSIM_ENABLE_HIP=ON
		-DHIP_ARCH="${HIP_ARCH}"
	)
else
	cmake_args+=(
		-DNWQSIM_ENABLE_CUDA=OFF
		-DNWQSIM_ENABLE_HIP=OFF
	)
fi

cmake "${cmake_args[@]}"
cmake --build "${BUILD_DIR}" --parallel "${BUILD_JOBS}"
cmake --install "${BUILD_DIR}"
install -m 0755 "${BUILD_DIR}/qasm/nwq_qasm" \
	"${INSTALL_PREFIX}/bin/circuit_runner.nwqsim"

printf 'NWQ-Sim installed in %s (ROCm: %s)\n' \
	"${INSTALL_PREFIX}" "${ROCM_ENABLED}"
