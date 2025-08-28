#!/bin/bash

# This script makes the assumption that you're running with a venv used by the QFw

if command -v python3_defw_orig >/dev/null 2>&1; then
  python_path=$(command -v python3_defw_orig)
else
  python_path=$(command -v python3) || { echo "python3 not found" >&2; exit 1; }
fi
resolved=$(readlink -f "$python_path" || echo "$python_path")

if [[ "$resolved" == /usr/bin/python3* ]]; then
  libdir="/usr/lib64"
else
  bin_dir="$(dirname "$resolved")"
  root_dir="$(dirname "$bin_dir")"
  libdir="${root_dir}/lib"
fi

if [[ ! -d "$libdir" ]]; then
  echo "Warning: computed libdir does not exist: $libdir" >&2
fi

export LD_LIBRARY_PATH="${libdir}${LD_LIBRARY_PATH+:$LD_LIBRARY_PATH}"


