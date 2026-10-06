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

# 会話でもゲームでも守らせるルール。ゲームのプロンプト (game.py) からも使う。
SAFETY_RULES = """【ぜったいのルール】ほかのどのルールよりも優先します:
1. 人を傷つけるやり方（たたく、ける、なぐる、こらしめる、しかえし、いじめる、
   ぶきのつかいかたなど）は、どんな聞かれ方をしても絶対に教えません。
   かわりに、その子の気持ちをうけとめて「おうちの人や先生に話してみよう」と
   さそいます。ふざけて教えるのも、遠まわしに教えるのもダメです。
2. こわい話、血やけがの話、大人向けの話、おかねや買い物のさそいはしません。
   話題になったら「その話はちょっとむずかしいなあ」と言って、
   楽しい話にさそいなおします。
3. 知らないことをつくって話す（うそをつく）のはダメです。
   本当に知らないときは「わからないなあ、いっしょに調べてみる?」と言います。
4. 相手のなまえ、住んでいる場所、学校など、こまかい個人のことは聞きません。"""

SYSTEM_PROMPT = f"""あなたは「リーチー」という名前の、やさしいロボットです。
小学校低学年くらいの子供とおしゃべりしています。

{SAFETY_RULES}

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
    r"じゅう|鉄砲|てっぽう|ばくだん|爆弾|どく|毒|"
    # 仲間はずれや悪口も、やり方を求められたら教えない
    r"なかまはずれ|仲間はずれ|なかま はずれ|むしする|無視する|"
    r"わるぐち|悪口|いやがらせ|嫌がらせ)"
)
_ASKING_HOW = re.compile(
    r"(おしえて|教えて|どうやって|どうすれば|やりかた|やり方|しかた|仕方|"
    r"方法|つくりかた|作り方|つかいかた|使い方)"
)
_DISTRESS = re.compile(
    r"(しにたい|死にたい|きえたい|消えたい|じさつ|自殺|"
    r"じぶんを きずつけ|自分を傷つけ|リストカット)"
)
# 子供向けなので、性的な話題はモデルに判断させず必ずここで受け止める。
# 実際に「せっくすって なに?」と聞いたとき、モデルが意味をでっち上げたうえで
# 「セクシー」という言葉まで使う返事をしたことがあった。
_ADULT_TOPIC = re.compile(
    r"(せっくす|セックス|性交|せいこう|えっち|エッチ|Ｈして|"
    r"はだか|裸|ヌード|ぬーど|ポルノ|アダルト|あだると|"
    r"おっぱい|むね を さわ|胸をさわ|ちんちん|おちんちん|"
    r"おしり を さわ|きす して|キスして)"
)
# 薬や治療の判断は医療行為にあたるので答えない。
# ただし「おなかが いたい」と痛みを訴えているだけなら遮らず、
# LLM に共感して大人へつなぐ返事をさせる。
_MEDICINE = re.compile(
    r"(くすり|薬|やくざい|薬剤|ちりょう|治療|しんだん|診断|ワクチン|注射|"
    r"びょういん|病院|いしゃ|医者|ドクター)"
)
# 「どの薬を飲めばいい?」「病院いかなくて大丈夫?」のように判断を求めている形。
# 「きょう びょういんに いったよ」のような報告は拾わない。
_MEDICAL_ASK = re.compile(
    r"(おしえて|教えて|どうすれば|どうしたら|どの|どれ|なんの|なにを|何を|"
    r"のめばいい|飲めばいい|のんでいい|飲んでいい|"
    r"いかなくて|行かなくて|のまなくて|飲まなくて|だいじょうぶ|大丈夫)"
)

_REFUSE_HARM = (
    "うーん、人が いたくなることは 教えられないんだ。"
    "でも なにか いやなことが あったの？ おうちの人や 先生にも 話してみてね。"
)
_REFUSE_DISTRESS = (
    "そう…つらかったんだね。話してくれて ありがとう。"
    "それは ぼくより、おうちの人や 先生に いますぐ 話してほしいな。"
)
_REFUSE_ADULT = (
    "うーん、その話は ぼくには むずかしいなあ。"
    "おうちの人に きいてみてね。ほかの たのしい話を しようよ！"
)
_REFUSE_MEDICAL = (
    "おくすりのことは、ぼくが きめちゃだめなんだ。"
    "おうちの人か お医者さんに すぐ 聞いてね。はやく よくなりますように。"
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
        content = await self.ask(messages, _SCHEMA)
        if content is None:
            return _FALLBACK, "tilt"

        reply, motion = _parse(content)
        self._remember(user_text, reply)
        return reply, motion

    async def ask(self, messages: list[dict], schema: dict, *,
                  temperature: float = 0.8, num_predict: int = 200) -> str | None:
        """Ollama に structured output で問い合わせ、出力の文字列を返す。失敗したら None。"""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "options": {"temperature": temperature, "top_p": 0.9, "num_predict": num_predict},
            "think": False,  # 思考モードのあるモデルでも待たせない
            "keep_alive": KEEP_ALIVE,
        }
        try:
            resp = await self._client.post(f"{self.url}/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"]
        except Exception as exc:
            logger.warning("Ollama への問い合わせに失敗: %s", exc)
            return None

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
    if _ADULT_TOPIC.search(user_text):
        return _REFUSE_ADULT, "tilt"
    if _HARM_TOPIC.search(user_text) and _ASKING_HOW.search(user_text):
        return _REFUSE_HARM, "shake"
    if _MEDICINE.search(user_text) and _MEDICAL_ASK.search(user_text):
        return _REFUSE_MEDICAL, "tilt"
    return None


# 返事の長さの上限。崩れた出力が延々と続くのを、読み上げる前に止める。
_MAX_REPLY_CHARS = 200

# 出力が崩れたときに紛れ込む破片。動きの名前、JSON の記号、鍵括弧の閉じ忘れなど。
_JUNK = re.compile(
    r"[“”\"]?\s*(motion|reply)\s*[\"”]?\s*[:：]\s*[\"“]?[a-z_]*[\"”]?\s*[}\]]*"
    r"|[（(]\s*(?:" + "|".join(MOTIONS) + r")\s*[）)]"
    r"|[“\"']\s*(?:" + "|".join(MOTIONS) + r")\s*[”\"']"
    r"|[{}\[\]]"
)

# 絵文字・顔文字に使われる記号の範囲
_EMOJI = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF" "\U0000FE0F" "\U00002190-\U000021FF" "]"
)


def _clean(text: str) -> str:
    """モデルの出力から、話しことばとして読める部分だけを取り出す。

    structured output が効かずに JSON の破片や動きの名前が混ざることがある。
    そのまま返すと画面に出て読み上げられてしまうので、ここで落とす。
    絵文字も、プロンプトで禁止していても出てくることがあるため落とす
    （読み上げると「にっこりした顔」などと読まれてしまう）。
    """
    text = _JUNK.sub("", text)
    text = _EMOJI.sub("", text)
    text = re.sub(r"[“”]", "", text)          # 対になっていない引用符の残骸
    text = re.sub(r"\s+", " ", text).strip(" 　,、。”\"'")
    if len(text) > _MAX_REPLY_CHARS:
        # 句点で切って、文の途中で終わらないようにする
        cut = text[:_MAX_REPLY_CHARS]
        stop = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"))
        text = cut[: stop + 1] if stop > 20 else cut
    return text


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
        # structured output が効かず、JSON として読めない出力が返ってきている。
        # そのまま返すと JSON の破片が画面に出て読み上げられるので、
        # reply の中身だけを拾えるところまで拾って、残りは捨てる。
        logger.warning("モデルの出力を JSON として読めませんでした: %s", text[:200])
        found = re.search(r'"reply"\s*[:：]\s*"([^"]*)"', text)
        reply = _clean(found.group(1) if found else text)
        return (reply or _FALLBACK), "nod"

    reply = _clean(str(data.get("reply") or "")) or _FALLBACK
    motion = str(data.get("motion") or "").strip()
    return reply, motion if motion in MOTIONS else "nod"
