#!/usr/bin/env python3

import json
import os
import sys
import time

from defw_app_util import defw_get_resource_mgr, defw_reserve_service_by_name


def validate_result(result, expected_np):
	if result.get('rc') != 0:
		raise RuntimeError(
			f"mpirun failed rc={result.get('rc')} "
			f"stderr={result.get('stderr', '')}"
		)

	records = result.get('records', [])
	if len(records) != expected_np:
		raise RuntimeError(f"expected {expected_np} MPI records, got {records}")

	pids = {entry.get('pid') for entry in records if 'pid' in entry}
	if len(pids) != expected_np:
		raise RuntimeError(f"expected {expected_np} distinct pids, got {records}")

	ranks = sorted(
		entry.get('rank') for entry in records
		if isinstance(entry.get('rank'), int)
	)
	expected_ranks = list(range(expected_np))
	if ranks != expected_ranks:
		raise RuntimeError(
			f"expected ranks {expected_ranks}, got {ranks} from {records}"
		)

	return records


def reserve_mpi_smoke(resmgr, timeout):
	deadline = time.time() + timeout
	last_error = None
	while time.time() < deadline:
		try:
			return defw_reserve_service_by_name(resmgr, 'MPISmoke')[0]
		except Exception as err:
			last_error = err
			time.sleep(1)
	raise RuntimeError(f"MPISmoke service was not available: {last_error}")


def main():
	timeout = int(os.environ.get('QFW_MPI_SMOKE_TIMEOUT', '40'))
	np = int(os.environ.get('QFW_MPI_SMOKE_NP', '2'))
	api = None

	try:
		resmgr = defw_get_resource_mgr(timeout=timeout)
		api = reserve_mpi_smoke(resmgr, timeout)
		result = api.run_pid_hello(np)
		records = validate_result(result, np)
		print(json.dumps({
			'status': 'ok',
			'service_host': result.get('service_host'),
			'service_pid': result.get('service_pid'),
			'mpi_records': records,
		}, sort_keys=True))
		return 0
	finally:
		if api is not None:
			try:
				api.shutdown()
			except Exception:
				pass


if __name__ == '__main__':
	sys.exit(main())
