"""Read a stable identity for a Linux process incarnation."""

import argparse
import json
import sys
from pathlib import Path


BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")

# Exit code for `main` when the probe could not tell whether the process is
# alive. Distinct from 0, which means the question was answered, and from
# argparse's 2 for a usage error.
PROBE_FAILED_EXIT = 3


class ProcessProbeError(RuntimeError):
    """Raised when the probe cannot tell whether a process is alive."""


def local_process_identity(pid):
    """Return the kernel identity of *pid*, or ``None`` when it is gone.

    Raises:
        ProcessProbeError: the probe could not answer. A caller must not read
            that as a dead process, because the process may still be running.
    """
    pid = int(pid)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError as exc:
        raise ProcessProbeError(
            f"cannot read /proc/{pid}/stat: {exc}") from exc

    try:
        boot_id = BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProcessProbeError(f"cannot read {BOOT_ID_PATH}: {exc}") from exc

    closing_parenthesis = stat.rfind(")")
    fields = stat[closing_parenthesis + 2:].split()
    if closing_parenthesis < 0 or len(fields) <= 19:
        raise ProcessProbeError(f"unreadable /proc/{pid}/stat contents")

    try:
        start_time_ticks = int(fields[19])
    except ValueError as exc:
        raise ProcessProbeError(
            f"unreadable start time in /proc/{pid}/stat") from exc

    return {
        "pid": pid,
        "boot_id": boot_id,
        "start_time_ticks": start_time_ticks,
    }


def identities_match(expected, observed):
    """Return whether two records describe the same process incarnation."""
    if not expected or not observed:
        return False
    try:
        return (
            int(expected["pid"]) == int(observed["pid"])
            and expected["boot_id"] == observed["boot_id"]
            and int(expected["start_time_ticks"])
            == int(observed["start_time_ticks"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    args = parser.parse_args(argv)
    # A caller running this on another node has to tell "the process is gone"
    # apart from "the probe did not run". Both answers are JSON on stdout with
    # exit 0. A failed probe exits non-zero with the reason on stderr.
    try:
        identity = local_process_identity(args.pid)
    except ProcessProbeError as exc:
        print(f"process-state probe failed: {exc}", file=sys.stderr)
        return PROBE_FAILED_EXIT
    if identity is None:
        print(json.dumps({"alive": False}))
        return 0
    print(json.dumps(identity, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
