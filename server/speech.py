"""音声まわり: 読み上げ (macOS の say) と 音声認識 (faster-whisper)。

どちらもこの Mac の中だけで完結する。子供の声がネットに出ていかない構成。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

TTS_VOICE = os.environ.get("TTS_VOICE", "Kyoko")
TTS_RATE = os.environ.get("TTS_RATE", "170")  # 子供が聞き取りやすいよう少しゆっくり
STT_MODEL_SIZE = os.environ.get("STT_MODEL", "small")

# 読み上げに渡す文字数の上限。暴走した出力で say を長時間走らせない。
_MAX_TTS_CHARS = 400


# ---------------------------------------------------------------- 読み上げ

def tts_available() -> bool:
    return shutil.which("say") is not None


async def synthesize(text: str) -> bytes:
    """日本語テキストを WAV バイト列にする。"""
    text = text.strip()[:_MAX_TTS_CHARS]
    if not text:
        return b""

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        proc = await asyncio.create_subprocess_exec(
            "say", "-v", TTS_VOICE, "-r", TTS_RATE,
            "-o", path, "--data-format=LEI16@22050", text,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            logger.warning("say に失敗: %s", stderr.decode(errors="replace").strip())
            return b""
        with open(path, "rb") as f:
            return f.read()
    except asyncio.TimeoutError:
        logger.warning("say がタイムアウトしました")
        return b""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------- 音声認識

class LocalSTT:
    """faster-whisper によるローカル音声認識。モデルは初回利用時に読み込む。"""

    def __init__(self, model_size: str = STT_MODEL_SIZE) -> None:
        self.model_size = model_size
        self._model = None
        self._lock = asyncio.Lock()

    @staticmethod
    def installed() -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        from faster_whisper import WhisperModel

        logger.info("Whisper モデル (%s) を読み込みます…", self.model_size)
        # Apple Silicon の CPU 実行。int8 が速度と精度のバランスがよい。
        return WhisperModel(self.model_size, device="cpu", compute_type="int8")

    async def transcribe(self, audio: bytes) -> str:
        """音声バイト列 (webm/ogg/wav など) を日本語テキストにする。"""
        if not audio:
            return ""
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load)
            model = self._model

        fd, path = tempfile.mkstemp(suffix=".webm")
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(audio)
            return await asyncio.to_thread(self._run, model, path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    @staticmethod
    def _run(model, path: str) -> str:
        segments, _ = model.transcribe(
            path,
            language="ja",
            beam_size=1,           # 応答速度優先
            vad_filter=True,       # 無音や環境音を落とす
            condition_on_previous_text=False,
        )
        return "".join(seg.text for seg in segments).strip()
