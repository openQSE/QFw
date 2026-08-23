.PHONY: docker-build help lint test

# Default target
.DEFAULT_GOAL := help

# Python source directories linted in CI (kept in sync with
# .github/scripts/ci-syntax.sh).
PY_DIRS := services/ service-apis/ backends/ examples/

# Colors for help output
CYAN := $(shell tput -Txterm setaf 6)
RESET := $(shell tput -Txterm sgr0)

help:  ## Show this help message
	@echo "QFw - Available Commands"
	@echo "========================="
	@echo ""
	@echo "Development & Testing:"
	@printf "  ${CYAN}%-10s${RESET} %s\n" "docker-build" "Build docker images"

# ============================================================================
# Docker builds
# ============================================================================

# The explicit Dockerfile name to use
DOCKERFILE := Dockerfile.build

DOCKER_IMAGE_NAME := qfw-builder
DOCKER_IMAGE_TAG := latest
IMAGE := $(DOCKER_IMAGE_NAME):$(DOCKER_IMAGE_TAG)

# The args for the docker
REVISION = $(shell git rev-parse --short HEAD)
BUILD_DATE = $(shell date -u +'%Y-%m-%dT%H:%M:%SZ')

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

# Contains a list of build args substituted within DOCKERFILE
# Each item is of the form <build-arg-name>=<value> with no spaces
IMAGE_BUILD_ARGS := \
	ROCKYLINUX_TAG=$(ROCKYLINUX_VERSION) \
	INSTALL_DIR=$(IMAGE_INSTALL_DIR) \
	QRMI_VERSION=$(QRMI_VERSION) \
	QDMI_VERSION=$(QDMI_VERSION)
#	REVISION=$(REVISION) \
#	BUILD_DATE=$(BUILD_DATE) \

# Creates a list of --build-arg arguments; prepends each IMAGE_BUILD_ARGS
# item of the form <build-arg-name>=<value> with --build-arg
docker-build: DOCKER_BUILD_ARGS = $(addprefix --build-arg ,$(IMAGE_BUILD_ARGS))

# Gets the directory path of the current Makefile
docker-build: SOURCE_TOPDIR = $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
docker-build:
	docker build -t $(IMAGE) $(SOURCE_TOPDIR) \
		$(DOCKER_BUILD_ARGS) \
		-f $(DOCKERFILE)
