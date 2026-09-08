"""Read a stable identity for a Linux process incarnation."""

import argparse
import json
from pathlib import Path


BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")


def local_process_identity(pid):
    """Return the kernel identity of *pid*, or ``None`` when it is dead."""
    pid = int(pid)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        boot_id = BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, ProcessLookupError):
        return None
    except PermissionError:
        return None

    closing_parenthesis = stat.rfind(")")
    fields = stat[closing_parenthesis + 2:].split()
    if closing_parenthesis < 0 or len(fields) <= 19:
        return None

    try:
        start_time_ticks = int(fields[19])
    except ValueError:
        return None

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
    identity = local_process_identity(args.pid)
    if identity is None:
        return 1
    print(json.dumps(identity, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
