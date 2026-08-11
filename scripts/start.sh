#!/usr/bin/env bash
# Reachy とおしゃべり: Sim → Ollama → 会話サーバ の順に立ち上げる。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODEL="${OLLAMA_MODEL:-gemma3:4b}"

echo "▶ Reachy 2 Sim を起動します…"
docker compose -f sim/compose.yaml up -d

echo "▶ Ollama を確認します…"
if ! curl -sf --max-time 3 http://127.0.0.1:11434/api/version >/dev/null; then
  echo "  Ollama が起動していません。'brew services start ollama' を実行してください。" >&2
  exit 1
fi
if ! ollama list | awk 'NR>1 {print $1}' | grep -qx "$MODEL"; then
  echo "  モデル $MODEL を取得します（初回のみ、数分かかります）…"
  ollama pull "$MODEL"
fi

echo "▶ Sim の SDK サーバ (50051) を待っています…"
for _ in $(seq 1 90); do
  if nc -z localhost 50051 2>/dev/null; then break; fi
  sleep 2
done
if ! nc -z localhost 50051 2>/dev/null; then
  echo "  50051 が開きませんでした。'docker logs reachy2-sim' を確認してください。" >&2
  echo "  （会話だけなら このまま続行できます）"
fi

echo
echo "  おしゃべり : http://localhost:8000  （3D の Reachy もこの画面の中です）"
echo
exec .venv/bin/uvicorn app:app --app-dir server --host 127.0.0.1 --port 8000
