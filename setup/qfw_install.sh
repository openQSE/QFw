#!/usr/bin/env bash
set -euo pipefail

usage() {
	cat <<'EOF'
Usage: qfw_install.sh --prefix <prefix> [--build-dir <dir>] [--with-defw]

Developer convenience installer. It delegates to the CMake configure, build,
and install flow used by packaged deployments.
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$(cd "${script_dir}/.." && pwd)"
build_dir="${source_dir}/build"
prefix=""
with_defw=0

while [[ $# -gt 0 ]]; do
	case "$1" in
		--prefix)
			[[ $# -ge 2 ]] || { echo "--prefix requires a path" >&2; exit 2; }
			prefix="$2"
			shift 2
			;;
		--build-dir)
			[[ $# -ge 2 ]] || { echo "--build-dir requires a path" >&2; exit 2; }
			build_dir="$2"
			shift 2
			;;
		--with-defw)
			with_defw=1
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "Unknown argument: $1" >&2
			usage >&2
			exit 2
			;;
	esac
done

if [[ -z "${prefix}" ]]; then
	usage >&2
	exit 2
fi

cmake -S "${source_dir}" -B "${build_dir}" \
	-DCMAKE_INSTALL_PREFIX="${prefix}" \
	-DQFW_BUILD_BUNDLED_DEFW="${with_defw}"
cmake --build "${build_dir}"
cmake --install "${build_dir}"
