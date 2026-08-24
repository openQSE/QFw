set(QFW_MAN1_PAGES
	man1/defw-python.1
	man1/qfw-activate.1
	man1/qfw-deactivate.1
	man1/qfw-dir-svc.1
	man1/qfw-qpm-svc.1
	man1/qfw-setup.1
	man1/qfw-srun.1
	man1/qfw-status.1
	man1/qfw-teardown.1)

set(QFW_MAN5_PAGES
	man5/qfw-device-access.yaml.5
	man5/qfw-runtime.yaml.5
	man5/qfw-services.yaml.5
	man5/qfw-site.yaml.5)

set(QFW_MAN7_PAGES
	man7/qfw.7
	man7/qfw-service-lifecycle.7)

foreach(qfw_man_section RANGE 1 9)
	set(qfw_man_list "QFW_MAN${qfw_man_section}_PAGES")
	foreach(qfw_man_page IN LISTS ${qfw_man_list})
		install(FILES "${qfw_man_page}"
			DESTINATION "${CMAKE_INSTALL_MANDIR}/man${qfw_man_section}"
			COMPONENT documentation)
	endforeach()
endforeach()
