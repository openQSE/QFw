from defw_remote import BaseRemote
from enum import IntFlag


VERSION = 0.1


class QPMType(IntFlag):
	QPM_TYPE_HARDWARE = 1 << 0
	QPM_TYPE_SIMULATOR = 1 << 1


class QPMCapability(IntFlag):
	QPM_CAP_TENSORNETWORK = 1 << 0
	QPM_CAP_STATEVECTOR = 1 << 1
	QPM_CAP_SUPERCONDUCTING = 1 << 2


class QPMRemoteBase(BaseRemote):
	pass
