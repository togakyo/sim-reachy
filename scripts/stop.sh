#!/usr/bin/env bash
# Sim を停止する（Ollama は brew services が管理しているのでそのまま）。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker compose -f "$ROOT/sim/compose.yaml" down
echo "Reachy 2 Sim を停止しました。"
