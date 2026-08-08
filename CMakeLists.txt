cmake_minimum_required(VERSION 3.20)

project(QFw
	VERSION 0.1.0
	LANGUAGES NONE)

if(NOT DEFINED CMAKE_INSTALL_BINDIR)
	set(CMAKE_INSTALL_BINDIR "bin" CACHE PATH "User executables")
endif()
if(NOT DEFINED CMAKE_INSTALL_LIBDIR)
	set(CMAKE_INSTALL_LIBDIR "lib" CACHE PATH "Object code libraries")
endif()
if(NOT DEFINED CMAKE_INSTALL_LIBEXECDIR)
	set(CMAKE_INSTALL_LIBEXECDIR "libexec" CACHE PATH "Program executables")
endif()
if(NOT DEFINED CMAKE_INSTALL_DATADIR)
	set(CMAKE_INSTALL_DATADIR "share" CACHE PATH "Read-only architecture-independent data")
endif()
include(GNUInstallDirs)

find_package(Python3 REQUIRED COMPONENTS Interpreter)

option(QFW_BUILD_BUNDLED_DEFW
	"Build and install the bundled DEFw tree with QFw" OFF)
set(QFW_DEFAULT_DEFW_PREFIX "self" CACHE STRING
	"Default DEFw prefix for generated QFw launchers; use 'self' for QFW_PREFIX")
set(QFW_PYTHON_INSTALL_DIR
	"${CMAKE_INSTALL_LIBDIR}/python${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/site-packages"
	CACHE STRING "Relative install directory for QFw Python packages")

set(QFW_GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(QFW_GENERATED_BIN_DIR "${QFW_GENERATED_DIR}/bin")
set(QFW_GENERATED_LIBEXEC_DIR "${QFW_GENERATED_DIR}/libexec/qfw")
file(MAKE_DIRECTORY
	"${QFW_GENERATED_BIN_DIR}"
	"${QFW_GENERATED_LIBEXEC_DIR}")

configure_file(
	cmake/qfw-activate.in
	"${QFW_GENERATED_BIN_DIR}/qfw-activate"
	@ONLY)
configure_file(
	cmake/defw-python.in
	"${QFW_GENERATED_BIN_DIR}/defw-python"
	@ONLY)
configure_file(
	cmake/qfw-setup-driver.in
	"${QFW_GENERATED_LIBEXEC_DIR}/qfw-setup-driver"
	@ONLY)
file(CHMOD
	"${QFW_GENERATED_BIN_DIR}/qfw-activate"
	"${QFW_GENERATED_BIN_DIR}/defw-python"
	"${QFW_GENERATED_LIBEXEC_DIR}/qfw-setup-driver"
	PERMISSIONS
		OWNER_READ OWNER_WRITE OWNER_EXECUTE
		GROUP_READ GROUP_EXECUTE
		WORLD_READ WORLD_EXECUTE)

foreach(qfw_command
		qfw-setup
		qfw-srun
		qfw-teardown
		qfw-dirsvc-start
		qfw-service-start)
	set(QFW_COMMAND_NAME "${qfw_command}")
	configure_file(
		cmake/qfw-command.in
		"${QFW_GENERATED_BIN_DIR}/${qfw_command}"
		@ONLY)
	file(CHMOD "${QFW_GENERATED_BIN_DIR}/${qfw_command}"
		PERMISSIONS
			OWNER_READ OWNER_WRITE OWNER_EXECUTE
			GROUP_READ GROUP_EXECUTE
			WORLD_READ WORLD_EXECUTE)
endforeach()

install(PROGRAMS
	"${QFW_GENERATED_BIN_DIR}/qfw-activate"
	"${QFW_GENERATED_BIN_DIR}/defw-python"
	"${QFW_GENERATED_BIN_DIR}/qfw-setup"
	"${QFW_GENERATED_BIN_DIR}/qfw-srun"
	"${QFW_GENERATED_BIN_DIR}/qfw-teardown"
	"${QFW_GENERATED_BIN_DIR}/qfw-dirsvc-start"
	"${QFW_GENERATED_BIN_DIR}/qfw-service-start"
	DESTINATION "${CMAKE_INSTALL_BINDIR}")

install(PROGRAMS
	"${QFW_GENERATED_LIBEXEC_DIR}/qfw-setup-driver"
	setup/qfw_install.sh
	DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}/qfw")

install(DIRECTORY setup/qfw_runtime
	DESTINATION "${QFW_PYTHON_INSTALL_DIR}"
	FILES_MATCHING
		PATTERN "*.py"
		PATTERN "__pycache__" EXCLUDE)
install(DIRECTORY backends/qfw_qiskit
	DESTINATION "${QFW_PYTHON_INSTALL_DIR}"
	FILES_MATCHING
		PATTERN "*.py"
		PATTERN "__pycache__" EXCLUDE)
install(DIRECTORY services/
	DESTINATION "${CMAKE_INSTALL_LIBDIR}/qfw/services"
	FILES_MATCHING
		PATTERN "*.py"
		PATTERN "*.yaml"
		PATTERN "__pycache__" EXCLUDE)
install(DIRECTORY service-apis/
	DESTINATION "${CMAKE_INSTALL_LIBDIR}/qfw/service-apis"
	FILES_MATCHING
		PATTERN "*.py"
		PATTERN "__pycache__" EXCLUDE)
install(DIRECTORY share/qfw/
	DESTINATION "${CMAKE_INSTALL_DATADIR}/qfw")
install(FILES
	share/qfw/service-manager/env.sh
	DESTINATION "${CMAKE_INSTALL_DATADIR}/qfw/service-manager")
install(FILES
	share/qfw/service-manager/systemd/qfw-dirsvc@.service
	share/qfw/service-manager/systemd/qfw-qpm@.service
	DESTINATION "${CMAKE_INSTALL_DATADIR}/qfw/service-manager/systemd")
install(DIRECTORY examples/
	DESTINATION "${CMAKE_INSTALL_DATADIR}/qfw/examples"
	FILES_MATCHING
		PATTERN "*.batch"
		PATTERN "*.md"
		PATTERN "*.py"
		PATTERN "*.sh"
		PATTERN "*.yaml"
		PATTERN "*.yml"
		PATTERN "__pycache__" EXCLUDE)

install(FILES
	services/config/container.yaml
	services/config/frontier.yaml
	DESTINATION "${CMAKE_INSTALL_DATADIR}/qfw/config/services/templates")

if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/DEFw/python/config/defw_generic.yaml")
	install(FILES DEFw/python/config/defw_generic.yaml
		DESTINATION "${CMAKE_INSTALL_DATADIR}/defw/config")
endif()

if(QFW_BUILD_BUNDLED_DEFW)
	add_subdirectory(DEFw)
endif()

enable_testing()

add_test(NAME qfw_source_runtime_smoke
	COMMAND
		"${CMAKE_COMMAND}"
		-DQFW_SOURCE_DIR=${CMAKE_CURRENT_SOURCE_DIR}
		-DQFW_BINARY_DIR=${CMAKE_CURRENT_BINARY_DIR}
		-DQFW_PYTHON=${Python3_EXECUTABLE}
		-DQFW_PYTHON_INSTALL_DIR=${QFW_PYTHON_INSTALL_DIR}
		-P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/RunQfwSourceRuntimeSmoke.cmake")

add_test(NAME qfw_install_tree_smoke
	COMMAND
		"${CMAKE_COMMAND}"
		-DQFW_BINARY_DIR=${CMAKE_CURRENT_BINARY_DIR}
		-DQFW_INSTALL_PREFIX=${CMAKE_CURRENT_BINARY_DIR}/test-install
		-DQFW_PYTHON=${Python3_EXECUTABLE}
		-DQFW_PYTHON_INSTALL_DIR=${QFW_PYTHON_INSTALL_DIR}
		-P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/RunQfwInstallTreeSmoke.cmake")
