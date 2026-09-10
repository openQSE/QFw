import ast
from pathlib import Path


QFW_ROOT = Path(__file__).resolve().parents[2]


def _service_info(path):
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	for statement in tree.body:
		if not isinstance(statement, ast.Assign):
			continue
		if not any(
			isinstance(target, ast.Name) and target.id == "svc_info"
			for target in statement.targets
		):
			continue
		if not isinstance(statement.value, ast.Dict):
			break
		return {
			ast.literal_eval(key): ast.literal_eval(value)
			for key, value in zip(statement.value.keys, statement.value.values)
			if isinstance(key, ast.Constant) and key.value == "instance_mode"
		}
	raise AssertionError(f"{path} does not define svc_info as a dictionary")


def test_all_qpm_service_packages_are_singletons():
	packages = sorted((QFW_ROOT / "services").glob("svc_*_qpm/__init__.py"))

	assert packages
	for package in packages:
		assert _service_info(package).get("instance_mode") == "singleton", (
			f"{package.parent.name} must use one service-wide QPM instance"
		)
