from util.mpi import build_mpi_command


def test_explicit_empty_dvm_disables_environment_dvm(monkeypatch):
	monkeypatch.setenv("QFW_DVM_URI_PATH", "/tmp/qfw-dvm-uri")

	command = build_mpi_command("simulator", dvm_uri="", config={})

	assert "--dvm" not in command


def test_unspecified_dvm_uses_environment_dvm(monkeypatch):
	monkeypatch.setenv("QFW_DVM_URI_PATH", "/tmp/qfw-dvm-uri")

	command = build_mpi_command("simulator", config={})

	assert command[command.index("--dvm") + 1] == "file:/tmp/qfw-dvm-uri"


# --- ppr:N:l3cache on a host with no L3 cache ---------------------------------

def test_l3cache_falls_back_to_the_last_level_cache():
	# Docker Desktop's VM on Apple Silicon reports L1 and L2 only, and
	# Open MPI cannot place ppr:1:l3cache there at all.
	from util.mpi import effective_map_by

	assert effective_map_by("ppr:1:l3cache", levels={1, 2}) == "ppr:1:l2cache"
	assert effective_map_by(
		"ppr:2:l3cache:pe=2", levels={1, 2}) == "ppr:2:l2cache:pe=2"


def test_l3cache_is_kept_where_the_host_has_one():
	from util.mpi import effective_map_by

	assert effective_map_by(
		"ppr:1:l3cache", levels={1, 2, 3}) == "ppr:1:l3cache"


def test_l3cache_is_kept_when_the_host_reports_no_caches(monkeypatch):
	import util.mpi as mpi

	monkeypatch.setattr(mpi, "cache_levels", lambda: None)

	assert mpi.effective_map_by("ppr:1:l3cache") == "ppr:1:l3cache"


def test_other_mappings_are_left_alone():
	from util.mpi import effective_map_by

	assert effective_map_by("ppr:1:node", levels={1, 2}) == "ppr:1:node"


def test_cache_levels_reads_the_sysfs_tree(tmp_path):
	from util.mpi import cache_levels

	for index, level in enumerate((1, 1, 2)):
		entry = tmp_path / f"index{index}"
		entry.mkdir()
		(entry / "level").write_text(f"{level}\n", encoding="utf-8")
	(tmp_path / "uevent").write_text("", encoding="utf-8")

	assert cache_levels(str(tmp_path)) == {1, 2}
	assert cache_levels(str(tmp_path / "missing")) is None


def test_the_mpirun_command_carries_the_fallback(monkeypatch):
	import util.mpi as mpi

	monkeypatch.setattr(mpi, "cache_levels", lambda: {1, 2})

	command = mpi.build_mpi_command(
		"simulator", dvm_uri="",
		config={"mpi-launch": {"map-by": "ppr:1:l3cache"}})

	assert command[command.index("--map-by") + 1] == "ppr:1:l2cache"
