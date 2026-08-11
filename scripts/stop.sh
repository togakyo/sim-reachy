#!/usr/bin/env bash
# 会話サーバと Sim を止める（Ollama は brew services が管理しているのでそのまま）。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# start.sh が exec で起動した uvicorn。パターンはこのアプリのものだけに当たるよう
# --app-dir server まで含めている。
SERVER_PATTERN="uvicorn app:app --app-dir server"

if pgrep -f "$SERVER_PATTERN" >/dev/null 2>&1; then
  echo "▶ 会話サーバを止めます…"
  pkill -f "$SERVER_PATTERN"
  for _ in $(seq 1 15); do
    pgrep -f "$SERVER_PATTERN" >/dev/null 2>&1 || break
    sleep 1
  done
  if pgrep -f "$SERVER_PATTERN" >/dev/null 2>&1; then
    echo "  すぐに終わらないので強制終了します。"
    pkill -9 -f "$SERVER_PATTERN"
  fi
fi

echo "▶ Reachy 2 Sim を止めます…"
docker compose -f "$ROOT/sim/compose.yaml" down

echo "停止しました。"
