import argparse
import importlib.util
import pathlib
import sys

import util.qpm.util_qpm as util_qpm
from fakes import FakeSchedulerContext
from svc_fake_iqm_qpm.svc_qpm import QPM
from test_fake_iqm_qpm import FakeAdmissionContext
from util.qpm.controller import clear_target_controllers


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DRIVER_PATH = REPO_ROOT / "examples" / "tests" / "test_fake_iqm_stress.py"


def _driver():
	examples_tests = str(REPO_ROOT / "examples" / "tests")
	if examples_tests not in sys.path:
		sys.path.insert(0, examples_tests)
	spec = importlib.util.spec_from_file_location(
		"qfw_fake_iqm_stress_driver", DRIVER_PATH)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_startup_scenario_matches_test_plan_defaults():
	driver = _driver()
	args = argparse.Namespace(
		scenario_set="startup",
		workers=2,
		tasks_per_worker=2,
		dispatch_depth=3,
	)

	scenarios = driver.build_scenarios(args)

	assert scenarios == [{
		"name": "startup-unlimited-fifo",
		"tier": "startup",
		"admission_policy": "unlimited",
		"scheduler_policy": "fifo",
		"workload": "short_only",
		"classical_mode": "sequential_pre",
		"workers": 1,
		"tasks_per_worker": 2,
		"dispatch_depth": 1,
		"expect_all_reservations": True,
		"expect_all_tasks": True,
	}]


def test_smoke_matrix_focuses_on_credit_and_rate():
	driver = _driver()
	args = argparse.Namespace(
		scenario_set="smoke",
		workers=2,
		tasks_per_worker=3,
		dispatch_depth=2,
	)

	scenarios = driver.build_scenarios(args)

	assert len(scenarios) == 12
	assert {item["admission_policy"] for item in scenarios} == {
		"credit",
		"rate",
	}
	assert {item["scheduler_policy"] for item in scenarios} == set(
		driver.SMOKE_SCHEDULERS)
	assert all(item["workload"] == "short_only" for item in scenarios)


def test_ordered_scheduler_aliases_use_ordered_plugin_options():
	driver = _driver()

	sjf = driver.scheduler_policy_payload("ordered_sjf")
	ljf = driver.scheduler_policy_payload("ordered_ljf")

	assert sjf["policy_name"] == "ordered"
	assert ljf["policy_name"] == "ordered"
	assert sjf["options"][str(driver.ORDER_KEY)] == driver.ORDER_SJF
	assert ljf["options"][str(driver.ORDER_KEY)] == driver.ORDER_LJF


def test_startup_scenario_runs_against_fake_qpm(monkeypatch, tmp_path):
	driver = _driver()
	clear_target_controllers()
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:1")
	monkeypatch.setenv("QFW_FAKE_QPM_MIN_SLEEP_SECONDS", "0.001")
	monkeypatch.setenv("QFW_FAKE_QPM_MAX_SLEEP_SECONDS", "0.01")
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	qpm = QPM(
		admission_context_factory=FakeAdmissionContext,
		scheduler_context_factory=FakeSchedulerContext,
	)
	args = argparse.Namespace(
		backend="fake-iqm",
		scenario_set="startup",
		workers=1,
		tasks_per_worker=2,
		dispatch_depth=1,
		harness_walltime=10,
		completion_timeout=5,
		classical_scale=0.0,
	)
	scenario = driver.build_scenarios(args)[0]
	sink = driver.ResultSink(str(tmp_path / "stress.jsonl"))

	record = driver.run_scenario(qpm, scenario, args, sink)

	assert record["status"] == "ok"
	assert record["leak_check"]["ok"] is True
	assert len(record["worker_results"]) == 1
	assert len(record["worker_results"][0]["completions"]) == 2
	assert (tmp_path / "stress.jsonl").read_text(
		encoding="utf-8").strip()
