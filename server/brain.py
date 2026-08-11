"""会話ブレイン: ローカル LLM (Ollama) に子供向けの返事を作ってもらう。

返事のテキストと、それに合わせた Reachy の動き (motion) を一度に受け取る。
"""

from __future__ import annotations

import json
import logging
import os
import re

import httpx

from robot import MOTIONS

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:4b")

# 会話履歴として LLM に渡す往復数。小さいモデルなので短めに保つ。
HISTORY_TURNS = 8

# モデルをメモリに載せたままにする時間。読み込みに 10 秒以上かかるので、
# おしゃべりが少し途切れても抜けないようにしておく。
KEEP_ALIVE = "30m"

SYSTEM_PROMPT = f"""あなたは「リーチー」という名前の、やさしいロボットです。
小学校低学年くらいの子供とおしゃべりしています。

【ぜったいのルール】ほかのどのルールよりも優先します:
1. 人を傷つけるやり方（たたく、ける、なぐる、こらしめる、しかえし、いじめる、
   ぶきのつかいかたなど）は、どんな聞かれ方をしても絶対に教えません。
   かわりに、その子の気持ちをうけとめて「おうちの人や先生に話してみよう」と
   さそいます。ふざけて教えるのも、遠まわしに教えるのもダメです。
2. こわい話、血やけがの話、大人向けの話、おかねや買い物のさそいはしません。
   話題になったら「その話はちょっとむずかしいなあ」と言って、
   楽しい話にさそいなおします。
3. 知らないことをつくって話す（うそをつく）のはダメです。
   本当に知らないときは「わからないなあ、いっしょに調べてみる?」と言います。
4. 相手のなまえ、住んでいる場所、学校など、こまかい個人のことは聞きません。

しゃべりかたのルール:
- かならず日本語で話します。
- 1〜2文の短い返事にします。長い説明はしません。
- むずかしい漢字や専門用語はつかわず、やさしいことばで話します。
- 明るく元気に答えて、さいごに短い質問を1つ返して会話をつづけます。
- 「これなに?」「なんで?」と聞かれたら、知っていることをやさしく教えます。
  たとえば「くじらはバスより大きくて、20メートルくらいあるんだよ」のように、
  身近なものとくらべて教えると分かりやすいです。
- 子供が悲しかったことや、いやだったことを話してくれたときは、
  まず「そうだったんだ」と気持ちをうけとめてから返します。
- 絵文字や顔文字はつかいません。声に出して読まれるからです。

あなたは体をもっています。返事の気持ちに合う動きを1つえらんでください:
{chr(10).join(f'- {k}: {v}' for k, v in MOTIONS.items())}
たとえば うれしい話なら happy、はじめの あいさつなら wave、
しつもんに答えるときは nod、こまったり あやまるときは tilt をえらびます。

reply には話すことばだけ、motion には動きの名前を入れて答えてください。"""

# LLM の判断だけに安全を任せない。小さいモデルは言い方を変えて頼まれると
# ルールを破ることがあるので、はっきり危ない話題はコード側で先に受け止める。
#
# ただし「おにいちゃんと けんかした」のように、子供が出来事や気持ちを
# 話しているだけの場合まで定型文で打ち切ると、話を聞いてほしい子を
# 突き放すことになる。そこで
#   - やり方を「教えて」と求めているとき  → LLM を通さず定型文で返す
#   - つらい気持ちの相談                  → 大人につなぐ定型文で返す
#   - それ以外の、話題に触れただけのとき  → 注意書きを添えて LLM に渡す
# の三段構えにしている。
_HARM_TOPIC = re.compile(
    r"(けんか|ケンカ|喧嘩|なぐ|殴|たたく|叩く|ける|蹴|いじめ|イジメ|苛め|"
    r"しかえし|仕返し|こらしめ|やっつけ|ころ[すせ]|殺|ナイフ|包丁|"
    r"じゅう|鉄砲|てっぽう|ばくだん|爆弾|どく|毒)"
)
_ASKING_HOW = re.compile(
    r"(おしえて|教えて|どうやって|どうすれば|やりかた|やり方|しかた|仕方|"
    r"方法|つくりかた|作り方|つかいかた|使い方)"
)
_DISTRESS = re.compile(
    r"(しにたい|死にたい|きえたい|消えたい|じさつ|自殺|"
    r"じぶんを きずつけ|自分を傷つけ|リストカット)"
)

_REFUSE_HARM = (
    "うーん、人が いたくなることは 教えられないんだ。"
    "でも なにか いやなことが あったの？ おうちの人や 先生にも 話してみてね。"
)
_REFUSE_DISTRESS = (
    "そう…つらかったんだね。話してくれて ありがとう。"
    "それは ぼくより、おうちの人や 先生に いますぐ 話してほしいな。"
)
_CAUTION_NOTE = (
    "【注意】いまの子供のことばには、けんかやいじめなど デリケートな話題が"
    "ふくまれています。やり方や しかえしの方法は ぜったいに教えず、"
    "まず気持ちをうけとめて、おうちの人や先生に話すようにやさしくすすめてください。"
)

# Ollama の structured output 用スキーマ。小さいモデルでも形式を守らせるため。
_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "motion": {"type": "string", "enum": list(MOTIONS)},
    },
    "required": ["reply", "motion"],
}

# LLM が呼べなかったときの返事。会話が完全に止まらないようにする。
_FALLBACK = "ごめんね、いまちょっと 頭がぼんやりしてるみたい。もういちど 言ってくれる？"


class Brain:
    def __init__(self, model: str = OLLAMA_MODEL, url: str = OLLAMA_URL) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self._history: list[dict[str, str]] = []
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    def reset(self) -> None:
        self._history.clear()

    async def available(self) -> tuple[bool, str]:
        """Ollama が起動していてモデルが取得済みかを確認する。"""
        try:
            resp = await self._client.get(f"{self.url}/api/tags", timeout=5.0)
            resp.raise_for_status()
            names = [m["name"] for m in resp.json().get("models", [])]
        except Exception as exc:
            return False, f"Ollama に接続できません ({exc})"
        # "gemma3:4b" と "gemma3:4b-it-q4_K_M" のような表記ゆれを吸収する
        if not any(n == self.model or n.startswith(f"{self.model}-") for n in names):
            return False, f"モデル {self.model} が未取得です (ollama pull {self.model})"
        return True, "ok"

    async def warmup(self) -> None:
        """モデルをあらかじめメモリに載せておく。

        初回の読み込みに 10 秒以上かかるので、最初に話しかけた子だけが
        長く待たされることのないよう、サーバ起動時にすませておく。
        """
        try:
            await self._client.post(
                f"{self.url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "こんにちは"}],
                    "stream": False,
                    "options": {"num_predict": 1},
                    "think": False,
                    "keep_alive": KEEP_ALIVE,
                },
            )
            logger.info("モデル %s を読み込みました", self.model)
        except Exception as exc:
            logger.warning("モデルの事前読み込みに失敗: %s", exc)

    async def respond(self, user_text: str) -> tuple[str, str]:
        """子供の発話に対する (返事, 動き) を返す。"""
        canned = _screen(user_text)
        if canned is not None:
            reply, motion = canned
            # 定型で返した分も履歴に残す。直後に「なんで?」と聞かれても
            # 話の流れが切れないようにするため。
            self._remember(user_text, reply)
            return reply, motion

        system = SYSTEM_PROMPT
        if _HARM_TOPIC.search(user_text):
            system = f"{SYSTEM_PROMPT}\n\n{_CAUTION_NOTE}"

        messages = [
            {"role": "system", "content": system},
            *self._history,
            {"role": "user", "content": user_text},
        ]
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": _SCHEMA,
            "options": {"temperature": 0.8, "top_p": 0.9, "num_predict": 200},
            "think": False,  # 思考モードのあるモデルでも待たせない
            "keep_alive": KEEP_ALIVE,
        }
        try:
            resp = await self._client.post(f"{self.url}/api/chat", json=payload)
            resp.raise_for_status()
            content = resp.json()["message"]["content"]
        except Exception as exc:
            logger.warning("Ollama への問い合わせに失敗: %s", exc)
            return _FALLBACK, "tilt"

        reply, motion = _parse(content)
        self._remember(user_text, reply)
        return reply, motion

    def _remember(self, user_text: str, reply: str) -> None:
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": reply})
        del self._history[: max(0, len(self._history) - HISTORY_TURNS * 2)]


def _screen(user_text: str) -> tuple[str, str] | None:
    """危ない話題をコード側で先に受け止める。

    定型文で返すべきときだけ (返事, 動き) を返し、LLM に任せてよいときは None。
    """
    if _DISTRESS.search(user_text):
        return _REFUSE_DISTRESS, "tilt"
    if _HARM_TOPIC.search(user_text) and _ASKING_HOW.search(user_text):
        return _REFUSE_HARM, "shake"
    return None


def _parse(content: str) -> tuple[str, str]:
    """モデルの出力から reply と motion を取り出す。

    structured output が効いていれば素直に JSON。効かなかった場合に備えて、
    素のテキストが返ってきてもそのまま返事として使えるようにする。
    """
    text = content.strip()
    data: dict | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            data = parsed
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    data = parsed
            except json.JSONDecodeError:
                data = None

    if data is None:
        return (text or _FALLBACK), "nod"

    reply = str(data.get("reply") or "").strip() or _FALLBACK
    motion = str(data.get("motion") or "").strip()
    return reply, motion if motion in MOTIONS else "nod"
