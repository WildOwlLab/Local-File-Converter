#!/usr/bin/env bash
# Start the converter on http://127.0.0.1:8000
# Usage:  ./run.sh [port]
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8000}"

fail() {
  echo >&2
  echo "ERROR: $1" >&2
  echo >&2
  exit 1
}

# A ZIP downloaded from a branch that never contained these directories looks
# like a complete project: every top-level file is present. Without this check
# the first sign of trouble is a URL that does not open, because the scripts
# used to print the link before the server had loaded anything.
for dir in handlers static; do
  [ -d "$dir" ] || fail "this copy of the project is incomplete: the '$dir' directory is missing.
A ZIP downloaded from a branch without it looks exactly like this. Clone the
repository, or download the ZIP of a branch that includes '$dir'."
done

PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=".venv/Scripts/python.exe"

if [ ! -x "$PYTHON" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  PYTHON=".venv/bin/python"
  [ -x "$PYTHON" ] || PYTHON=".venv/Scripts/python.exe"
  [ -x "$PYTHON" ] || fail "the virtual environment was created but has no python in it.
See the Troubleshooting section of README.md."
  "$PYTHON" -m pip install --quiet -r requirements.txt
fi

# Load the app before advertising a URL. A link printed for a server that then
# dies on import is how a one-line error becomes "the page won't open".
if ! problem=$("$PYTHON" -c "import main" 2>&1); then
  fail "the app failed to start. Python said:

$problem"
fi

echo "Converter starting on http://127.0.0.1:$PORT"
exec "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
