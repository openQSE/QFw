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
include(ExternalProject)

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
set(QFW_EXTERNAL_BUILD_DIR "${CMAKE_CURRENT_BINARY_DIR}/external-build")
set(QFW_EXTERNAL_STAGE_DIR "${CMAKE_CURRENT_BINARY_DIR}/external-stage")
file(MAKE_DIRECTORY
	"${QFW_GENERATED_BIN_DIR}"
	"${QFW_GENERATED_LIBEXEC_DIR}"
	"${QFW_EXTERNAL_BUILD_DIR}"
	"${QFW_EXTERNAL_STAGE_DIR}")

function(qfw_require_submodule marker description)
	if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${marker}")
		message(FATAL_ERROR
			"Missing ${description} submodule content at ${marker}. "
			"Run: git submodule update --init --recursive")
	endif()
endfunction()

function(qfw_install_python_source_package package_name source_dir)
	qfw_require_submodule("${source_dir}/pyproject.toml" "${package_name}")
	qfw_require_submodule(
		"${source_dir}/src/${package_name}/__init__.py"
		"${package_name}")
	install(DIRECTORY "${source_dir}/src/${package_name}/"
		DESTINATION "${QFW_PYTHON_INSTALL_DIR}/${package_name}"
		FILES_MATCHING
			PATTERN "*.py"
			PATTERN "*.json"
			PATTERN "__pycache__" EXCLUDE)
endfunction()

function(qfw_add_external_cmake_package target_name source_dir)
	qfw_require_submodule("${source_dir}/CMakeLists.txt" "${target_name}")
	ExternalProject_Add("${target_name}"
		SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/${source_dir}"
		BINARY_DIR "${QFW_EXTERNAL_BUILD_DIR}/${target_name}"
		INSTALL_DIR "${QFW_EXTERNAL_STAGE_DIR}"
		BUILD_ALWAYS TRUE
		CMAKE_ARGS
			"-DCMAKE_INSTALL_PREFIX=<INSTALL_DIR>"
			"-DCMAKE_INSTALL_BINDIR=${CMAKE_INSTALL_BINDIR}"
			"-DCMAKE_INSTALL_INCLUDEDIR=${CMAKE_INSTALL_INCLUDEDIR}"
			"-DCMAKE_INSTALL_LIBDIR=${CMAKE_INSTALL_LIBDIR}"
			"-DCMAKE_INSTALL_MANDIR=${CMAKE_INSTALL_MANDIR}"
			"-DCMAKE_INSTALL_DOCDIR=${CMAKE_INSTALL_DOCDIR}"
			"-DCMAKE_PREFIX_PATH=<INSTALL_DIR>"
			"-DPython3_EXECUTABLE=${Python3_EXECUTABLE}"
			${ARGN})
endfunction()

qfw_install_python_source_package(qhw_data external/qhw-data)
qfw_install_python_source_package(qhw_iqm external/qhw-iqm)

qfw_add_external_cmake_package(qfw_external_qhw_admission
	external/qhw-admission
	"-DQHW_ADM_BUILD_TESTS=OFF"
	"-DQHW_ADM_BUILD_PYTHON=ON"
	"-DQHW_ADM_BUILD_SWIG=ON"
	"-DQHW_ADM_INSTALL_PYTHON_ARTIFACTS=ON"
	"-DQHW_ADM_PYTHON_INSTALL_DIR=<INSTALL_DIR>/${QFW_PYTHON_INSTALL_DIR}")
qfw_add_external_cmake_package(qfw_external_qhw_scheduler
	external/qhw-scheduler
	"-DQHW_SCHED_BUILD_TESTS=OFF"
	"-DQHW_SCHED_BUILD_PYTHON=ON"
	"-DQHW_SCHED_BUILD_SWIG=ON"
	"-DQHW_SCHED_INSTALL_PYTHON_ARTIFACTS=ON"
	"-DQHW_SCHED_PYTHON_INSTALL_DIR=<INSTALL_DIR>/${QFW_PYTHON_INSTALL_DIR}")
add_custom_target(qfw_external_runtime ALL
	DEPENDS
		qfw_external_qhw_admission
		qfw_external_qhw_scheduler)

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
configure_file(
	cmake/qfw-service-plane.in
	"${QFW_GENERATED_BIN_DIR}/qfw-service-plane"
	@ONLY)
foreach(qfw_role_command IN ITEMS qfw-dir-svc qfw-qpm-svc)
	if(qfw_role_command STREQUAL "qfw-dir-svc")
		set(QFW_SERVICE_LIFECYCLE_ROLE "directory")
	else()
		set(QFW_SERVICE_LIFECYCLE_ROLE "qpm")
	endif()
	configure_file(
		cmake/qfw-role-service.in
		"${QFW_GENERATED_BIN_DIR}/${qfw_role_command}"
		@ONLY)
endforeach()
file(CHMOD
	"${QFW_GENERATED_BIN_DIR}/qfw-activate"
	"${QFW_GENERATED_BIN_DIR}/defw-python"
	"${QFW_GENERATED_BIN_DIR}/qfw-service-plane"
	"${QFW_GENERATED_BIN_DIR}/qfw-dir-svc"
	"${QFW_GENERATED_BIN_DIR}/qfw-qpm-svc"
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
	"${QFW_GENERATED_BIN_DIR}/qfw-service-plane"
	"${QFW_GENERATED_BIN_DIR}/qfw-dir-svc"
	"${QFW_GENERATED_BIN_DIR}/qfw-qpm-svc"
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
		PATTERN "*.json"
		PATTERN "*.txt"
		PATTERN "*.in"
		PATTERN "dev-config" EXCLUDE
		PATTERN "__pycache__" EXCLUDE)
install(DIRECTORY service-apis/
	DESTINATION "${CMAKE_INSTALL_LIBDIR}/qfw/service-apis"
	FILES_MATCHING
		PATTERN "*.py"
		PATTERN "__pycache__" EXCLUDE)
install(CODE
	"file(REMOVE_RECURSE \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_LIBDIR}/qfw/service-apis/api_qpm\")")
install(DIRECTORY share/qfw/
	DESTINATION "${CMAKE_INSTALL_DATADIR}/qfw")
install(CODE
	"file(REMOVE
		\"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/qfw/config/services/qfw_services.yaml\"
		\"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/qfw/config/services/service-runtime.yaml\"
		\"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/qfw/config/device/device-access.yaml\")")
install(CODE
	"file(REMOVE_RECURSE
		\"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/qfw/config/services/templates\"
		\"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/qfw/config/device\"
		\"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_LIBDIR}/qfw/services/config\")")
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
		PATTERN "*.yaml"
		PATTERN "*.yml"
		PATTERN "__pycache__" EXCLUDE)
install(PROGRAMS
	examples/qfw_chem_app.sh
	examples/qfw_example_common.sh
	examples/qfw_fake_iqm_stress.sh
	examples/qfw_ghz.sh
	examples/qfw_init_test.sh
	examples/qfw_iqm_chem_driver.sh
	examples/qfw_iqm_chem_site_run.sh
	examples/qfw_iqm_chem_smoke.sh
	examples/qfw_iqm_site_services.sh
	examples/qfw_long_running_qpm.sh
	examples/qfw_mpi_smoke.sh
	examples/qfw_pennylane.sh
	examples/qfw_qaoa.sh
	examples/qfw_qiskit_simple.sh
	examples/qfw_qiskit_vqe.sh
	examples/qfw_run_all.sh
	examples/qfw_shim_smoke.sh
	examples/qfw_slurm_driver.sh
	examples/qfw_supermarq.sh
	DESTINATION "${CMAKE_INSTALL_DATADIR}/qfw/examples")

install(DIRECTORY "${QFW_EXTERNAL_STAGE_DIR}/"
	DESTINATION "."
	USE_SOURCE_PERMISSIONS)

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
