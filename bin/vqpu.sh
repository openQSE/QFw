#!/bin/bash

export MODULEPATH=/usr/share/Modules/modulefiles:/etc/modulefiles:/usr/share/modulefiles:/sw/wombat/modulefiles:/sw/wombat/RHEL9.4/spack/share/spack/modules/linux-rhel9-neoverse_n1:/sw/wombat/Nvidia_HPC_SDK/modulefile:$MODULEPATH

module load qb/qristal

VQPU_PORT=${VQPU_PORT:-8443}
VQPU_SYSTEM=${VQPU_SYSTEM:-vqpu}
VQPU_MAX_CIRCUIT_DEPTH=${VQPU_MAX_CIRCUIT_DEPTH:-1000}
VQPU_SECRET=${VQPU_SECRET:-QuantumBrillianceVQPU}
VQPU_SSL_CERT_DIR=${VQPU_SSL_CERT_DIR:-/sw/wombat/qb/qristal/1.7.0-rc0/qcstack/certs}

export QcStackPath=/tmp

#--ssl-cert-dir ${VQPU_SSL_CERT_DIR}
qcstack --port ${VQPU_PORT} \
	--system ${VQPU_SYSTEM} \
	--max-circuit-depth ${VQPU_MAX_CIRCUIT_DEPTH} \
	--reservation-shared-secret ${VQPU_SECRET} \
	--calibration False \
	--benchmarking False
#	--python-log-level DEBUG
