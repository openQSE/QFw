# Resource-intent matching for the Backline QPM.
#
# A client states a resource_intent (roles, hardware kinds, transport, QEC
# code, latency). The site's inventory says what exists. match() picks free
# inventory entries that satisfy the intent and returns the placement the
# client builds its Backline nodes from. Nothing else in QFw reads either.
#
# Matching order, first failure wins: QEC code, controller kind, per
# coprocessor decoder + transport + latency, then capacity.

import importlib.util
import os
import string
from pathlib import Path

import yaml

PLACEMENT_VERSION = 1


class Reject(Exception):
	def __init__(self, reason, message):
		super().__init__(message)
		self.reason = reason


def rejected(reason, message):
	return {"status": "rejected", "reason": reason, "message": message}


def _default_catalyst_lib():
	spec = importlib.util.find_spec("catalyst")
	if spec is None or not spec.submodule_search_locations:
		return ""
	return str(Path(list(spec.submodule_search_locations)[0]) / "lib")


def catalyst_lib():
	return os.environ.get("CATALYST_LIB") or _default_catalyst_lib()


def load_inventory(path):
	# ${VAR} is expanded from the environment. CATALYST_LIB defaults to the
	# installed catalyst package's lib/ directory, where the wheel ships the
	# precompiled decoders.
	env = dict(os.environ)
	env["CATALYST_LIB"] = catalyst_lib()
	try:
		text = string.Template(Path(path).read_text()).substitute(env)
	except KeyError as e:
		raise ValueError(f"inventory {path} uses unset variable {e}") from None
	inventory = yaml.safe_load(text) or {}
	for decoder in inventory.get("decoder", []):
		if not Path(decoder["lib"]).is_file():
			raise FileNotFoundError(
				f"decoder {decoder['id']!r}: library not found: "
				f"{decoder['lib']}")
	for ctrl in inventory.get("controller", []):
		missing = [k for k in ("device", "device_lib") if not ctrl.get(k)]
		if missing:
			raise ValueError(
				f"controller {ctrl.get('id')!r} in {path} lacks {missing}")
	return inventory


def capabilities(inventory):
	return {
		"placement_version": PLACEMENT_VERSION,
		"qec_codes": sorted({d["code"] for d in inventory.get("decoder", [])}),
		"controller_kinds": sorted(
			{c["hardware"] for c in inventory.get("controller", [])}),
		"coprocessor_kinds": sorted(
			{c["hardware"] for c in inventory.get("coprocessor", [])}),
		"transports": sorted(
			{t for c in inventory.get("controller", [])
			 for t in c["transports"]}),
	}


def _field(obj, key):
	value = obj.get(key)
	if value is None:
		return {}
	if not isinstance(value, dict):
		raise Reject("invalid-request",
			     f"{key} must be an object, got {type(value).__name__}")
	return value


def match(inventory, intent, qubit_count, busy):
	"""Return (placement, entry_ids) or raise Reject."""
	if not isinstance(intent, dict) or intent.get("version") != PLACEMENT_VERSION:
		raise Reject("invalid-request",
			     "resource_intent with version 1 is required")
	if type(qubit_count) is not int or qubit_count < 1:
		raise Reject("invalid-request",
			     "task_class.qubit_count (logical qubits) must be an int >= 1")
	wanted = intent.get("coprocessors") or [{"role": "qec_decoder"}]
	if not isinstance(wanted, list) or not all(
			isinstance(w, dict) for w in wanted):
		raise Reject("invalid-request",
			     "resource_intent.coprocessors must be a list of objects")
	code = _field(intent, "qec").get("code")
	transport = _field(intent, "transport").get("preferred")
	kind = _field(intent, "controller").get("kind")
	limit = _field(intent, "latency").get("max_round_trip_us")

	codes = sorted({d["code"] for d in inventory.get("decoder", [])})
	if code not in codes:
		raise Reject("unsupported-qec-code",
			     f"qec code {code!r} is not offered; supported: {codes}")

	ctrls = [c for c in inventory.get("controller", [])
		 if kind in (None, c["hardware"])]
	if not ctrls:
		raise Reject("no-matching-controller",
			     f"no controller of kind {kind!r}")

	if transport is not None:
		return _match_on(inventory, transport, ctrls, wanted, code, limit,
				 qubit_count, busy)
	# Outcome-only intent: the first transport the controller and every
	# coprocessor share; report the first transport's reason if none fits.
	first = None
	for t in sorted({t for c in ctrls for t in c["transports"]}):
		try:
			return _match_on(inventory, t, ctrls, wanted, code, limit,
					 qubit_count, busy)
		except Reject as r:
			first = first or r
	raise first or Reject("unsupported-transport",
			      "no controller lists a transport")


def _match_on(inventory, transport, ctrls, wanted, code, limit, qubit_count,
	      busy):
	ctrls = [c for c in ctrls if transport in c["transports"]]
	if not ctrls:
		raise Reject("unsupported-transport",
			     f"no matching controller supports transport {transport!r}")

	options = []
	for i, want in enumerate(wanted):
		if want.get("decoder") == "client":
			raise Reject("client-decoder-unsupported",
				     "client-defined decoders are not supported yet")
		ck = want.get("kind")
		decoders = [d for d in inventory.get("decoder", [])
			    if d["code"] == code and ck in (None, d["hardware"])
			    and want.get("decoder") in (None, d["code"], d["id"])]
		opts = [(c, d) for c in inventory.get("coprocessor", [])
			for d in decoders if c["hardware"] == d["hardware"]]
		if not opts:
			raise Reject("no-matching-coprocessor",
				     f"coprocessor {i}: no {ck or 'any'} decoder for {code!r}")
		opts = [(c, d) for c, d in opts if transport in c["transports"]]
		if not opts:
			raise Reject("unsupported-transport",
				     f"coprocessor {i}: no option supports transport "
				     f"{transport!r}")
		if limit is not None:
			measured = [(c, d) for c, d in opts
				    if d.get("round_trip_us_p50") is not None]
			if not measured:
				raise Reject("latency-unverified",
					     f"coprocessor {i}: no measured round trip to "
					     f"check against {limit} us")
			opts = [(c, d) for c, d in measured
				if d["round_trip_us_p50"] <= limit]
			if not opts:
				raise Reject("latency-unattainable",
					     f"coprocessor {i}: no decoder meets {limit} us")
		options.append(opts)

	# Greedy, first free choice per role; can miss a fit when
	# interchangeable coprocessors differ in reach. Bipartite matching if
	# inventories grow.
	taken = set(busy)
	ctrl = next((c for c in ctrls if c["id"] not in taken), None)
	if ctrl is None:
		raise Reject("capacity-exhausted",
			     "every matching controller is reserved")
	taken.add(ctrl["id"])
	chosen = []
	for i, opts in enumerate(options):
		pick = next(((c, d) for c, d in opts if c["id"] not in taken), None)
		if pick is None:
			raise Reject("capacity-exhausted",
				     f"coprocessor {i}: every matching coprocessor is "
				     "reserved")
		taken.add(pick[0]["id"])
		chosen.append(pick)

	placement = {
		"version": PLACEMENT_VERSION,
		"controller": {"name": ctrl["id"], "hardware": ctrl["hardware"],
			       "device": {"name": ctrl["device"],
					  "wires": qubit_count}},
		"coprocessors": [
			{"name": c["id"], "hardware": c["hardware"],
			 "function": {"symbol": d["symbol"], "lib_path": d["lib"]}}
			for c, d in chosen
		],
		"transport": transport,
		"qec_code": code,
	}
	return placement, [ctrl["id"]] + [c["id"] for c, _ in chosen]
