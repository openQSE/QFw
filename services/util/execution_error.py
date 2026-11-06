# A compile job may run with no DEFw, so the modules it imports raise this.
try:
	from defw_exception import DEFwExecutionError
except ImportError:
	DEFwExecutionError = RuntimeError
