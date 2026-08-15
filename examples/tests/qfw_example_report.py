import json
import os
import time


def parse_bool(value):
	if isinstance(value, bool):
		return value
	text = str(value).strip().lower()
	if text in ("1", "true", "yes", "on", "y"):
		return True
	if text in ("0", "false", "no", "off", "n"):
		return False
	raise ValueError(f"expected boolean value, got {value!r}")


def jsonable(value):
	if value is None or isinstance(value, (str, int, float, bool)):
		return value
	if isinstance(value, dict):
		return {str(key): jsonable(item) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [jsonable(item) for item in value]
	if isinstance(value, complex):
		return {"real": value.real, "imag": value.imag}
	to_list = getattr(value, "tolist", None)
	if callable(to_list):
		return jsonable(to_list())
	item = getattr(value, "item", None)
	if callable(item):
		try:
			return jsonable(item())
		except Exception:
			pass
	return str(value)


def emit_result(example, status="ok", parameters=None, metrics=None,
		artifacts=None, details=None):
	record = {
		"schema": "qfw-example-result-v1",
		"kind": "example",
		"example": example,
		"status": status,
		"timestamp_ns": time.time_ns(),
		"parameters": jsonable(parameters or {}),
		"metrics": jsonable(metrics or {}),
		"artifacts": jsonable(artifacts or {}),
		"details": jsonable(details or {}),
	}
	payload = json.dumps(record, sort_keys=True)
	print("QFW_EXAMPLE_RESULT " + payload)
	path = os.environ.get("QFW_EXAMPLE_RESULT_FILE")
	if path:
		directory = os.path.dirname(path)
		if directory:
			os.makedirs(directory, exist_ok=True)
		with open(path, "a", encoding="utf-8") as handle:
			handle.write(payload + "\n")
	return record
