#!/usr/bin/env bash
# リーチーと おしゃべり: Sim → Ollama → 会話サーバ の順に立ち上げる。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODEL="${OLLAMA_MODEL:-gemma3:4b}"
PORT="${PORT:-8000}"

# 先に会話サーバのポートを確かめる。ここが埋まっていると、Sim を起動し終えた
# あとで bind に失敗して落ちるので、何もしないうちに知らせる。
if nc -z localhost "$PORT" 2>/dev/null; then
  echo "  ポート $PORT はすでに使われています。" >&2
  echo "  前回の会話サーバが動いたままかもしれません。確認して止めてください:" >&2
  echo "    lsof -nP -iTCP:$PORT -sTCP:LISTEN" >&2
  echo "    pkill -f 'uvicorn app:app'" >&2
  exit 1
fi

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

# ポートが開いたかどうかでは判定できない。Docker はコンテナを起動した時点で
# ポートを公開するので、中の SDK サーバがまだ立ち上がっていなくても
# 接続に成功してしまう。コンテナのログで実際の起動完了を待つ。
echo -n "▶ Sim の SDK サーバの起動を待っています"
ready=""
for _ in $(seq 1 120); do
  if docker logs reachy2-sim 2>&1 | grep -q "Server started on port 50051"; then
    ready=yes
    break
  fi
  echo -n "."
  sleep 2
done
echo

if [ -n "$ready" ]; then
  sleep 3   # 各部位の初期化が終わるまで少しだけ待つ
  echo "  Sim の準備ができました。"
else
  echo "  時間内に起動しませんでした。'docker logs reachy2-sim' を確認してください。" >&2
  echo "  （会話だけなら このまま続行できます。あとで画面の「ロボットに つなぐ」を押してください）"
fi

echo
echo "  おしゃべり : http://localhost:$PORT  （3D の Reachy もこの画面の中です）"
echo
exec .venv/bin/uvicorn app:app --app-dir server --host 127.0.0.1 --port "$PORT"
