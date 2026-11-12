import importlib
import json
import sys


def service_record(entry):
	module = importlib.import_module(entry["module"])
	accelerator = getattr(module, "accelerator", None)
	return {
		"service": entry["name"],
		"provider": module.svc_info.get("properties", {}).get("provider"),
		"device_id": entry.get("device-id"),
		"accelerator": accelerator(entry) if accelerator else None,
	}


def main(argv):
	entries = json.loads(argv[0])
	print(json.dumps([service_record(entry) for entry in entries], indent=2))
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
