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
	@echo ""
	@echo "Variables:"
	@awk 'BEGIN {FS = "[ \t]*\\?=[ \t]*.*?## "} /^[a-zA-Z_-]+[ \t]*[?]=[ \t]*.*?##/ {printf "  ${CYAN}%-20s${RESET} %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ============================================================================
# Docker builds
# ============================================================================

# The Dockerfile names to use. Dockerfile.builder installs the build
# toolchain; Dockerfile.qfw builds QRMI, QDMI, and QFw on top of it.
DOCKERFILE_BUILDER := Dockerfile.builder
DOCKERFILE_QFW := Dockerfile.qfw

IMAGE_USERNAME ?= openqse
IMAGE_VERSION ?= 0.1

IMAGE_NAME_BUILDER := builder-qfw
IMAGE_NAME_QFW := qfw

# The IMAGE_TAG is used for all images built here.
IMAGE_TAG ?= $(DEFAULT_IMAGE_TAG) ## Replaces the automatically-generated docker image tag
#
# The default non-sensical value for IMAGE_TAG to help determine if IMAGE_TAG
# was explicitly set outside this Makefile.
DEFAULT_IMAGE_TAG = default-tag-value
#
# If IMAGE_TAG is not provided explicitly, derive the tag for each image from
# the IMAGE_VERSION and the current date/timestamp composite.
# If not running a CI/CD build, use only a date, not H:M:S
ifneq ($(strip $(IMAGE_TAG)),$(DEFAULT_IMAGE_TAG))
# IMAGE_TAG is defined, use its value to set tags for all images
IMAGE_TAG_BUILDER := $(IMAGE_TAG)
IMAGE_TAG_QFW := $(IMAGE_TAG)
else
ifneq ($(strip $(GITHUB_ACTIONS)),)
# Running inside GitHub actions, use precise timestamp
IMAGE_TAG_TIMESTAMP := $(shell date -u +%y%m%d%H%M%S)
$(error CI/CD)
else
# Use only the date in the
IMAGE_TAG_TIMESTAMP := $(shell date -u +%y%m%d)
endif
# Create an image tag as the composite of the IMAGE_VERSION and the timestamp
IMAGE_TAG_BUILDER := $(IMAGE_VERSION).$(IMAGE_TAG_TIMESTAMP)
IMAGE_TAG_QFW := $(IMAGE_VERSION).$(IMAGE_TAG_TIMESTAMP)

# Replace the DEFAULT_IMAGE_TAG value with the just-derived one.
IMAGE_TAG := $(IMAGE_VERSION).$(IMAGE_TAG_TIMESTAMP)
endif

# Compose the full image name, including the tag
IMAGE_BUILDER := $(IMAGE_USERNAME)/$(IMAGE_NAME_BUILDER):$(IMAGE_TAG_BUILDER)
IMAGE_QFW := $(IMAGE_USERNAME)/$(IMAGE_NAME_QFW):$(IMAGE_TAG_QFW)

# Dependency/tool versions used as docker build args. Read from VERSIONS.env
# so version bumps happen in one place.
VERSIONS_FILENAME := VERSIONS.env

# Use abspath to ensure portable Makefile that works in other directories.
VERSIONS ?= $(dir $(abspath $(lastword $(MAKEFILE_LIST))))$(VERSIONS_FILENAME) ## The env file with dependency versions

# Read in the file with versions of all dependencies and set the values as variables.
ifneq (,$(wildcard $(VERSIONS)))
include $(VERSIONS)
else
$(error File $(abspath $(VERSIONS)) not found)
endif

# A default value when entry not found in VERSIONS env file.
# The value is chosen deliberately to fail a shell command.
UNDEFINED_VALUE := <undefined>

# Rocky Linux version to use for docker image builds if VERSIONS file not found.
ROCKYLINUX_VERSION ?= $(UNDEFINED_VALUE)

# The QDMI version to use for builds if VERSIONS file not found.
QRMI_VERSION ?= $(UNDEFINED_VALUE)

# The QDMI version to use for builds if VERSIONS file not found.
QDMI_VERSION ?= $(UNDEFINED_VALUE)

# The directory within the docker image where all QFw dependencies,
# like QRMI and QDMI are installed. Each dependency has its own
# directory of the form DOCKER_INSTALL_DIR/<package-name>
DOCKER_INSTALL_DIR ?= /opt/qfw ## Install directory inside a docker image with built artifacts

# Build args values for docker image labels
REVISION = $(shell git rev-parse --short HEAD)
BUILD_DATE = $(shell date -u +'%Y-%m-%dT%H:%M:%SZ')

# Build args for the builder-qfw toolchain image (Dockerfile.builder).
# The values are
IMAGE_BUILD_ARGS_BUILDER := \
	NAME=$(IMAGE_NAME_BUILDER) \
	ROCKYLINUX_TAG=$(ROCKYLINUX_VERSION) \
	REVISION=$(REVISION) \
	VERSION=$(IMAGE_VERSION)

# Do not include this in the --build-arg list for now. It causes rebuilds even
# with docker build caching enabled.
# IMAGE_BUILD_ARGS_BUILDER += BUILD_DATE=$(BUILD_DATE)

# Build args for the qfw image (Dockerfile.qfw), layered on top of the
# builder-qfw image built above.
IMAGE_BUILD_ARGS_QFW := \
	BASE_IMAGE=$(IMAGE_BUILDER) \
	INSTALL_DIR=$(DOCKER_INSTALL_DIR) \
	QRMI_VERSION=$(QRMI_VERSION) \
	QDMI_VERSION=$(QDMI_VERSION)

# Define the docker build PATH argument aka the build context.
# Use the location where the source files are listed in relation to
# the directory path of the current Makefile.
# Given this file's location in the source tree, use the parent directory.
SOURCE_TOPDIR := $(abspath $(dir $(abspath $(lastword $(MAKEFILE_LIST))))../)

FORCE ?= ## Set to force docker rebuild and bypass build cache

# If the FORCE variable is defined, do not use previously-cached build layers.
ifneq ($(strip $(FORCE)),)
# Use the --no-cache arg with docker build
DOCKER_BUILD_EXTRA_ARGS := --no-cache
endif

# Creates a list of --build-arg arguments; prepends each *_IMAGE_BUILD_ARGS
# item of the form <build-arg-name>=<value> with --build-arg
docker-build-builder: DOCKER_BUILD_ARGS = $(addprefix --build-arg ,$(IMAGE_BUILD_ARGS_BUILDER))
# Set the path for the build context based on the location of the source files
docker-build-builder: DOCKER_BUILD_PATH = .
docker-build-builder: ## Build the builder-qfw toolchain image
	docker build $(DOCKER_BUILD_EXTRA_ARGS) -t $(IMAGE_BUILDER) $(DOCKER_BUILD_PATH) \
        $(DOCKER_BUILD_ARGS) \
        -f $(DOCKERFILE_BUILDER)

docker-build-qfw: DOCKER_BUILD_ARGS = $(addprefix --build-arg ,$(IMAGE_BUILD_ARGS_QFW))
# Set the path for the build context based on the location of the source files
docker-build-qfw: DOCKER_BUILD_PATH = $(SOURCE_TOPDIR)
docker-build-qfw: docker-build-builder ## Build the qfw image (QRMI, QDMI, QFw) atop builder-qfw
	docker build $(DOCKER_BUILD_EXTRA_ARGS) -t $(IMAGE_QFW) $(DOCKER_BUILD_PATH) \
        $(DOCKER_BUILD_ARGS) \
        -f $(DOCKERFILE_QFW)

# Builds both docker images in dependency order: the toolchain image first,
# then the qfw image on top of it.
docker-build: docker-build-builder docker-build-qfw
docker-build: ## Build the builder-qfw and qfw docker images
	@echo "Built docker images"
	@docker image ls 'openqse/*$(IMAGE_TAG)'
