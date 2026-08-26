import json
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_TESTS = REPOSITORY_ROOT / "examples" / "tests"
sys.path.insert(0, str(EXAMPLE_TESTS))

from qfw_example_report import (
    emit_result,
    format_console_record,
    parse_console_records,
)


def parse_console_result(output):
    prefix = "QFW_EXAMPLE_RESULT "
    assert output.startswith(prefix)
    return json.loads(output[len(prefix):])


def test_example_result_is_pretty_on_stdout_and_jsonl_in_file(
        tmp_path, monkeypatch, capsys):
    result_path = tmp_path / "result.jsonl"
    monkeypatch.setenv("QFW_EXAMPLE_RESULT_FILE", str(result_path))

    emitted = emit_result(
        "format-smoke",
        parameters={"qubits": 4},
        metrics={"counts": {"0000": 8, "1111": 8}},
    )

    output = capsys.readouterr().out
    assert len(output.splitlines()) > 2
    assert parse_console_result(output) == emitted

    file_lines = result_path.read_text(encoding="utf-8").splitlines()
    assert len(file_lines) == 1
    assert json.loads(file_lines[0]) == emitted


def test_wrapper_result_is_pretty_on_stdout():
    script = """
source examples/qfw_example_common.sh
QFW_EXAMPLE_NAME=format-wrapper-smoke
QFW_EXAMPLE_ARGS=(one two)
qfw_example_emit finish ok 0 3 0
"""

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert len(result.stdout.splitlines()) > 2
    record = parse_console_result(result.stdout)
    assert record["schema"] == "qfw-example-wrapper-v1"
    assert record["example"] == "format-wrapper-smoke"
    assert record["args"] == ["one", "two"]


def test_execution_options_select_site_runtime_without_local_services(
        tmp_path):
    site = tmp_path / "site.yaml"
    site.write_text("directory-service: {}\n", encoding="utf-8")
    script = f"""
source examples/qfw_example_common.sh
qfw-setup() {{ printf 'setup:%s\\n' "$*"; }}
qfw-srun() {{ :; }}
qfw-teardown() {{ :; }}
qfw_example_parse_execution_options \\
  --service-mode site --backend nwqsim \\
  --site-config {site} payload
printf 'selection:%s:%s:%s\\n' \\
  "$QFW_EXAMPLE_SERVICE_MODE" "$QFW_EXAMPLE_BACKEND" \\
  "${{QFW_EXAMPLE_REMAINING_ARGS[*]}}"
qfw_example_setup_backend_service nwqsim
"""

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "selection:site:nwqsim:payload" in result.stdout
    assert f"--site-config {site}" in result.stdout
    assert "--runtime-config" not in result.stdout
    assert "--profile" not in result.stdout


def test_execution_options_select_installed_local_profile():
    script = """
source examples/qfw_example_common.sh
qfw-setup() { printf 'setup:%s\n' "$*"; }
qfw-srun() { :; }
qfw-teardown() { :; }
qfw_example_parse_execution_options \
  --service-mode local --backend nwqsim payload
qfw_example_setup_backend_service nwqsim
"""

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "setup:--profile local --service-id nwqsim" in result.stdout


def test_execution_options_reject_unknown_service_mode():
    script = """
source examples/qfw_example_common.sh
qfw_example_parse_execution_options --service-mode unknown
"""

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "must be local or site" in result.stderr


def test_terminal_result_helper_requires_successful_finish(tmp_path):
    success = tmp_path / "success.jsonl"
    missing = tmp_path / "missing.jsonl"
    success.write_text(json.dumps({
        "kind": "wrapper",
        "event": "finish",
        "status": "ok",
        "rc": 0,
    }) + "\n", encoding="utf-8")
    missing.write_text(json.dumps({
        "kind": "example",
        "status": "ok",
    }) + "\n", encoding="utf-8")
    script = f"""
source examples/qfw_example_common.sh
qfw_example_result_is_terminal_success {success}
if qfw_example_result_is_terminal_success {missing}; then
  exit 9
fi
"""

    subprocess.run(
        ["bash", "-c", script],
        cwd=REPOSITORY_ROOT,
        check=True,
    )


def test_run_all_rejects_unknown_selected_case():
    result = subprocess.run(
        ["bash", "examples/qfw_run_all.sh", "--tests", "not-a-test"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "unknown --tests case" in result.stderr


def test_console_record_parser_accepts_pretty_and_compact_records():
    prefix = "QFW_EXAMPLE_RESERVATION"
    reserve = {"kind": "reserve", "decision": {"reservation_id": 17}}
    release = {"kind": "release", "reservation_id": 17}
    output = "\n".join([
        "unrelated output",
        format_console_record(prefix, reserve),
        prefix + " " + json.dumps(release, sort_keys=True),
    ])

    assert parse_console_records(output, prefix) == [reserve, release]


def test_embedded_structured_emitters_request_pretty_json():
    emitters = {
        "examples/qfw_example_common.sh": {
            "QFW_EXAMPLE_RESULT": 1,
        },
        "examples/qfw_slurm_driver.sh": {
            "QFW_SLURM_DRIVER_RESULT": 1,
        },
        "examples/qfw_iqm_chem_driver.sh": {
            "QFW_CHEM_CREDENTIAL_PREFLIGHT": 2,
        },
    }

    for relative_path, prefixes in emitters.items():
        source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        for prefix, expected_count in prefixes.items():
            marker = f'"{prefix} " + json.dumps('
            chunks = source.split(marker)[1:]
            assert len(chunks) == expected_count
            for chunk in chunks:
                arguments = chunk.split("sort_keys=True", 1)[0]
                assert "indent=2" in arguments
