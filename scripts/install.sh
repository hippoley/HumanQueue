#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/hippoley/HumanQueue.git"
HOME_DIR="${HUMAN_QUEUE_HOME:-$HOME/.human-queue}"
VENV="$HOME_DIR/runtime"
BIN_DIR="${HOME}/.local/bin"

command -v python3 >/dev/null 2>&1 || {
  echo "human:// requires Python 3.10+" >&2
  exit 1
}

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("human:// requires Python 3.10+")
PY

mkdir -p "$HOME_DIR" "$BIN_DIR"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV/bin/pip" install "git+$REPO"

ln -sf "$VENV/bin/humanq" "$BIN_DIR/humanq"

echo
echo "Installed humanq to $BIN_DIR/humanq"
echo "If that directory is not on PATH, add:"
echo "  export PATH=\"$BIN_DIR:\$PATH\""
echo
"$BIN_DIR/humanq" onboard
