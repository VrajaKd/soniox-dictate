#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LANG_FLAG="${1:-et}"
exec python -m soniox_dictate --lang "$LANG_FLAG"
