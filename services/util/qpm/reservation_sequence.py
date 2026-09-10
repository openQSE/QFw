import fcntl
import os
from pathlib import Path

from .admission import QPMAdmissionValidationError


UINT64_MAX = (1 << 64) - 1
SEQUENCE_FILE_NAME = "reservation-sequence"


class PersistentReservationSequence:
	def __init__(self, run_dir):
		self.state_dir = Path(run_dir).expanduser().resolve() / "state"
		self.path = self.state_dir / SEQUENCE_FILE_NAME
		self.lock_path = self.state_dir / f"{SEQUENCE_FILE_NAME}.lock"
		self.state_dir.mkdir(parents=True, exist_ok=True)
		self._validate_existing()

	def allocate(self, active_ids=None):
		active_ids = {int(value) for value in (active_ids or ())}
		with self.lock_path.open("a+", encoding="utf-8") as lock_stream:
			fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
			last_used = self._read()
			candidate = last_used
			while True:
				candidate = 1 if candidate == UINT64_MAX else candidate + 1
				if candidate not in active_ids:
					break
				if candidate == last_used:
					raise QPMAdmissionValidationError(
						"reservation identifier space is exhausted")
			self._write(candidate)
			return candidate

	def _validate_existing(self):
		if self.path.exists():
			self._read()

	def _read(self):
		if not self.path.exists():
			return 0
		try:
			value = self.path.read_text(encoding="utf-8").strip()
		except OSError as exc:
			raise QPMAdmissionValidationError(
				f"cannot read reservation sequence {self.path}: {exc}") from exc
		if not value or not value.isdecimal():
			raise QPMAdmissionValidationError(
				f"invalid reservation sequence in {self.path}")
		number = int(value, 10)
		if number < 0 or number > UINT64_MAX:
			raise QPMAdmissionValidationError(
				f"reservation sequence is outside uint64_t in {self.path}")
		return number

	def _write(self, value):
		temporary = self.state_dir / f".{SEQUENCE_FILE_NAME}.{os.getpid()}"
		try:
			fd = os.open(
				temporary,
				os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
				0o600)
			with os.fdopen(fd, "w", encoding="utf-8") as stream:
				stream.write(f"{value}\n")
				stream.flush()
				os.fsync(stream.fileno())
			os.replace(temporary, self.path)
			directory_fd = os.open(self.state_dir, os.O_RDONLY)
			try:
				os.fsync(directory_fd)
			finally:
				os.close(directory_fd)
		finally:
			try:
				temporary.unlink()
			except FileNotFoundError:
				pass
