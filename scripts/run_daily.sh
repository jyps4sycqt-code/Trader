#!/bin/bash
# Daily monitor + push. Invoked by launchd weekday afternoons.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

LOG_DIR="$REPO_ROOT/.cache/logs"
mkdir -p "$LOG_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

exec python -m scanner.main daily -v >> "$LOG_DIR/daily-$TS.log" 2>&1
