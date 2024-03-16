import sys, logging, traceback
from defw_util import expand_host_list
from defw_cmd import defw_exec_remote_cmd

def cleanup_dvm(targets):
	for target in targets:
		defw_exec_remote_cmd("pkill -9 srun", target, deamonize=True)
		defw_exec_remote_cmd("pkill -9 prte", target, deamonize=True)
		defw_exec_remote_cmd("pkill -9 prted", target, deamonize=True)

def start_dvm(node_list):
	# Start the DVM on the second node in the Simulation Environment
	# because currently MPI can not co-exist on the DVM's head node.
	# Our launcher will live on node 0
	cmd = f'prte --host {",".join(node_list)}'
	defw_exec_remote_cmd(cmd, node_list[1], deamonize=True)

def print_stacktrace():
	exception_list = traceback.format_stack()
	exception_list = exception_list[:-2]
	exception_list.extend(traceback.format_tb(sys.exc_info()[2]))
	exception_list.extend(traceback.format_exception_only(sys.exc_info()[0],
							sys.exc_info()[1]))
	stacktrace = exception_str = "Traceback (most recent call last):\n"
	stacktrace = "\n".join(exception_list)
	print(stacktrace)

if __name__ == '__main__':
	g0_node_list, g1_node_list = extract_group_node_lists(sys.argv[1])

	try:
		# Start PRTE DVM
		start_dvm(g1_node_list)
		logging.debug("DVM STARTED")
	except Exception as e:
		print(e)
		print_stacktrace()
		cleanup_dvm(g0_node_list+g1_node_list)
		raise e

	logging.debug("FINISHED QFW PROCESS STARTUP")
