#!/usr/bin/env python3

import argparse
import json
import os
import threading
import time
import traceback
from copy import deepcopy
from types import SimpleNamespace

from defw_app_util import defw_get_directory_service
from defw_exception import DEFwError, DEFwNotReady
from qfw_qiskit.qpm_resolver import QPMResolver
from qfw_qiskit.qpm_selection import qpm_selection_for_provider
from qfw_example_report import emit_result, jsonable


FAKE_PROVIDER = "fake-iqm"
FAKE_TARGET = "fake-iqm-20q"
ORDER_KEY = 300
ORDER_PRIORITY = 1
ORDER_SJF = 2
ORDER_LJF = 3
ORDER_FIFO = 4
ORDER_ROUND_ROBIN = 5
SCHEDULER_ALIASES = {
	"fifo": {"policy_name": "fifo", "options": {}},
	"priority": {"policy_name": "priority", "options": {}},
	"round_robin": {"policy_name": "round_robin", "options": {}},
	"ordered": {
		"policy_name": "ordered",
		"options": {str(ORDER_KEY): ORDER_FIFO},
	},
	"ordered_sjf": {
		"policy_name": "ordered",
		"options": {str(ORDER_KEY): ORDER_SJF},
	},
	"ordered_ljf": {
		"policy_name": "ordered",
		"options": {str(ORDER_KEY): ORDER_LJF},
	},
}
SMOKE_SCHEDULERS = (
	"fifo",
	"priority",
	"round_robin",
	"ordered_sjf",
	"ordered_ljf",
	"ordered",
)
SMOKE_ADMISSION = ("credit", "rate")
LIMIT_ADMISSION = ("credit", "rate")
WORKLOAD_SHAPES = (
	"short_only",
	"long_only",
	"mixed_short_long",
	"mixed_job_types",
	"standalone",
)
CLASSICAL_MODES = ("sequential_pre", "sequential_post", "parallel")


def parse_args():
	parser = argparse.ArgumentParser(
		description="Run fake IQM admission/scheduler stress scenarios.")
	parser.add_argument(
		"--scenario-set",
		choices=(
			"startup", "smoke", "admission", "workload", "hybrid",
			"scheduler", "all"),
		default="startup")
	parser.add_argument("--workers", type=int, default=2)
	parser.add_argument("--tasks-per-worker", type=int, default=2)
	parser.add_argument("--waves", type=int, default=1)
	parser.add_argument("--backend", default=FAKE_PROVIDER)
	parser.add_argument("--system-up-timeout", type=int, default=40)
	parser.add_argument("--completion-timeout", type=int, default=30)
	parser.add_argument("--harness-walltime", type=int, default=120)
	parser.add_argument("--dispatch-depth", type=int, default=1)
	parser.add_argument("--classical-scale", type=float, default=1.0)
	parser.add_argument("--result-file", default="")
	return parser.parse_args()


def resolve_qpm(backend, timeout):
	selection = qpm_selection_for_provider(
		backend, default_provider=FAKE_PROVIDER)
	dirsvc = defw_get_directory_service()
	resolver = QPMResolver.from_environment(dirsvc=dirsvc)
	request = {
		"service_type": "qfw.qpm",
		"qpm_type": selection["qpm_type"],
		"qpm_capabilities": selection["qpm_capabilities"],
		"provider": selection["provider"],
		"timeout": timeout,
	}
	clients = {
		name: resolver.connect(binding_name=binding, **request)
		for name, binding in (
			("execution", "execution"),
			("admission", "admission"),
			("policy", "admission-policy"),
			("scheduler", "scheduler"),
			("telemetry", "telemetry"),
			("control", "control"),
		)
	}
	qpm = SimpleNamespace(
		async_run=clients["execution"].async_run,
		read_cq=clients["execution"].read_cq,
		reserve=clients["admission"].reserve,
		release=clients["admission"].release,
		configure_device_profile=clients["policy"].configure_device_profile,
		get_device_profile=clients["policy"].get_device_profile,
		set_admission_policy=clients["policy"].set_admission_policy,
		configure_dispatch_limits=(
			clients["scheduler"].configure_dispatch_limits),
		configure_scheduler_policy=(
			clients["scheduler"].configure_scheduler_policy),
		get_scheduler_queue_state=(
			clients["scheduler"].get_scheduler_queue_state),
		pause_execution_target=(
			clients["scheduler"].pause_execution_target),
		resume_execution_target=(
			clients["scheduler"].resume_execution_target),
		get_capacity_snapshot=clients["telemetry"].get_capacity_snapshot,
		is_ready=clients["control"].is_ready,
	)
	return selection["provider"], qpm


def wait_for_qpm(qpm, timeout):
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		try:
			if qpm.is_ready().get("ready"):
				return
		except DEFwNotReady:
			time.sleep(1)
			continue
	time.sleep(1)
	raise DEFwError(f"QPM did not become ready within {timeout} seconds")


def build_scenarios(args):
	scenarios = []
	if args.scenario_set in ("startup", "all"):
		scenarios.append(_scenario(
			"startup-unlimited-fifo",
			"startup",
			"unlimited",
			"fifo",
			"short_only",
			"sequential_pre",
			workers=1,
			tasks_per_worker=2,
			dispatch_depth=1))
	if args.scenario_set in ("smoke", "all"):
		for admission in SMOKE_ADMISSION:
			for scheduler in SMOKE_SCHEDULERS:
				scenarios.append(_scenario(
					f"smoke-{admission}-{scheduler}",
					"smoke",
					admission,
					scheduler,
					"short_only",
					"sequential_pre",
					workers=args.workers,
					tasks_per_worker=args.tasks_per_worker,
					dispatch_depth=args.dispatch_depth))
	if args.scenario_set in ("admission", "all"):
		scenarios.extend(admission_limit_scenarios())
	if args.scenario_set in ("workload", "all"):
		for workload in WORKLOAD_SHAPES:
			scenarios.append(_scenario(
				f"workload-credit-fifo-{workload}",
				"workload",
				"credit",
				"fifo",
				workload,
				"sequential_pre",
				workers=args.workers,
				tasks_per_worker=args.tasks_per_worker,
				dispatch_depth=args.dispatch_depth,
				profile_overrides=stress_capacity_profile(),
				reservation_count_margin=1,
				waves=args.waves))
	if args.scenario_set in ("hybrid", "all"):
		for admission in SMOKE_ADMISSION:
			for classical_mode in CLASSICAL_MODES:
				scenarios.append(_scenario(
					f"hybrid-{admission}-fifo-{classical_mode}",
					"hybrid",
					admission,
					"fifo",
					"mixed_job_types",
					classical_mode,
					workers=args.workers,
					tasks_per_worker=args.tasks_per_worker,
					dispatch_depth=args.dispatch_depth,
					profile_overrides=stress_capacity_profile(),
					reservation_count_margin=1,
					waves=args.waves))
	if args.scenario_set in ("scheduler", "all"):
		for scheduler in SMOKE_SCHEDULERS:
			scenarios.append(_scenario(
				f"scheduler-credit-{scheduler}",
				"scheduler",
				"credit",
				scheduler,
				"mixed_job_types",
				"parallel",
				workers=args.workers,
				tasks_per_worker=args.tasks_per_worker,
				dispatch_depth=1,
				waves=args.waves,
				profile_overrides=stress_capacity_profile(),
				reservation_count_margin=1,
				queue_all_before_resume=True,
				assert_scheduler_order=True))
	return scenarios


def stress_capacity_profile():
	return {
		"total_credits": 512,
		"device_rate": 16384,
		"concurrent_jobs": 8,
	}


def _scenario(name, tier, admission, scheduler, workload, classical_mode,
	      workers, tasks_per_worker, dispatch_depth, waves=1,
	      expect_all_reservations=True, expect_all_tasks=True,
	      expected_reservation_statuses=None, profile_overrides=None,
	      walltime_ns=None, queue_all_before_resume=False,
	      assert_scheduler_order=False, reservation_count_margin=0):
	return {
		"name": name,
		"tier": tier,
		"admission_policy": admission,
		"scheduler_policy": scheduler,
		"workload": workload,
		"classical_mode": classical_mode,
		"workers": workers,
		"tasks_per_worker": tasks_per_worker,
		"waves": waves,
		"dispatch_depth": dispatch_depth,
		"expect_all_reservations": expect_all_reservations,
		"expect_all_tasks": expect_all_tasks,
		"expected_reservation_statuses": (
			list(expected_reservation_statuses or [])),
		"profile_overrides": dict(profile_overrides or {}),
		"walltime_ns": walltime_ns,
		"queue_all_before_resume": queue_all_before_resume,
		"assert_scheduler_order": assert_scheduler_order,
		"reservation_count_margin": reservation_count_margin,
	}


def admission_limit_scenarios():
	return [
		_scenario(
			"admission-credit-accept-exact",
			"admission",
			"credit",
			"fifo",
			"short_only",
			"sequential_pre",
			workers=1,
			tasks_per_worker=1,
			dispatch_depth=1,
			expected_reservation_statuses=["accepted"],
			profile_overrides={"total_credits": 1}),
		_scenario(
			"admission-credit-delay-remaining",
			"admission",
			"credit",
			"fifo",
			"short_only",
			"sequential_pre",
			workers=2,
			tasks_per_worker=1,
			dispatch_depth=1,
			expect_all_reservations=False,
			expected_reservation_statuses=["accepted", "delayed"],
			profile_overrides={"total_credits": 1}),
		_scenario(
			"admission-credit-reject-limit",
			"admission",
			"credit",
			"fifo",
			"long_only",
			"sequential_pre",
			workers=1,
			tasks_per_worker=1,
			dispatch_depth=1,
			expect_all_reservations=False,
			expect_all_tasks=False,
			expected_reservation_statuses=["rejected"],
			profile_overrides={"total_credits": 1}),
		_scenario(
			"admission-rate-accept-exact",
			"admission",
			"rate",
			"fifo",
			"short_only",
			"sequential_pre",
			workers=1,
			tasks_per_worker=1,
			dispatch_depth=1,
			expected_reservation_statuses=["accepted"],
			profile_overrides={
				"device_rate": 60,
				"concurrent_jobs": 1,
			},
			walltime_ns=1_000_000_000),
		_scenario(
			"admission-rate-delay-remaining",
			"admission",
			"rate",
			"fifo",
			"short_only",
			"sequential_pre",
			workers=2,
			tasks_per_worker=1,
			dispatch_depth=1,
			expect_all_reservations=False,
			expected_reservation_statuses=["accepted", "delayed"],
			profile_overrides={
				"device_rate": 60,
				"concurrent_jobs": 1,
			},
			walltime_ns=1_000_000_000),
		_scenario(
			"admission-rate-reject-limit",
			"admission",
			"rate",
			"fifo",
			"long_only",
			"sequential_pre",
			workers=1,
			tasks_per_worker=1,
			dispatch_depth=1,
			expect_all_reservations=False,
			expect_all_tasks=False,
			expected_reservation_statuses=["rejected"],
			profile_overrides={
				"device_rate": 60,
				"concurrent_jobs": 1,
			},
			walltime_ns=1_000_000_000),
	]


def run_scenario(qpm, scenario, args, result_sink):
	start_ns = time.time_ns()
	deadline = time.monotonic() + args.harness_walltime
	record = {
		"schema": "qfw-fake-iqm-stress-v1",
		"kind": "fake-iqm-stress-scenario",
		"scenario": deepcopy(scenario),
		"status": "running",
		"start_ns": start_ns,
		"policy": {},
		"reservation_decisions": [],
		"worker_results": [],
		"waves": [],
		"errors": [],
	}
	try:
		apply_runtime_configuration(qpm, scenario, record)
		record["capacity_before"] = qpm.get_capacity_snapshot()
		record["queue_before"] = qpm.get_scheduler_queue_state(
			include_restricted=True)
		for wave_index in range(max(1, int(scenario.get("waves", 1)))):
			wave = run_wave(qpm, scenario, args, deadline, wave_index)
			record["waves"].append(wave)
			record["reservation_decisions"].extend(
				deepcopy(wave["reservation_decisions"]))
			record["worker_results"].extend(
				deepcopy(wave["worker_results"]))
			if wave.get("status") != "ok":
				raise DEFwError(
					f"wave failed: scenario={scenario['name']} "
					f"wave_index={wave_index}")
		record["expected_dispatch_order"] = [
			wave.get("expected_dispatch_order")
			for wave in record["waves"]
		]
		record["actual_dispatch_order"] = [
			wave.get("actual_dispatch_order")
			for wave in record["waves"]
		]
		record["completion_order"] = [
			qtask_id
			for wave in record["waves"]
			for qtask_id in wave.get("actual_dispatch_order", [])
		]
		record["releases"] = [
			release
			for wave in record["waves"]
			for release in wave.get("releases", [])
		]
		record["capacity_after"] = qpm.get_capacity_snapshot()
		record["queue_after"] = qpm.get_scheduler_queue_state(
			include_restricted=True)
		record["leak_check"] = leak_check(
			record["capacity_after"], record["queue_after"])
		if not record["leak_check"]["ok"]:
			raise DEFwError(
				f"final leak check failed: {record['leak_check']}")
		if time.monotonic() > deadline:
			raise DEFwError(
				f"scenario exceeded harness walltime: "
				f"{args.harness_walltime}s")
		record["status"] = "ok"
	except Exception as error:
		record["status"] = "failed"
		record["errors"].append({
			"type": type(error).__name__,
			"message": str(error),
			"traceback": traceback.format_exc(),
		})
	finally:
		record["end_ns"] = time.time_ns()
		record["duration_sec"] = (
			record["end_ns"] - record["start_ns"]) / 1_000_000_000.0
		result_sink.write(record)
	return record


def run_wave(qpm, scenario, args, deadline, wave_index):
	wave = {
		"wave_index": wave_index,
		"status": "running",
		"reservation_decisions": [],
		"worker_results": [],
		"submitted_tasks": [],
		"releases": [],
		"errors": [],
	}
	active = []
	try:
		reservations = reserve_workers(qpm, scenario, wave_index)
		wave["reservation_decisions"] = [
			deepcopy(item["decision"]) for item in reservations
		]
		validate_reservation_decisions(scenario, reservations)
		active = [
			item for item in reservations
			if decision_status(item["decision"]) == "accepted"
		]
		wave["expected_reservation_count"] = len(reservations)
		wave["accepted_reservation_count"] = len(active)
		if scenario.get("expect_all_tasks") and active:
			if scenario.get("queue_all_before_resume"):
				wave["worker_results"] = run_workers_queued(
					qpm, scenario, active, args, deadline, wave)
			else:
				wave["worker_results"] = run_workers(
					qpm, scenario, active, args, deadline)
		wave["submitted_tasks"] = submitted_tasks_from_workers(
			wave["worker_results"])
		wave["expected_dispatch_order"] = expected_dispatch_order(
			scenario, wave["submitted_tasks"])
		wave["actual_dispatch_order"] = actual_dispatch_order(
			wave["worker_results"])
		validate_dispatch_order(scenario, wave)
		wave["status"] = "ok"
	except Exception as error:
		wave["status"] = "failed"
		wave["errors"].append({
			"type": type(error).__name__,
			"message": str(error),
			"traceback": traceback.format_exc(),
		})
	finally:
		wave["releases"] = release_workers(qpm, active)
	return wave


def validate_reservation_decisions(scenario, reservations):
	statuses = [
		decision_status(item["decision"]) for item in reservations
	]
	expected = scenario.get("expected_reservation_statuses") or []
	if expected and statuses != expected:
		raise DEFwError(
			"reservation decisions did not match expectation: "
			f"expected={expected} actual={statuses}")
	active_count = sum(1 for status in statuses if status == "accepted")
	if scenario["expect_all_reservations"] and active_count != len(reservations):
		raise DEFwError(
			"not all reservations were accepted: "
			f"accepted={active_count} total={len(reservations)} "
			f"statuses={statuses}")


def decision_status(decision):
	return str(decision.get("status") or decision.get("decision") or "").lower()


def apply_runtime_configuration(qpm, scenario, record):
	profile = fake_device_profile(qpm, scenario)
	profile_result = qpm.configure_device_profile(profile=profile)
	policy_name = scenario["admission_policy"]
	capacity = {}
	if policy_name == "credit":
		capacity["total_credits"] = profile["total_credits"]
	elif policy_name == "rate":
		capacity.update({
			"device_rate": profile["device_rate"],
			"time_span_ns": profile["time_span_ns"],
		})
	admission_result = qpm.set_admission_policy(configuration={
		"policy": {
			"name": policy_name,
			"options": admission_policy_options(scenario),
		},
		"estimator": {"name": "baseline", "options": {}},
		"baseline": profile["baseline"],
		"capacity": capacity,
	})
	scheduler_payload = scheduler_policy_payload(
		scenario["scheduler_policy"])
	scheduler_result = qpm.configure_scheduler_policy(
		configuration=scheduler_payload)
	dispatch_result = qpm.configure_dispatch_limits(
		limits={"max_inflight": scenario["dispatch_depth"]})
	record["policy"] = {
		"device_profile": profile_result,
		"admission": admission_result,
		"scheduler": scheduler_result,
		"dispatch": dispatch_result,
	}


def fake_device_profile(qpm, scenario):
	current = dict((qpm.get_device_profile() or {}).get("device_profile") or {})
	device_id = current.get("device_id", FAKE_TARGET)
	profile = {
		"device_id": device_id,
		"external_device_id": FAKE_TARGET,
		"max_qubits": 20,
		"max_shots": 10_000,
		"time_span_ns": 60_000_000_000,
		"baseline": {
			"qubit_count": 4,
			"depth": 10,
			"one_q_gate_count": 10,
			"two_q_gate_count": 5,
			"measurement_count": 4,
			"shots": 128,
		},
		"one_q_gate_ns": 20,
		"two_q_gate_ns": 100,
		"measurement_ns": 1000,
		"one_q_gate_transfer_ns": 1,
		"two_q_gate_transfer_ns": 4,
		"measurement_transfer_ns": 10,
		"compile_ns": 1000,
		"control_overhead_ns": 200,
		"provider_overhead_ns": 300,
		"total_credits": 64,
		"device_rate": 512,
		"concurrent_jobs": 8,
		"default_ttl_ns": 60_000_000_000,
		"max_provider_queue_depth": 8,
	}
	_deep_update(profile, scenario.get("profile_overrides") or {})
	return profile


def _deep_update(target, updates):
	for key, value in updates.items():
		if isinstance(value, dict) and isinstance(target.get(key), dict):
			_deep_update(target[key], value)
		else:
			target[key] = value
	return target


def admission_policy_options(scenario):
	if scenario["admission_policy"] == "unlimited":
		return {}
	if scenario["admission_policy"] == "credit":
		return {}
	if scenario["admission_policy"] == "rate":
		return {}
	return {}


def scheduler_policy_payload(name):
	try:
		import qhw_scheduler
		order_key = getattr(qhw_scheduler, "QHW_SCHED_OPT_ORDER_KEY")
		order_values = {
			"ordered": getattr(qhw_scheduler, "QHW_SCHED_ORDER_FIFO"),
			"ordered_sjf": getattr(qhw_scheduler, "QHW_SCHED_ORDER_SJF"),
			"ordered_ljf": getattr(qhw_scheduler, "QHW_SCHED_ORDER_LJF"),
		}
	except Exception:
		order_key = ORDER_KEY
		order_values = {
			"ordered": ORDER_FIFO,
			"ordered_sjf": ORDER_SJF,
			"ordered_ljf": ORDER_LJF,
		}
	if name in ("ordered", "ordered_sjf", "ordered_ljf"):
		return {
			"policy_name": "ordered",
			"options": {str(order_key): order_values[name]},
		}
	return dict(SCHEDULER_ALIASES[name])


def reserve_workers(qpm, scenario, wave_index=0):
	reservations = []
	for worker_index in range(scenario["workers"]):
		tasks = build_workload(
			scenario["workload"], worker_index,
			scenario["tasks_per_worker"])
		request = reservation_request(
			scenario, worker_index, tasks, wave_index)
		decision = qpm.reserve(request=request)
		reservations.append({
			"worker_index": worker_index,
			"wave_index": wave_index,
			"tasks": tasks,
			"request": request,
			"decision": decision,
		})
	return reservations


def reservation_request(scenario, worker_index, tasks, wave_index=0):
	max_qubits = max(task["num_qubits"] for task in tasks)
	max_depth = max(task["depth"] for task in tasks)
	shots = max(task["shots"] for task in tasks)
	task_count = len(tasks) + int(scenario.get("reservation_count_margin", 0))
	return {
		"owner": {"user": os.environ.get("USER", "fake-iqm-stress")},
		"job_id": (
			f"{scenario['name']}-wave-{wave_index}-worker-{worker_index}"),
		"allocation_id": os.environ.get("SLURM_JOB_ID", scenario["name"]),
		"scope_id": scenario["name"],
		"target_device_id": FAKE_TARGET,
		"num_qubits": max_qubits,
		"walltime_ns": (
			scenario.get("walltime_ns") or
			scenario_walltime_ns(scenario, tasks)),
		"ttl_ns": 120_000_000_000,
		"workload": {
			"example": "fake-iqm-stress",
			"tier": scenario["tier"],
			"category": scenario["workload"],
			"classical_mode": scenario["classical_mode"],
			"wave_index": wave_index,
		},
		"run_context": {"operation": "async_run"},
		"task_class": {
			"count": task_count,
			"qubit_count": max_qubits,
			"depth": max_depth,
			"one_q_gate_count": max(
				task["one_q_gate_count"] for task in tasks),
			"two_q_gate_count": max(
				task["two_q_gate_count"] for task in tasks),
			"measurement_count": max_qubits,
			"shots": shots,
		},
	}


def scenario_walltime_ns(scenario, tasks):
	classical_ns = int(sum(
		task["classical_seconds"] for task in tasks) * 1_000_000_000)
	quantum_ns = sum(task["rough_quantum_ns"] for task in tasks)
	if scenario["classical_mode"] == "parallel":
		total = max(classical_ns, quantum_ns)
	else:
		total = classical_ns + quantum_ns
	return max(1_000_000_000, int(total * 2) + 1_000_000_000)


def build_workload(category, worker_index, task_count):
	if category == "standalone":
		task_count = 1
	tasks = []
	for index in range(task_count):
		shape = task_shape(category, worker_index, index)
		tasks.append(circuit_task(category, worker_index, index, shape))
	return tasks


def task_shape(category, worker_index, index):
	short = {
		"label": "short",
		"num_qubits": 4,
		"depth": 10,
		"one_q_gate_count": 10,
		"two_q_gate_count": 5,
		"shots": 128,
		"classical_seconds": 0.005,
	}
	long = {
		"label": "long",
		"num_qubits": 8,
		"depth": 80,
		"one_q_gate_count": 160,
		"two_q_gate_count": 80,
		"shots": 512,
		"classical_seconds": 0.015,
	}
	if category == "short_only":
		return short
	if category == "long_only":
		return long
	if category == "mixed_short_long":
		return short if (worker_index + index) % 2 == 0 else long
	if category == "mixed_job_types":
		shape = short if index % 2 == 0 else long
		shape = dict(shape)
		shape["priority"] = (worker_index + 1) * 10 - index
		return shape
	if category == "standalone":
		return short
	raise ValueError(f"unknown workload category: {category}")


def circuit_task(category, worker_index, index, shape):
	shape = dict(shape)
	num_qubits = shape["num_qubits"]
	depth = shape["depth"]
	shots = shape["shots"]
	payload = {
		"qasm": make_qasm(num_qubits, depth),
		"num_qubits": num_qubits,
		"num_shots": shots,
		"shots": shots,
		"depth": depth,
		"one_q_gate_count": shape["one_q_gate_count"],
		"two_q_gate_count": shape["two_q_gate_count"],
		"measurement_count": num_qubits,
		"compiler": "staq",
		"priority": shape.get("priority", 0),
		"workload_category": category,
		"worker_index": worker_index,
		"workload_index": index,
	}
	return {
		"label": shape["label"],
		"worker_index": worker_index,
		"workload_index": index,
		"num_qubits": num_qubits,
		"depth": depth,
		"shots": shots,
		"one_q_gate_count": shape["one_q_gate_count"],
		"two_q_gate_count": shape["two_q_gate_count"],
		"priority": payload["priority"],
		"classical_seconds": shape["classical_seconds"],
		"rough_quantum_ns": rough_quantum_ns(shape),
		"payload": payload,
	}


def make_qasm(num_qubits, depth):
	lines = [
		"OPENQASM 2.0;",
		'include "qelib1.inc";',
		f"qreg q[{num_qubits}];",
		f"creg c[{num_qubits}];",
		"h q[0];",
	]
	for qubit in range(1, num_qubits):
		lines.append(f"cx q[{qubit - 1}],q[{qubit}];")
	for layer in range(max(1, depth // max(1, num_qubits))):
		for qubit in range(num_qubits):
			lines.append(f"rz({0.01 * (layer + 1):.6f}) q[{qubit}];")
			lines.append(f"rx({0.02 * (qubit + 1):.6f}) q[{qubit}];")
		for qubit in range(0, num_qubits - 1, 2):
			lines.append(f"cx q[{qubit}],q[{qubit + 1}];")
	lines.append("measure q -> c;")
	return "\n".join(lines) + "\n"


def rough_quantum_ns(shape):
	return (
		shape["one_q_gate_count"] * 20 +
		shape["two_q_gate_count"] * 100 +
		shape["num_qubits"] * 1000 +
		1_500)


def run_workers(qpm, scenario, reservations, args, deadline):
	results = [None] * len(reservations)
	threads = []
	for slot, reservation in enumerate(reservations):
		thread = threading.Thread(
			target=_worker_entry,
			args=(qpm, scenario, reservation, args, deadline, results, slot),
			name=f"fake-iqm-worker-{slot}")
		thread.start()
		threads.append(thread)
	for thread in threads:
		remaining = max(0.1, deadline - time.monotonic())
		thread.join(remaining)
	for slot, thread in enumerate(threads):
		if thread.is_alive():
			raise DEFwError(
				f"worker did not finish before harness walltime: "
				f"worker={slot}")
	for result in results:
		if result is None:
			raise DEFwError("worker result missing")
		if result["status"] != "ok":
			raise DEFwError(f"worker failed: {result}")
	return results


def _worker_entry(qpm, scenario, reservation, args, deadline, results, slot):
	try:
		results[slot] = run_worker(
			qpm, scenario, reservation, args, deadline)
	except Exception as error:
		results[slot] = {
			"worker_index": reservation["worker_index"],
			"status": "failed",
			"error": {
				"type": type(error).__name__,
				"message": str(error),
				"traceback": traceback.format_exc(),
			},
		}


def run_worker(qpm, scenario, reservation, args, deadline):
	worker_index = reservation["worker_index"]
	reservation_id = reservation["decision"]["reservation_id"]
	worker = {
		"worker_index": worker_index,
		"reservation_id": reservation_id,
		"status": "running",
		"submissions": [],
		"completions": [],
		"classical_events": [],
	}
	for task in reservation["tasks"]:
		run_classical_phase(worker, scenario, task, "pre", args.classical_scale)
		parallel = None
		if scenario["classical_mode"] == "parallel":
			parallel = start_parallel_classical(
				worker, task, args.classical_scale)
		submission = qpm.async_run(
			task["payload"],
			reservation_id=reservation_id,
			timeout=remaining_seconds(deadline))
		worker["submissions"].append(submission_record(
			submission, reservation, task))
		if (scenario["expect_all_tasks"] and
				not submission_will_complete(submission)):
			raise DEFwError(f"qtask was not accepted: {submission}")
		completion = wait_for_completion(
			qpm, submission["cid"], reservation_id,
			min(args.completion_timeout, remaining_seconds(deadline)))
		worker["completions"].append(completion)
		if parallel is not None:
			parallel.join(max(0.1, remaining_seconds(deadline)))
			if parallel.is_alive():
				raise DEFwError("parallel classical emulation timed out")
		run_classical_phase(worker, scenario, task, "post", args.classical_scale)
	worker["status"] = "ok"
	return worker


def run_workers_queued(qpm, scenario, reservations, args, deadline, wave):
	workers = [
		{
			"wave_index": reservation["wave_index"],
			"worker_index": reservation["worker_index"],
			"reservation_id": reservation["decision"]["reservation_id"],
			"status": "running",
			"submissions": [],
			"completions": [],
			"classical_events": [],
		}
		for reservation in reservations
	]
	pending = []
	parallel_threads = []
	qpm.pause_execution_target(
		reason=f"fake-iqm-stress:{scenario['name']}:wave-{wave['wave_index']}")
	try:
		for worker, reservation in zip(workers, reservations):
			reservation_id = reservation["decision"]["reservation_id"]
			for task in reservation["tasks"]:
				run_classical_phase(
					worker, scenario, task, "pre",
					args.classical_scale)
				parallel = None
				if scenario["classical_mode"] == "parallel":
					parallel = start_parallel_classical(
						worker, task, args.classical_scale)
					parallel_threads.append(parallel)
				submission = qpm.async_run(
					task["payload"],
					reservation_id=reservation_id,
					timeout=remaining_seconds(deadline))
				record = submission_record(submission, reservation, task)
				worker["submissions"].append(record)
				pending.append({
					"worker": worker,
					"reservation": reservation,
					"task": task,
					"submission": record,
				})
				if (scenario["expect_all_tasks"] and
						not submission_will_complete(submission)):
					raise DEFwError(
						f"qtask was not accepted: {submission}")
		wave["queued_state"] = qpm.get_scheduler_queue_state(
			include_restricted=True)
	finally:
		qpm.resume_execution_target()
	complete_pending_submissions(qpm, scenario, pending, args, deadline)
	for thread in parallel_threads:
		thread.join(max(0.1, remaining_seconds(deadline)))
		if thread.is_alive():
			raise DEFwError("parallel classical emulation timed out")
	for worker, reservation in zip(workers, reservations):
		for task in reservation["tasks"]:
			run_classical_phase(
				worker, scenario, task, "post", args.classical_scale)
		worker["status"] = "ok"
	return workers


def complete_pending_submissions(qpm, scenario, pending, args, deadline):
	incomplete = list(pending)
	while incomplete:
		if time.monotonic() > deadline:
			raise DEFwError("queued workers exceeded harness walltime")
		next_incomplete = []
		for item in incomplete:
			submission = item["submission"]
			completion = qpm.read_cq(
				cid=submission["cid"],
				reservation_id=submission["reservation_id"])
			if completion.get("completion_ready"):
				item["worker"]["completions"].append(completion)
			else:
				next_incomplete.append(item)
		if len(next_incomplete) == len(incomplete):
			time.sleep(0.05)
		incomplete = next_incomplete


def submission_record(submission, reservation, task):
	record = dict(submission)
	record.update({
		"wave_index": reservation["wave_index"],
		"worker_index": reservation["worker_index"],
		"reservation_id": reservation["decision"].get("reservation_id"),
		"workload_index": task["workload_index"],
		"workload_label": task["label"],
		"priority": task["priority"],
		"rough_quantum_ns": task["rough_quantum_ns"],
		"num_qubits": task["num_qubits"],
		"depth": task["depth"],
		"shots": task["shots"],
	})
	return record


def submission_will_complete(submission):
	return (
		submission.get("cid") is not None and
		submission.get("outcome") in ("ACCEPTED", "DELAYED"))


def run_classical_phase(worker, scenario, task, phase, scale):
	mode = scenario["classical_mode"]
	if (phase == "pre" and mode != "sequential_pre" or
			phase == "post" and mode != "sequential_post"):
		return
	sleep_seconds = task["classical_seconds"] * scale
	start = time.time_ns()
	time.sleep(sleep_seconds)
	worker["classical_events"].append({
		"phase": phase,
		"mode": mode,
		"workload_index": task["workload_index"],
		"requested_seconds": sleep_seconds,
		"start_ns": start,
		"end_ns": time.time_ns(),
	})


def start_parallel_classical(worker, task, scale):
	def sleep_and_record():
		sleep_seconds = task["classical_seconds"] * scale
		start = time.time_ns()
		time.sleep(sleep_seconds)
		worker["classical_events"].append({
			"phase": "parallel",
			"mode": "parallel",
			"workload_index": task["workload_index"],
			"requested_seconds": sleep_seconds,
			"start_ns": start,
			"end_ns": time.time_ns(),
		})
	thread = threading.Thread(target=sleep_and_record)
	thread.start()
	return thread


def wait_for_completion(qpm, cid, reservation_id, timeout):
	deadline = time.monotonic() + max(0.1, timeout)
	while time.monotonic() < deadline:
		completion = qpm.read_cq(cid=cid, reservation_id=reservation_id)
		if completion.get("completion_ready"):
			return completion
		time.sleep(0.05)
	raise DEFwError(
		f"completion timed out: reservation_id={reservation_id} cid={cid}")


def remaining_seconds(deadline):
	return max(0, int(deadline - time.monotonic()))


def release_workers(qpm, reservations):
	results = []
	for reservation in reservations:
		reservation_id = reservation["decision"].get("reservation_id")
		if reservation_id is None:
			continue
		results.append(qpm.release(reservation_id=reservation_id, reason=0))
	return results


def leak_check(capacity, queue):
	held = capacity.get("held_capacity", {})
	leaks = {
		"pending_qtask_count": capacity.get("pending_qtask_count", 0),
		"held_qtask_count": held.get("qtask_count", 0),
		"scheduler_queue_depth": capacity.get("scheduler_queue_depth", 0),
		"active_reservation_count": capacity.get("active_reservation_count"),
		"provider_inflight_qtask_ids": queue.get(
			"provider_inflight_qtask_ids", []),
		"selected_qtask_ids": queue.get("selected_qtask_ids", []),
		"runtime_tasks": queue.get("runtime_tasks", []),
		"pending_capacity": queue.get("pending_capacity", []),
	}
	leaks["ok"] = (
		leaks["pending_qtask_count"] == 0 and
		leaks["held_qtask_count"] == 0 and
		leaks["scheduler_queue_depth"] in (0, None) and
		leaks["active_reservation_count"] in (0, None) and
		not leaks["provider_inflight_qtask_ids"] and
		not leaks["selected_qtask_ids"] and
		not leaks["runtime_tasks"] and
		not leaks["pending_capacity"])
	return leaks


def submitted_tasks_from_workers(workers):
	tasks = []
	for worker in workers:
		for submission in worker.get("submissions", []):
			tasks.append(dict(submission))
	return tasks


def actual_dispatch_order(workers):
	completions = []
	for worker in workers:
		for completion in worker.get("completions", []):
			completions.append(dict(completion))
	completions.sort(key=lambda item: (
		item.get("qpm_cq_enqueue_time_ns", 0),
		item.get("qtask_id", 0)))
	return [completion.get("qtask_id") for completion in completions]


def expected_dispatch_order(scenario, submitted_tasks):
	tasks = [dict(task) for task in submitted_tasks]
	policy = scenario["scheduler_policy"]
	if policy in ("fifo", "ordered"):
		ordered = tasks
		assertion = "submission-order"
	elif policy == "priority":
		ordered = sorted(
			tasks, key=lambda item: (-item["priority"],
				item["worker_index"], item["workload_index"]))
		assertion = "priority-descending"
	elif policy == "ordered_sjf":
		ordered = sorted(
			tasks, key=lambda item: (item["rough_quantum_ns"],
				item["worker_index"], item["workload_index"]))
		assertion = "shortest-estimate-first"
	elif policy == "ordered_ljf":
		ordered = sorted(
			tasks, key=lambda item: (-item["rough_quantum_ns"],
				item["worker_index"], item["workload_index"]))
		assertion = "longest-estimate-first"
	elif policy == "round_robin":
		ordered = round_robin_order(tasks)
		assertion = "round-robin-by-reservation"
	else:
		ordered = tasks
		assertion = "policy-specific-order"
	return {
		"assertion": assertion,
		"qtask_ids": [task.get("qtask_id") for task in ordered],
		"tasks": ordered,
	}


def round_robin_order(tasks):
	groups = []
	group_map = {}
	for task in tasks:
		key = task.get("reservation_id") or task.get("job_id")
		if key not in group_map:
			group = []
			group_map[key] = group
			groups.append(group)
		group_map[key].append(task)
	ordered = []
	while groups:
		next_groups = []
		for group in groups:
			if group:
				ordered.append(group.pop(0))
			if group:
				next_groups.append(group)
		groups = next_groups
	return ordered


def validate_dispatch_order(scenario, wave):
	if not scenario.get("assert_scheduler_order"):
		return
	expected = wave["expected_dispatch_order"].get("qtask_ids", [])
	actual = wave["actual_dispatch_order"]
	if expected != actual:
		raise DEFwError(
			"scheduler dispatch order mismatch: "
			f"expected={expected} actual={actual} "
			f"assertion={wave['expected_dispatch_order'].get('assertion')}")


class ResultSink:
	def __init__(self, path):
		self.path = path or os.environ.get("QFW_EXAMPLE_RESULT_FILE", "")
		self.lock = threading.Lock()

	def write(self, record):
		payload = json.dumps(jsonable(record), sort_keys=True)
		print("QFW_FAKE_IQM_STRESS_RESULT " + payload)
		if not self.path:
			return
		directory = os.path.dirname(self.path)
		if directory:
			os.makedirs(directory, exist_ok=True)
		with self.lock:
			with open(self.path, "a", encoding="utf-8") as handle:
				handle.write(payload + "\n")


def main():
	args = parse_args()
	result_sink = ResultSink(args.result_file)
	start = time.time_ns()
	status = "ok"
	scenario_records = []
	resolved_backend = args.backend
	try:
		resolved_backend, qpm = resolve_qpm(args.backend, args.system_up_timeout)
		wait_for_qpm(qpm, args.system_up_timeout)
		for scenario in build_scenarios(args):
			record = run_scenario(qpm, scenario, args, result_sink)
			scenario_records.append(record)
			if record["status"] != "ok":
				status = "failed"
				break
	except Exception as error:
		status = "failed"
		scenario_records.append({
			"schema": "qfw-fake-iqm-stress-v1",
			"kind": "fake-iqm-stress-driver-error",
			"status": "failed",
			"error": {
				"type": type(error).__name__,
				"message": str(error),
				"traceback": traceback.format_exc(),
			},
		})
	duration_sec = (time.time_ns() - start) / 1_000_000_000.0
	emit_result(
		"fake-iqm-stress",
		status=status,
		parameters={
			"scenario_set": args.scenario_set,
			"backend": resolved_backend,
			"workers": args.workers,
			"tasks_per_worker": args.tasks_per_worker,
			"waves": args.waves,
			"harness_walltime": args.harness_walltime,
		},
		metrics={
			"scenario_count": len(scenario_records),
			"passed": sum(
				1 for record in scenario_records
				if record.get("status") == "ok"),
			"failed": sum(
				1 for record in scenario_records
				if record.get("status") != "ok"),
			"duration_sec": duration_sec,
		},
		details={"scenarios": scenario_records},
	)
	if status != "ok":
		raise SystemExit(1)


if __name__ == "__main__":
	main()
