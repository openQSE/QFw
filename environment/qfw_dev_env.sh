#!/usr/bin/env bash

# Must be sourced

# --- sanity check -------------------------------------------------------------

if [[ -z "${QFW_MASTER_SETUP_DEV_INSTALL}" ]]; then
	echo "ERROR: QFW_MASTER_SETUP_DEV_INSTALL is not set"
	return 1 2>/dev/null || exit 1
fi

# --- derive ROCm version and paths --------------------------------------------

AMD_CURPATH="${QFW_MASTER_SETUP_DEV_INSTALL%/}"

detect_rocm_version() {
	local root="$1"
	local version_file
	local candidate

	if [[ -n "${QFW_MASTER_SETUP_DEV_VERSION:-}" ]]; then
		echo "${QFW_MASTER_SETUP_DEV_VERSION}"
		return 0
	fi

	if [[ "${root}" =~ /rocm-([0-9]+(\.[0-9]+)*)$ ]]; then
		echo "${BASH_REMATCH[1]}"
		return 0
	fi

	for version_file in \
		"${root}/.info/version" \
		"${root}/.info/version-dev" \
		"${root}/lib/hip/version"; do
		if [[ -f "${version_file}" ]]; then
			candidate=$(grep -Eo '[0-9]+(\.[0-9]+)+' "${version_file}" | head -n1)
			if [[ -n "${candidate}" ]]; then
				echo "${candidate}"
				return 0
			fi
		fi
	done

	if command -v "${root}/bin/hipconfig" >/dev/null 2>&1; then
		candidate=$("${root}/bin/hipconfig" --version 2>/dev/null | grep -Eo '[0-9]+(\.[0-9]+)+' | head -n1)
		if [[ -n "${candidate}" ]]; then
			echo "${candidate}"
			return 0
		fi
	fi

	return 1
}

MOD_LEVEL=$(detect_rocm_version "${AMD_CURPATH}") || {
	echo "ERROR: Unable to detect ROCm version from ${AMD_CURPATH}. Set QFW_MASTER_SETUP_DEV_VERSION if needed."
	return 1 2>/dev/null || exit 1
}

PKG_CONFIG_PREFIX="/usr/lib64"

# CPE metadata
CPE_PRODUCT_NAME="CRAY_ROCM"
CPE_PKGCONFIG_LIB="rocm-${MOD_LEVEL}"
CPE_PKGCONFIG_PATH="${PKG_CONFIG_PREFIX}/pkgconfig"

# AMD paths
AMD_LIB="${AMD_CURPATH}/lib"
AMD_BIN="${AMD_CURPATH}/bin"
AMD_INCLUDE="${AMD_CURPATH}/include"
AMD_MAN="${AMD_CURPATH}/share/man"

AMD_ROCP_LIB="${AMD_CURPATH}/lib/rocprofiler"
AMD_ROCP_INCLUDE="${AMD_CURPATH}/include/rocprofiler"

AMD_ROCT_LIB="${AMD_CURPATH}/lib/roctracer"
AMD_ROCT_INCLUDE="${AMD_CURPATH}/include/roctracer"

AMD_HIP_CMAKE="${AMD_CURPATH}/lib/cmake/hip"
AMD_HIP_INCLUDE="${AMD_CURPATH}/include/hip"

# --- environment variables ----------------------------------------------------

export CRAY_ROCM_DIR="${AMD_CURPATH}"
export CRAY_ROCM_PREFIX="${AMD_CURPATH}"
export CRAY_ROCM_VERSION="${MOD_LEVEL}"

export ROCM_PATH="${AMD_CURPATH}"
export HIP_LIB_PATH="${AMD_LIB}"

export CRAY_ROCM_INCLUDE_OPTS="-I${AMD_INCLUDE} \
-I${AMD_ROCP_INCLUDE} \
-I${AMD_ROCT_INCLUDE} \
-I${AMD_HIP_INCLUDE} \
-D__HIP_PLATFORM_AMD__"

export CRAY_ROCM_POST_LINK_OPTS="\
-L${AMD_LIB} \
-L${AMD_ROCP_LIB} \
-L${AMD_ROCT_LIB} \
-lamdhip64"

# --- path helpers -------------------------------------------------------------

path_prepend() {
	local var="$1"
	local val="$2"
	[[ -d "$val" ]] || return 0
	eval "export ${var}=\"${val}:\${${var}:-}\""
}

path_append() {
	local var="$1"
	local val="$2"
	[[ -d "$val" ]] || return 0
	eval "export ${var}=\"\${${var}:-}:${val}\""
}

# --- PATH updates -------------------------------------------------------------

path_prepend PATH "${AMD_BIN}"
path_prepend MANPATH "${AMD_MAN}"
path_prepend CMAKE_PREFIX_PATH "${AMD_HIP_CMAKE}"

path_prepend LD_LIBRARY_PATH "${AMD_LIB}"
path_prepend LD_LIBRARY_PATH "${AMD_ROCP_LIB}"
path_prepend LD_LIBRARY_PATH "${AMD_ROCT_LIB}"

# --- CPE / pkg-config ---------------------------------------------------------

path_append PE_PRODUCT_LIST "${CPE_PRODUCT_NAME}"
path_prepend PKG_CONFIG_PATH "${CPE_PKGCONFIG_PATH}"
path_prepend PE_PKGCONFIG_LIBS "${CPE_PKGCONFIG_LIB}"
