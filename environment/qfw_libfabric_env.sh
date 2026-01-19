#!/usr/bin/env bash

PREFIX=$QFW_MASTER_SETUP_LIBFABRIC_INSTALL

export LIBFABRIC_INSTALL_DIR="${PREFIX}"
export LIBFABRIC_DIR="${PREFIX}"

export PATH="${PREFIX}/bin:${PATH}"
export LD_LIBRARY_PATH="${PREFIX}/lib:${LD_LIBRARY_PATH}"
export MANPATH="${PREFIX}/share/man:${MANPATH}"
export PKG_CONFIG_PATH="${PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH}"

export FI_SHM_USE_XPMEM=1

if [[ -z "${FI_LNX_PROV_LINKS:-}" ]]; then
	export FI_LNX_PROV_LINKS="shm+cxi:cxi0|shm+cxi:cxi1|shm+cxi:cxi2|shm+cxi:cxi3"
fi
