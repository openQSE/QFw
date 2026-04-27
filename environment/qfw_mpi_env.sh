#!/usr/bin/env bash

PREFIX=$QFW_MASTER_SETUP_MPI_INSTALL

export OMPI_INSTALL_DIR="${PREFIX}"
export OMPI_DIR="${PREFIX}"

export PRTE_MCA_ras_slurm_use_entire_allocation=1
export PRTE_MCA_ras_base_launch_orted_on_hn=1
export PRTE_MCA_prte_routed_radix=128

export PMIX_MCA_gds=hash

if [[ "${QFW_MPI_TRANSPORT_MODE:-ofi}" == "ofi" ]]; then
	export OMPI_MCA_opal_common_ofi_provider_include=lnx
	export OMPI_MCA_mtl_ofi_av=table
	export OMPI_MCA_btl='^tcp,openib,ofi'
	export OMPI_MCA_pml='^ucx'
	export OMPI_MCA_mtl=ofi
fi

export PATH="${PREFIX}/bin:${PATH:-}"
export LD_LIBRARY_PATH="${PREFIX}/lib:${LD_LIBRARY_PATH:-}"
export MANPATH="${PREFIX}/share/man:${MANPATH:-}"
export PKG_CONFIG_PATH="${PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
