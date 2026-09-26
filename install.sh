#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=
FOUND=
for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    FOUND=1
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 11)' >/dev/null 2>&1; then
      PYTHON=$candidate
      break
    fi
  fi
done
if [ -z "$PYTHON" ]; then
  if [ -n "$FOUND" ]; then
    echo "Python 3.10 or newer is required; only an older Python was found. No download was attempted." >&2
    exit 11
  fi
  echo "Python 3.10 or newer was not found. No download was attempted." >&2
  exit 10
fi
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUTF8=1
exec "$PYTHON" -B "$SCRIPT_DIR/install.py" "$@"
