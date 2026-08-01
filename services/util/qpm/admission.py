QHW_ADM_THREAD_SAFE = "QHW_ADM_THREAD_SAFE"
QHW_ADM_THREAD_USER = "QHW_ADM_THREAD_USER"


class QPMAdmissionUnavailable(RuntimeError):
	pass


class UnavailableAdmissionContext:
	def __init__(self, threading_mode, error):
		self.threading_mode = threading_mode
		self.error = error
		self.available = False

	@property
	def threading(self):
		return self.threading_mode

	def close(self):
		return None

	def __getattr__(self, name):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {self.error}")


def create_admission_context(threading_mode=QHW_ADM_THREAD_SAFE):
	try:
		import qhw_admission
	except Exception as error:
		return UnavailableAdmissionContext(threading_mode, error)

	threading_value = _native_threading_value(qhw_admission, threading_mode)
	return qhw_admission.AdmissionContext(threading=threading_value)


def admission_context_available(context):
	return getattr(context, "available", True)


def _native_threading_value(qhw_admission, threading_mode):
	if threading_mode == QHW_ADM_THREAD_SAFE:
		return qhw_admission.THREAD_SAFE
	if threading_mode == QHW_ADM_THREAD_USER:
		return qhw_admission.THREAD_USER
	raise ValueError(f"unsupported qhw-admission threading mode: {threading_mode}")
