"""子供が Reachy (Sim) とおしゃべりしたり、おかたづけゲームをしたりするためのサーバ。

  ブラウザ ──WebSocket──> このサーバ ──> Ollama (ローカル LLM)
                              └────────> Reachy 2 Sim (gRPC/50051)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from brain import Brain
from game import Game
from robot import MOTIONS, ReachyRobot
from speech import LocalSTT, synthesize, tts_available

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("reachy-kids")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
REACHY_HOST = os.environ.get("REACHY_HOST", "localhost")
# "local" = この Mac の Whisper で認識 / "browser" = ブラウザの音声認識を使う
STT_MODE = os.environ.get("STT_MODE", "local")
# 音声アップロードの上限 (バイト)。だいたい 1 分ぶんの Opus 音声に相当。
_MAX_AUDIO_BYTES = 8 * 1024 * 1024
# ブラウザの 3D 表示へ関節角を送る頻度。ブラウザ側で補間するのでこれで十分なめらか。
POSE_HZ = 30

robot = ReachyRobot(host=REACHY_HOST)
brain = Brain()
game = Game(robot, brain)
stt = LocalSTT()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sim はまだ起動途中かもしれないので、失敗しても続行する（あとで再試行）。
    await asyncio.to_thread(robot.connect)
    ok, msg = await brain.available()
    if ok:
        # 最初に話しかけた子を待たせないよう、裏でモデルを載せておく。
        asyncio.create_task(brain.warmup())
    else:
        logger.warning("会話ブレインが使えません: %s", msg)
    yield
    await brain.aclose()
    await asyncio.to_thread(robot.disconnect)


app = FastAPI(title="Reachy とおしゃべり", lifespan=lifespan)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict:
    llm_ok, llm_msg = await brain.available()
    return {
        "robot": robot.connected,
        "llm": llm_ok,
        "llm_message": llm_msg,
        "model": brain.model,
        "tts": tts_available(),
        "stt_mode": STT_MODE if (STT_MODE != "local" or LocalSTT.installed()) else "browser",
        "motions": MOTIONS,
    }


@app.post("/api/reconnect")
async def reconnect() -> dict:
    """Sim をあとから起動した場合に、UI のボタンから接続し直す。"""
    connected = await asyncio.to_thread(robot.connect)
    return {"robot": connected}


@app.post("/api/reset")
async def reset() -> dict:
    brain.reset()
    return {"ok": True}


@app.post("/api/motion/{motion}")
async def play_motion(motion: str) -> dict:
    """UI のジェスチャーボタン用。動きの確認にも使う。"""
    if motion not in MOTIONS:
        return {"ok": False, "error": "unknown motion"}
    if game.active and motion == "wave":
        # ゲーム中は腕をテーブルの上で構えている。手をふると構えがくずれる。
        return {"ok": False, "error": "playing the game"}
    return {"ok": await asyncio.to_thread(robot.play, motion)}


@app.get("/api/tts")
async def tts(text: str) -> Response:
    audio = await synthesize(text)
    if not audio:
        return Response(status_code=204)
    return Response(content=audio, media_type="audio/wav")


@app.post("/api/stt")
async def transcribe(audio: UploadFile) -> dict:
    data = await audio.read(_MAX_AUDIO_BYTES + 1)
    if len(data) > _MAX_AUDIO_BYTES:
        return {"text": "", "error": "音声が長すぎます"}
    try:
        return {"text": await stt.transcribe(data)}
    except Exception as exc:
        logger.warning("音声認識に失敗: %s", exc)
        return {"text": "", "error": str(exc)}


@app.websocket("/ws/pose")
async def pose_stream(ws: WebSocket) -> None:
    """Sim の関節角をブラウザの 3D 表示へ流し続ける。

    macOS の Docker は GPU を使えず、コンテナ内の RViz はソフトウェア描画で
    2〜3fps しか出ない。そこで描画はブラウザ（Mac の GPU）に任せ、
    ここでは Sim の実際の関節角だけを送る。
    """
    await ws.accept()
    try:
        while True:
            pose = await asyncio.to_thread(robot.pose)
            await ws.send_json(pose or {})
            await asyncio.sleep(1 / POSE_HZ)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as exc:
        logger.debug("関節角の配信を終了: %s", exc)


@app.websocket("/ws/game")
async def game_channel(ws: WebSocket) -> None:
    """おかたづけゲーム。子供のおねがいを受けて、腕の動きと物の置き場所を送り返す。

    受け取るもの: start / leave / say(text) / next / retry / restart
    送るもの:     world（テーブルのようす）/ say / hint / thinking / clear / done
    """
    await ws.accept()

    async def out(msg: dict) -> None:
        await ws.send_json(msg)

    try:
        await out({"type": "world", "world": game.world(), "cause": None})
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "say":
                text = str(msg.get("text", "")).strip()[:500]
                if text:
                    await game.handle(text, out)   # 終わったら handle が done を送る
                continue
            commands = {"start": game.start, "leave": game.leave, "next": game.next,
                        "retry": game.retry, "restart": game.restart}
            if kind in commands:
                await commands[kind](out)
                # 腕の構えやおだいの読み上げが終わったら、画面の入力を受け付け直す
                await out({"type": "done"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("ゲームの WebSocket でエラー: %s", exc)


@app.websocket("/ws")
async def chat(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            text = str(msg.get("text", "")).strip()[:500]
            if not text:
                continue

            # 考えている間も体は動かす。子供を無言で待たせないための間つなぎ。
            await asyncio.to_thread(robot.play, "think")
            await ws.send_json({"type": "thinking"})

            reply, motion = await brain.respond(text)
            await asyncio.to_thread(robot.play, motion)
            await ws.send_json({"type": "reply", "text": reply, "motion": motion})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WebSocket でエラー: %s", exc)


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
