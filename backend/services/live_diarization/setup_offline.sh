#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MAIN_VENV="$PROJECT_ROOT/.venv"
DIART_VENV="$PROJECT_ROOT/.venv-diart"
WHEEL_DIR="$PROJECT_ROOT/diart_offline"

test -x "$MAIN_VENV/bin/python"
test -f "$WHEEL_DIR/diart-0.9.2-py3-none-any.whl"
test -f "$WHEEL_DIR/Rx-3.2.0-py3-none-any.whl"

"$MAIN_VENV/bin/python" -m venv "$DIART_VENV"
DIART_SITE="$("$DIART_VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')"
MAIN_SITE="$("$MAIN_VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')"
printf '%s\n' "$MAIN_SITE" > "$DIART_SITE/meeting_scribe_main_venv.pth"
"$DIART_VENV/bin/python" -m pip install --no-index --no-deps --find-links="$WHEEL_DIR" diart==0.9.2 rx==3.2.0
"$DIART_VENV/bin/python" -c 'from importlib.metadata import version; print({"diart": version("diart"), "rx": version("Rx")})'
