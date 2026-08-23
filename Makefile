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


# Rocky Linux version for builder images
ROCKYLINUX_VERSION ?= 10.2

QRMI_VERSION ?= 0.17.2

# Contains a list of build args substituted within DOCKERFILE
# Each item is of the form <build-arg-name>=<value> with no spaces
IMAGE_BUILD_ARGS = \
	ROCKYLINUX_TAG=$(ROCKYLINUX_VERSION) \
	INSTALL_DIR=$(IMAGE_INSTALL_DIR) \
	QRMI_VERSION=$(QRMI_VERSION)

#	REVISION=$(REVISION) \
#	BUILD_DATE=$(BUILD_DATE) \


# The directory where all QFw dependencies, like QRMI/QDMI are installed
IMAGE_INSTALL_DIR = /opt/qfw

# Gets the directory path of the current Makefile
docker-build: SOURCE_TOPDIR = $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
# Creates a list of --build-arg arguments; prepends each IMAGE_BUILD_ARGS
# item of the form <build-arg-name>=<value> with --build-arg
docker-build: DOCKER_BUILD_ARGS = $(addprefix --build-arg ,$(IMAGE_BUILD_ARGS))
docker-build:
	docker build -t $(IMAGE) $(SOURCE_TOPDIR) \
		$(DOCKER_BUILD_ARGS) \
		-f $(DOCKERFILE)
