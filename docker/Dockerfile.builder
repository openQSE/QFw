# The Rocky Linux version. Used as the docker iamge tag.
# The 10.2 default can be replaced with --build-arg cmdline arg.
ARG ROCKYLINUX_TAG=10.2

FROM rockylinux/rockylinux:${ROCKYLINUX_TAG}


ARG REVISION=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.revision="${REVISION}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"

# TODO - inject these values from the build script environment.
ARG LICENSE="BSD-3-Clause"
ARG NAME="builder-qfw"
ARG SUMMARY="Build environment with the QRMI/QDMI/QFw build toolchain"
ARG VENDOR="Open QHPC Software Ecosystem (openQSE)"
ARG VERSION="0.0.1"

# Define these explicitly to override values in the base image.
LABEL org.opencontainers.image.authors="openQSE Contributors"
LABEL org.opencontainers.image.description="${SUMMARY}"
LABEL org.opencontainers.image.source=""
LABEL org.opencontainers.image.title="${NAME}"
LABEL org.opencontainers.image.vendor="${VENDOR}"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL license="${LICENSE}"
LABEL name="${NAME}"
LABEL summary="${SUMMARY}"
LABEL vendor="${VENDOR}"
LABEL version="${VERSION}"

RUN set -ex \
    && dnf makecache \
    && dnf -y install dnf-plugins-core epel-release \
    && dnf config-manager --set-enabled crb \
    && dnf makecache \
    && dnf -y install --nobest --exclude='*.i686' \
    cmake \
    curl \
    gcc \
    gcc-c++ \
    gcc-gfortran \
    git \
    make \
    python3 \
    python3-pip \
    tar \
    uv \
    && dnf clean all \
    && rm -rf /var/cache/dnf
