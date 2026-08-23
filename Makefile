.PHONY: docker-build docker-build-builder docker-build-qfw help

# Default target
.DEFAULT_GOAL := help

# The help target prints help message. It scans this file and looks for targets
# with the '##' (double hash) description. If found, includes the target name
# and the description in the printed output.

# Colors for help output
help: CYAN = $(shell tput -Txterm setaf 6)
help: RESET = $(shell tput -Txterm sgr0)
help:  ## Show this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?##/ {printf "  ${CYAN}%-20s${RESET} %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ============================================================================
# Docker builds
# ============================================================================

# Builds both docker images in dependency order: the toolchain image first,
# then the qfw image on top of it.
docker-build: docker-build-builder docker-build-qfw
docker-build: ## Build the qfw-builder and qfw docker images
	@echo "Built all docker images"

# The Dockerfile names to use. Dockerfile.builder installs the build
# toolchain; Dockerfile.qfw builds QRMI, QDMI, and QFw on top of it.
DOCKERFILE_BUILDER := Dockerfile.builder
DOCKERFILE_QFW := Dockerfile.qfw

DOCKER_IMAGE_TAG := latest
DOCKER_BUILDER_IMAGE_NAME := qfw-builder
DOCKER_QFW_IMAGE_NAME := qfw
BUILDER_IMAGE := $(DOCKER_BUILDER_IMAGE_NAME):$(DOCKER_IMAGE_TAG)
QFW_IMAGE := $(DOCKER_QFW_IMAGE_NAME):$(DOCKER_IMAGE_TAG)

# Dependency/tool versions used as docker build args. Read from
# VERSIONS.env when present, so version bumps happen in one place; the
# ?= defaults below are only used if VERSIONS.env is missing.
VERSIONS := VERSIONS.env
# Use abspath to ensure portable Makefile that works in other directories.
VERSIONS_FILE := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))$(VERSIONS)
ifneq (,$(wildcard $(VERSIONS_FILE)))
include $(VERSIONS_FILE)
else
$(info ⚠️ File $(VERSIONS) not found, using hard-coded default versions  ⚠️)
endif

# Rocky Linux version to use for docker image builds if VERSIONS_FILE not found.
ROCKYLINUX_VERSION ?= 10.2

# The QDMI version to use for builds if VERSIONS_FILE not found.
QRMI_VERSION ?= 0.17.2

# The QDMI version to use for builds if VERSIONS_FILE not found.
QDMI_VERSION ?= 1.3.3

# The directory within the docker image where all QFw dependencies,
# like QRMI and QDMI are installed. Each dependency has its own
# directory of the form IMAGE_INSTALL_DIR/<package-name>
IMAGE_INSTALL_DIR ?= /opt/qfw

# Build args values for docker image labels
REVISION = $(shell git rev-parse --short HEAD)
BUILD_DATE = $(shell date -u +'%Y-%m-%dT%H:%M:%SZ')

# Build args for the qfw-builder toolchain image (Dockerfile.builder).
BUILDER_IMAGE_BUILD_ARGS := \
	ROCKYLINUX_TAG=$(ROCKYLINUX_VERSION) \
	REVISION=$(REVISION) \
	BUILD_DATE=$(BUILD_DATE)

# Build args for the qfw image (Dockerfile.qfw), layered on top of the
# qfw-builder image built above.
QFW_IMAGE_BUILD_ARGS := \
	BUILDER_IMAGE=$(BUILDER_IMAGE) \
	INSTALL_DIR=$(IMAGE_INSTALL_DIR) \
	QRMI_VERSION=$(QRMI_VERSION) \
	QDMI_VERSION=$(QDMI_VERSION)

# Gets the directory path of the current Makefile
SOURCE_TOPDIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

# If the FORCE variable is defined, do not use previously-cached
# build layers.
ifneq ($(strip $(FORCE)),)
# Actions to perform if FORCE is defined AND not empty
DOCKER_BUILD_EXTRA_ARGS := --no-cache
endif

# Creates a list of --build-arg arguments; prepends each *_IMAGE_BUILD_ARGS
# item of the form <build-arg-name>=<value> with --build-arg
docker-build-builder: DOCKER_BUILD_ARGS = $(addprefix --build-arg ,$(BUILDER_IMAGE_BUILD_ARGS))
docker-build-builder: ## Build the qfw-builder toolchain image
	docker build $(DOCKER_BUILD_EXTRA_ARGS) -t $(BUILDER_IMAGE) $(SOURCE_TOPDIR) \
        $(DOCKER_BUILD_ARGS) \
        -f $(DOCKERFILE_BUILDER)

docker-build-qfw: DOCKER_BUILD_ARGS = $(addprefix --build-arg ,$(QFW_IMAGE_BUILD_ARGS))
docker-build-qfw: docker-build-builder ## Build the qfw image (QRMI, QDMI, QFw) atop qfw-builder
	docker build $(DOCKER_BUILD_EXTRA_ARGS) -t $(QFW_IMAGE) $(SOURCE_TOPDIR) \
        $(DOCKER_BUILD_ARGS) \
        -f $(DOCKERFILE_QFW)
