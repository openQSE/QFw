# Runs one Pulser program on a Pasqal device through the shim QPM, with
# Pulser's own QPUBackend and the QFw connection, through each shim library
# asked for, and prints the results side by side.
#
# The register sits on a calibrated layout of the device, fetched through the
# QPM. A device can require a layout to be filled to a minimum fraction unless
# it has exactly the minimum number of traps, so the program uses such a
# layout when the device has one and otherwise raises the atom count to the
# minimum (42 on FRESNEL's 120 traps). Atoms sit at least 15 um apart where
# they fit, close enough to interact otherwise.
#
# A global drive of pi rad/us runs for a variable duration: 500 ns is a pi/2
# pulse and 1000 ns a pi pulse. Atoms that do not interact end up excited
# half the time and nearly always respectively; interacting atoms block each
# other's excitation, so the excited fraction stays lower. The summary shows
# that fraction per job. A QPM that serves no Pulser device gets a program
# built on pulser.AnalogDevice.
#
# Takes the shim libraries, the shot count, the number of atoms and the
# device whose specs to build on, which an emulator lets you choose:
#  ex: test_pulser.py qrmi 100 4 FRESNEL_CAN1

import math
import sys
import time

import pulser
from pulser import Pulse, Register, Sequence

from qfw_example_context import qfw_reservation_options
from qfw_example_report import emit_result
from qfw_pulser import QFwConnection

libs = (sys.argv[1] if len(sys.argv) > 1 else "qrmi").split(",")
shots = int(sys.argv[2]) if len(sys.argv) > 2 else 100
atoms = int(sys.argv[3]) if len(sys.argv) > 3 else 4
build_on = sys.argv[4] if len(sys.argv) > 4 else "FRESNEL_CAN1"
durations = [500, 1000]
spacing = 15.0


def pick_device(connection):
	try:
		devices = connection.fetch_available_devices()
	except NotImplementedError:
		return pulser.AnalogDevice
	return devices.get(build_on) or next(iter(devices.values()))


def build_register(device):
	layouts = device.calibrated_register_layouts.values()
	if not layouts:
		return Register.rectangle(
			1, atoms, spacing=spacing, prefix="q").with_automatic_layout(device)
	layout = min(layouts, key=lambda layout: (
		layout.number_of_traps != device.min_layout_traps,
		layout.number_of_traps))
	size = atoms
	if layout.number_of_traps != device.min_layout_traps:
		size = max(atoms, math.ceil(
			device.min_layout_filling * layout.number_of_traps))
	if size != atoms:
		print(f"{device.name} needs at least {size} atoms on its "
		      f"{layout.number_of_traps}-trap layout; using {size}")
	# Traps nearest the centre, each at least `spacing` um from the others
	# while that many fit, else simply the nearest ones.
	nearest = sorted(range(layout.number_of_traps),
		key=lambda trap: math.hypot(*layout.coords[trap]))
	chosen = []
	for trap in nearest:
		if all(math.dist(layout.coords[trap], layout.coords[other]) >= spacing
		       for other in chosen):
			chosen.append(trap)
	chosen = chosen[:size] if len(chosen) >= size else nearest[:size]
	return layout.define_register(
		*chosen, qubit_ids=[f"q{index}" for index in range(size)])


def excited_fraction(counts):
	shots_total = sum(counts.values())
	width = len(next(iter(counts)))
	ones = sum(bits.count("1") * count for bits, count in counts.items())
	return ones / (shots_total * width)


def build_sequence(device):
	sequence = Sequence(build_register(device), device)
	sequence.declare_channel("rydberg", "rydberg_global")
	duration = sequence.declare_variable("duration", dtype=int)
	sequence.add(Pulse.ConstantPulse(duration, math.pi, 0, 0), "rydberg")
	return sequence


reservation = qfw_reservation_options()
runs = []
for lib in libs:
	connection = QFwConnection(lib=lib, **reservation)
	device = pick_device(connection)
	start = time.monotonic()
	results = pulser.QPUBackend(build_sequence(device), connection=connection).run(
		[{"runs": shots, "variables": {"duration": value}} for value in durations],
		wait=True)
	runs.append({
		"lib": lib,
		"device": device.name,
		"atoms": len(results.results[0].atom_order),
		"seconds": round(time.monotonic() - start, 1),
		"batch_status": results.get_batch_status().name,
		"counts": [result.bitstring_counts for result in results.results],
	})

print(f"\nPulser program on {runs[0]['atoms']} atoms, {shots} shots per "
      f"job, built on {runs[0]['device']}")
for run in runs:
	print(f"  {run['lib'].upper():5} {run['batch_status']:6} {run['seconds']:7}s")
	for duration, counts in zip(durations, run["counts"]):
		top = sorted(counts.items(), key=lambda item: -item[1])[:2]
		print(f"        {duration:5} ns  excited {excited_fraction(counts):.2f}  "
		      + "  ".join(f"{bits}:{count}" for bits, count in top))
emit_result(
	"pulser",
	parameters={"libs": libs, "shots": shots, "atoms": atoms,
		"durations_ns": durations, "build_on": build_on},
	metrics={"runs": runs},
)
