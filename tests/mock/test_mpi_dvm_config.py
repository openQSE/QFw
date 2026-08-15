from util.mpi import build_mpi_command


def test_explicit_empty_dvm_disables_environment_dvm(monkeypatch):
	monkeypatch.setenv("QFW_DVM_URI_PATH", "/tmp/qfw-dvm-uri")

	command = build_mpi_command("simulator", dvm_uri="", config={})

	assert "--dvm" not in command


def test_unspecified_dvm_uses_environment_dvm(monkeypatch):
	monkeypatch.setenv("QFW_DVM_URI_PATH", "/tmp/qfw-dvm-uri")

	command = build_mpi_command("simulator", config={})

	assert command[command.index("--dvm") + 1] == "file:/tmp/qfw-dvm-uri"
