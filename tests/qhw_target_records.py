"""Representative qhw records shared by optional target-helper tests."""


def representative_qhw_records():
	device = {
		"schema": "qhw-device-v1",
		"device": {"id": "test"},
		"qubits": ("QB1", "QB7"),
		"metadata": {"source": "test"},
		"raw": {"provider": "unchanged"},
	}
	coupling = {
		"schema": "qhw-coupling-v1",
		"coupling": {
			"nodes": ("QB1", "QB7"),
			"edges": (("QB1", "QB7"),),
			"directed": True,
		},
		"operations": ({
			"name": "x",
			"native_name": "prx",
			"arity": 1,
			"parameters": (),
			"supported_loci": (("QB1",), ("QB7",)),
			"properties_by_locus": {"QB1": {"duration": 2e-8}},
		}, {
			"name": "cz",
			"native_name": "cz",
			"arity": 2,
			"parameters": (),
			"supported_loci": (("QB1", "QB7"),),
			"properties_by_locus": {"QB1,QB7": {"duration": 4e-8}},
		}, {
			"name": "rz",
			"native_name": "rz",
			"arity": 1,
			"parameters": ({"name": "theta", "bounds": (-3.2, 3.2)},),
			"supported_loci": (("QB1",), ("QB7",)),
		}),
		"extensions": {"test": True},
		"raw": {"provider": "unchanged"},
	}
	calibration = {
		"schema": "qhw-calibration-v1",
		"extensions": {"qdmi.fomac.v1": {
			"duration_unit": "ns",
			"qubit_metrics": ({"qubit": "QB1", "t1": 50000},),
			"gate_metrics": ({
				"operation": "cz",
				"locus": ("QB1", "QB7"),
				"fidelity": 0.99,
			},),
		}},
	}
	return device, coupling, calibration
