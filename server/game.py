"""おかたづけゲーム: 子供のことばどおりに、Reachy がテーブルの物を片づける。

  子供のことば → LLM が決まった動作の並びに直す → サーバが物を決める → Sim の腕が動く

LLM には関節を動かさせない。LLM の仕事は、ことばを「いれる・つかむ・ゆびさす…」の
決まった動作に直すところまでで、どの物のことか、腕をどう動かすかはサーバが決める。
LLM の出力が崩れても、腕がおかしな動きをすることはない。

ゲームのおもしろさは「言われたとおりにしか動かないロボット」にある。
子供が言わなかった色や種類は、LLM が気をきかせて補っても捨てる（_ground）。
ボールが 2 つあるのに「ボールを はこに いれて」と言われたら、リーチーは
どちらかを当てずっぽうで選ぶので、まちがえることがある。それを見た子供が言い直す。
何回のおねがいでできたかで、星の数が決まる。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import unicodedata
from dataclasses import dataclass

import table
from brain import _CAUTION_NOTE, _HARM_TOPIC, SAFETY_RULES, _clean, _screen
from robot import MOTIONS

logger = logging.getLogger(__name__)

COLOR_LABEL = {"red": "あかい", "blue": "あおい", "yellow": "きいろい", "green": "みどりの"}
# 色だけで呼ぶときの言い方（「あかいの は 2こ あるね」）
COLOR_ONLY = {"red": "あかいの", "blue": "あおいの", "yellow": "きいろいの", "green": "みどりの"}
KIND_LABEL = {"ball": "ボール", "block": "つみき"}
BIN_LABEL = {"box": "はこ", "basket": "かご"}
DOS = ("put", "pick", "putdown", "drop", "point", "show")

# 子供が「言った」とみなすことば。発話はカタカナをひらがなに直してから探す。
_WORDS = {
    "red": ("あか", "赤", "れっど", "まっか", "真っ赤"),
    "blue": ("あお", "青", "ぶるー", "まっさお", "真っ青"),
    "yellow": ("きいろ", "黄色", "黄", "いえろー"),
    "green": ("みどり", "緑", "ぐりーん"),
    "ball": ("ぼーる", "たま", "玉", "まり", "まるい", "丸い"),
    "block": ("つみき", "積み木", "積木", "ぶろっく", "しかく", "四角", "きゅーぶ"),
    "box": ("はこ", "箱", "ばこ", "ぼっくす"),
    "basket": ("かご", "籠", "ばすけっと"),
}
# 動きのことば。LLM が動きを取りちがえたときの直しと、何を動かすか言っていない
# おねがい（「それ みせて」）を残すかの判断に使う。
_VERBS = {
    "put": ("いれ", "入れ", "しまっ", "しまう", "かたづけ", "片付け", "片づけ", "ぽい", "なおし"),
    "pick": ("とって", "とる", "取", "もって", "もつ", "持", "つかん", "つかむ", "掴",
             "もちあげ", "持ち上げ", "ひろっ", "ひろう", "拾"),
    "putdown": ("おいて", "おく", "置", "もどし", "もどす", "戻", "おろし", "下ろ"),
    "drop": ("はなし", "はなす", "離", "放", "おとし", "おとす", "落"),
    "point": ("ゆびさ", "指さ", "指差", "どれ", "どこ"),
    "show": ("みせ", "見せ"),
}
# 動きのことばが 2 つあるときに優先する順。「とって いれて」は put、
# 「とって みせて」は show にする（どちらも、つかむ動きをふくんでいる）。
_VERB_ORDER = ("put", "show", "putdown", "drop", "point", "pick")
# 入れ物のことばのすぐ後ろ。「はこから」は取り出す元で、行き先ではない。
_FROM = re.compile(r"\s*から")

# おだい。objects は「物 → 置き場所 (table.SLOTS)」。r が Reachy の右手側（画面の左）。
# 後ろのおだいほど、色と種類の両方を言わないと伝わらないように並べている。
MISSIONS = [
    {
        "title": "あかい ボールを はこに いれてもらおう",
        "objects": {"red_ball": "r1", "yellow_block": "l1"},
        "goal": {"in": {"red_ball": "box"}},
        "par": 1,
    },
    {
        "title": "きいろい つみきを みせてもらおう",
        "objects": {"blue_block": "r1", "yellow_block": "l1", "red_ball": "l2"},
        "goal": {"show": "yellow_block"},
        "par": 1,
    },
    {
        "title": "あおい ボールを かごに いれてもらおう",
        "objects": {"red_ball": "r1", "blue_block": "r2", "blue_ball": "l1", "yellow_block": "l2"},
        "goal": {"in": {"blue_ball": "basket"}},
        "par": 1,
    },
    {
        "title": "あおい ものを ぜんぶ はこに いれてもらおう",
        "objects": {"blue_ball": "r1", "yellow_block": "r2", "red_ball": "l1", "blue_block": "l2"},
        "goal": {"in": {"blue_ball": "box", "blue_block": "box"}},
        "par": 1,
    },
    {
        "title": "ボールは かごに、つみきは はこに かたづけよう",
        "objects": {"red_block": "r1", "blue_ball": "r2", "red_ball": "l1", "yellow_block": "l2"},
        "goal": {"in": {"red_ball": "basket", "blue_ball": "basket",
                        "red_block": "box", "yellow_block": "box"}},
        "par": 2,
    },
]
# ぜんぶのおだいのあとの「じゆう あそび」
FREE_PLAY = {"red_ball": "r1", "yellow_block": "r2", "blue_block": "r3",
             "blue_ball": "l1", "green_block": "l2", "red_block": "l3"}

# グリッパーの開き（%）。Sim には物がないので閉じきれてしまうが、画面で指が
# 物にめりこまないよう、つかんでいるあいだは物の大きさのところで止める。
_OPEN, _HOLD, _CLOSED = 100.0, 75.0, 0.0
# 首をまっすぐ前（子供のほう）へ戻す
_FACE_KID = ("head", 0.0, -10.0, 0.0, 0.5)

_ACKS = ("いいよ！", "まかせて！", "わかった！", "やってみるね！")
# 動きのおねがいとして読めなかったのに、LLM が「いいよ！」とだけ返してきたときの言いかえ。
# 引き受けたのに何もしないと、子供は何がいけなかったのか分からない。
_ACK_ONLY = re.compile(r"^(いいよ|まかせて|わかった|はーい|うん|やってみる|オッケー|おっけー)[！!。、ね〜ー]*$")
_ASK_AGAIN = "なにを すれば いいかな？「あかい ボールを はこに いれて」みたいに おしえてね。"
_FALLBACK = "ごめんね、いま ちょっと 頭が ぼんやりしてるみたい。もういちど 言ってくれる？"

GAME_PROMPT = f"""あなたは「リーチー」という名前の、やさしいロボットです。
小学校低学年くらいの子供と「おかたづけゲーム」をしています。
テーブルの上の ボールや つみきを、子供のことばどおりに はこや かごへ かたづけます。

{SAFETY_RULES}

あなたの しごとは、子供のことばを ロボットの うごき（actions）に なおすことです。

うごき（do）は つぎの 6 つだけです:
- put: ものを いれものに いれる（いれて、しまって、かたづけて）
- pick: ものを もつ（とって、もって、つかんで）
- putdown: もっている ものを テーブルに おく（おいて、もどして）
- drop: もっている ものを はなす（はなして、おとして）
- point: ものや いれものを ゆびさす（どれ？、どこ？、ゆびさして）
- show: ものを 子供に みせる（みせて）

それぞれの うごきには、つぎを つけます:
- color: red（あか）、blue（あお）、yellow（きいろ）、green（みどり）、none
- thing: ball（ボール）、block（つみき）、other（テーブルに ない もの）、none
- to: box（はこ）、basket（かご）、none
- all: 「ぜんぶ」「みんな」のときや、しゅるいごとに かたづけるときは true

だいじな きまり:
- color、thing、to には、子供が いった ことばだけを いれます。いわなかったら none です。
  「あかい ボール」なら color は red、thing は ball です。「ボール」だけなら color は none です。
- 「それ」「これ」と いわれたら、color も thing も none に します。
- 子供が いわなかった ことを、あてずっぽうで おぎなっては いけません。
- うごきの おねがいで ない ときは、actions を [] にして、reply で みじかく おしゃべりします。
- うごきの おねがいの ときの reply は「いいよ！」のような みじかい へんじに します。
- 絵文字や顔文字は つかいません。

れい:
子供「あかい ボールを はこに いれて」
{{"actions":[{{"do":"put","color":"red","thing":"ball","to":"box","all":false}}],"reply":"いいよ！","motion":"nod"}}
子供「ボールを とって」
{{"actions":[{{"do":"pick","color":"none","thing":"ball","to":"none","all":false}}],"reply":"いいよ！","motion":"nod"}}
子供「それを かごに いれて」
{{"actions":[{{"do":"put","color":"none","thing":"none","to":"basket","all":false}}],"reply":"まかせて！","motion":"nod"}}
子供「あおいのを ぜんぶ はこに かたづけて」
{{"actions":[{{"do":"put","color":"blue","thing":"none","to":"box","all":true}}],"reply":"やってみるね！","motion":"nod"}}
子供「あかい ボールと きいろい つみきを かごに いれて」
{{"actions":[{{"do":"put","color":"red","thing":"ball","to":"basket","all":false}},{{"do":"put","color":"yellow","thing":"block","to":"basket","all":false}}],"reply":"いいよ！","motion":"nod"}}
子供「ボールは かご、つみきは はこに かたづけて」
{{"actions":[{{"do":"put","color":"none","thing":"ball","to":"basket","all":true}},{{"do":"put","color":"none","thing":"block","to":"box","all":true}}],"reply":"いいよ！","motion":"nod"}}
子供「きいろい つみきを みせて」
{{"actions":[{{"do":"show","color":"yellow","thing":"block","to":"none","all":false}}],"reply":"いいよ！","motion":"nod"}}
子供「はなして」
{{"actions":[{{"do":"drop","color":"none","thing":"none","to":"none","all":false}}],"reply":"はーい！","motion":"nod"}}
子供「あかいのは どれ？」
{{"actions":[{{"do":"point","color":"red","thing":"none","to":"none","all":false}}],"reply":"えっとね","motion":"nod"}}
子供「りんごを とって」
{{"actions":[{{"do":"pick","color":"none","thing":"other","to":"none","all":false}}],"reply":"りんごは ないみたい","motion":"tilt"}}
子供「きみの すきな いろは？」
{{"actions":[],"reply":"ぼくは あおが すきだよ！きみは なにいろが すき？","motion":"happy"}}"""

_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "do": {"type": "string", "enum": list(DOS)},
        "color": {"type": "string", "enum": [*COLOR_LABEL, "none"]},
        "thing": {"type": "string", "enum": [*KIND_LABEL, "other", "none"]},
        "to": {"type": "string", "enum": [*BIN_LABEL, "none"]},
        "all": {"type": "boolean"},
    },
    "required": ["do", "color", "thing", "to", "all"],
}
_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {"type": "array", "items": _ACTION_SCHEMA},
        "reply": {"type": "string"},
        "motion": {"type": "string", "enum": list(MOTIONS)},
    },
    "required": ["actions", "reply", "motion"],
}

# 会話履歴として LLM に渡す往復数。「ちがう、あかいほう」のような言い直しに使う。
HISTORY_TURNS = 3
# 1 回のおねがいで行う動きの上限。崩れた出力で延々と動き続けないように。
_MAX_ACTIONS = 6


# ------------------------------------------------------------ ことばの扱い

def _kana(text: str) -> str:
    """全角・半角をそろえ、カタカナをひらがなに直す。言いかえの照合用。"""
    text = unicodedata.normalize("NFKC", text)
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def _said(text: str, key: str) -> bool:
    return any(w in text for w in _WORDS[key])


def _said_verb(text: str, do: str) -> bool:
    return any(w in text for w in _VERBS[do])


def _destinations(text: str) -> list[str]:
    """ことばに出てきた入れ物のうち、行き先として言われたもの（「はこに」「かご！」）。"""
    found = []
    for b in BIN_LABEL:
        for w in _WORDS[b]:
            start = text.find(w)
            while start >= 0:
                if not _FROM.match(text, start + len(w)) and b not in found:
                    found.append(b)
                start = text.find(w, start + 1)
    return found


def _parse_plan(content: str) -> tuple[list[dict], str, str]:
    """LLM の出力から (動きの並び, 返事, しぐさ) を取り出す。おかしな値は捨てる。"""
    data = None
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict):
        logger.warning("ゲームの指示を JSON として読めませんでした: %s", content[:200])
        return [], "", "tilt"

    actions = []
    for a in data.get("actions") or []:
        if not isinstance(a, dict) or a.get("do") not in DOS:
            continue
        actions.append({
            "do": a["do"],
            "color": a.get("color") if a.get("color") in COLOR_LABEL else None,
            "thing": a.get("thing") if a.get("thing") in (*KIND_LABEL, "other") else None,
            "to": a.get("to") if a.get("to") in BIN_LABEL else None,
            "all": a.get("all") is True,
        })
    reply = _clean(str(data.get("reply") or ""))
    motion = data.get("motion") if data.get("motion") in MOTIONS else "nod"
    return actions[:_MAX_ACTIONS], reply, motion


def _ground(text: str, actions: list[dict], pending: dict | None) -> list[dict]:
    """LLM の読み取りを、子供が実際に言ったことばに合わせる。

    - 言っていない色・種類・入れ物は捨てる。LLM が気をきかせて補ったものも捨てる。
      ここで捨てるから、リーチーは「言われたとおり」にしか動かない。
    - 逆に LLM が言われたことを落としていたら、ことばから拾い直す
      （おねがいが 1 つのときだけ。2 つ以上だと、どれに付く言葉か決められない）。
    - 何を動かすかも、どう動かすかも言っていないものは捨てる。
      「こんにちは」で物を動かさないため。
    - 前のターンでリーチーが聞き返していたら（pending）、その答えとしてつなぐ。
    """
    said = _kana(text)
    colors = [c for c in COLOR_LABEL if _said(said, c)]
    kinds = [k for k in KIND_LABEL if _said(said, k)]
    bins = _destinations(said)
    verbs = [d for d in _VERB_ORDER if _said_verb(said, d)]
    single = len(actions) == 1

    grounded = []
    for a in actions:
        a = dict(a)
        if a["color"] not in colors:
            a["color"] = None
        if a["thing"] in KIND_LABEL and a["thing"] not in kinds:
            a["thing"] = None
        if a["to"] not in bins:
            a["to"] = None
        if single:
            # 小さいモデルは「いれて」を pick と読むことがある。ことばにある動きを優先する。
            if verbs:
                a["do"] = verbs[0]
            # 行き先を言っているなら、つかむだけで終わらせず入れる（「あかいのを かごに」）
            if a["do"] in ("pick", "putdown", "drop") and bins:
                a["do"] = "put"
            if a["color"] is None and len(colors) == 1:
                a["color"] = colors[0]
            if a["thing"] in (None, "other") and len(kinds) == 1:
                a["thing"] = kinds[0]
            if a["to"] is None and len(bins) == 1 and a["do"] in ("put", "point"):
                a["to"] = bins[0]
        if a["do"] == "pick":
            a["to"] = None   # 「はこから とって」の「はこ」は行き先ではない
        # LLM は「テーブルに おいて」のテーブルや「はこは どこ？」のはこを、
        # 「テーブルにない物 (other)」と読むことがある。物を指していないので外す。
        if a["thing"] == "other" and (
                a["do"] in ("putdown", "drop")
                or (a["do"] == "point" and a["to"] and a["color"] is None)):
            a["thing"] = None
        if a["color"] is None and a["thing"] is None and a["to"] is None \
                and not _said_verb(said, a["do"]):
            continue
        grounded.append(a)

    if pending:
        if grounded:
            first = grounded[0]
            if pending["do"] == "put" and first["do"] in ("pick", "put"):
                first["do"] = "put"
                first["to"] = first["to"] or pending.get("to")
            elif pending["do"] == "show" and first["do"] == "pick":
                first["do"] = "show"
        elif colors or kinds or bins:
            # 「どこに いれる？」に「はこ！」とだけ答えたときなど。LLM がおしゃべりと
            # 受けとって動きを返さなくても、聞き返しの答えとして扱う。
            grounded = [{
                "do": pending["do"],
                "color": colors[0] if len(colors) == 1 else None,
                "thing": kinds[0] if len(kinds) == 1 else None,
                "to": bins[0] if len(bins) == 1 else pending.get("to"),
                "all": False,
            }]
    return grounded


def _spec_label(color: str | None, kind: str | None) -> str:
    if color and kind:
        return f"{COLOR_LABEL[color]} {KIND_LABEL[kind]}"
    if color:
        return COLOR_ONLY[color]
    if kind:
        return KIND_LABEL[kind]
    return "それ"


# ------------------------------------------------------------ 腕の動き

def _arm(side: str, joints: list[float], duration: float) -> tuple:
    return ("arm" if side == "r" else "larm", joints, duration)


def _kind(key) -> str:
    return key[0] if isinstance(key, tuple) else key


# 天板をくぐらないことを確かめてある、待ちの姿勢 (ready) を通らない近道
_DIRECT = {("slot", "slot"), ("slot", "bin"), ("bin", "slot"), ("slot", "show")}
_TRAVEL = {"slot": 1.0, "bin": 1.1, "show": 1.0, "ready": 0.9}


def _duration(step: tuple) -> float:
    if step[0] in ("look",):
        return 0.0
    if step[0] == "wait":
        return step[1]
    return float(step[-1])


@dataclass
class Thing:
    id: str
    color: str
    kind: str
    where: str   # 置き場所 (r1〜l3) / 入れ物 (box, basket) / 手 (hand_r, hand_l)
    home: str    # おだいを始めたときの置き場所

    @property
    def label(self) -> str:
        return f"{COLOR_LABEL[self.color]} {KIND_LABEL[self.kind]}"


class Game:
    """おかたづけゲームの進行。物の置き場所、腕の位置、おだいの進み具合を持つ。"""

    def __init__(self, robot, brain, seed: int | None = None) -> None:
        self.robot = robot
        self.brain = brain
        self.poses = table.build()
        self._rng = random.Random(seed)
        self._lock = asyncio.Lock()   # おねがいは 1 つずつ順に処理する
        self.active = False
        self.mission: int | None = 0  # None はじゆう あそび
        # 腕がいまどの姿勢にいるか。動かす道すじ（天板をくぐらない道）を選ぶのに使う
        self.arm_at: dict[str, object] = {"r": "rest", "l": "rest"}
        self._history: list[dict] = []
        self._reset_state()

    # ------------------------------------------------------------ 状態

    def _layout(self) -> dict[str, str]:
        return FREE_PLAY if self.mission is None else MISSIONS[self.mission]["objects"]

    def _reset_state(self) -> None:
        self.objects: dict[str, Thing] = {}
        for oid, slot in self._layout().items():
            color, kind = oid.split("_")
            self.objects[oid] = Thing(oid, color, kind, slot, slot)
        self.bins: dict[str, list[str]] = {name: [] for name in table.BINS}
        self.hands: dict[str, str | None] = {"r": None, "l": None}
        self.focus_obj: str | None = None   # さっき動かした物（「それ」の行き先）
        self.focus_bin: str | None = None   # さっき使った入れ物
        self.pending: dict | None = None    # リーチーが聞き返している内容
        self.shown: str | None = None       # このターンで見せた物
        self.turns = 0
        self.lucky = False   # 当てずっぽうで選んだことがあるか（あれば星は 2 つまで）
        self.cleared = False
        self.stars = 0
        self._acked = False
        self._history.clear()

    def world(self) -> dict:
        """ブラウザに送る、テーブルの上のようす。"""
        mission = None
        if self.mission is not None:
            spec = MISSIONS[self.mission]
            mission = {
                "index": self.mission, "total": len(MISSIONS), "title": spec["title"],
                "turns": self.turns, "par": spec["par"],
                "cleared": self.cleared, "stars": self.stars,
            }
        return {
            "active": self.active,
            "table": table.TABLE,
            "obj_radius": table.OBJ_RADIUS,
            "bin_height": table.BIN_HEIGHT,
            "slots": {n: s["pos"] for n, s in self.poses["slots"].items()},
            "bins": {n: {"pos": b["pos"], "label": BIN_LABEL[n], "items": list(self.bins[n])}
                     for n, b in self.poses["bins"].items()},
            "objects": [{"id": t.id, "kind": t.kind, "color": t.color,
                         "label": t.label, "where": t.where} for t in self.objects.values()],
            "mission": mission,
        }

    def _describe(self) -> str:
        """LLM に渡す、いまのテーブルのようす。"""
        def names(ids) -> str:
            return "、".join(self.objects[i].label for i in ids) or "なし"

        on_table = [t.id for t in self.objects.values() if t.where in table.SLOTS]
        held = [i for i in self.hands.values() if i]
        return ("いまの ようす:\n"
                f"- テーブルの うえ: {names(on_table)}\n"
                f"- はこの なか: {names(self.bins['box'])}\n"
                f"- かごの なか: {names(self.bins['basket'])}\n"
                f"- リーチーが もっている もの: {names(held)}")

    # ------------------------------------------------------------ 送信

    async def _world(self, out, how: str | None = None, obj: str | None = None, **extra) -> None:
        cause = {"how": how, "obj": obj, **extra} if how else None
        await out({"type": "world", "world": self.world(), "cause": cause})

    async def _say(self, out, text: str, motion: str | None = None) -> None:
        await out({"type": "say", "text": text, "motion": motion})
        if motion:
            await asyncio.to_thread(self.robot.play, motion)

    async def _move(self, steps: list[tuple], name: str = "game") -> None:
        if not steps:
            return
        if self.robot.connected:
            await asyncio.to_thread(self.robot.run, steps, name)
            return
        # Sim がないときも、画面の物が動くあいだだけ待って、テンポをそろえる
        await asyncio.sleep(min(1.5, 0.3 * sum(_duration(s) for s in steps)))

    # ------------------------------------------------------------ 姿勢

    def _pose(self, side: str, key) -> list[float]:
        if key in ("ready", "show", "tuck"):
            return self.poses[key][side]
        if key == "rest":
            return table.REST[side]
        kind, name = key
        if kind == "slot":
            return self.poses["slots"][name]["above"]
        return self.poses["bins"][name]["above"][side]

    def _route(self, side: str, key) -> list[tuple]:
        """いまの姿勢から key の姿勢へ行くステップ。天板をくぐらない道だけを通る。"""
        here = self.arm_at[side]
        steps: list[tuple] = []
        if here == key:
            return steps
        if here in ("rest", "tuck"):
            if here == "rest":
                steps.append(_arm(side, self.poses["tuck"][side], 0.8))
            steps.append(_arm(side, self.poses["ready"][side], 0.8))
            here = "ready"
        if here != key and here != "ready" and key != "ready" \
                and (_kind(here), _kind(key)) not in _DIRECT:
            steps.append(_arm(side, self.poses["ready"][side], 0.9))
            here = "ready"
        if here != key:
            steps.append(_arm(side, self._pose(side, key), _TRAVEL[_kind(key)]))
        self.arm_at[side] = key
        return steps

    def _sync_arms(self) -> None:
        """腕が思っている姿勢にいるか、Sim の実際の角度で確かめる。

        Sim の腕は、ほかのプログラムや会話のジェスチャーからも動かせる。思っている
        姿勢とずれたまま近道を通ると、天板をくぐることがある。ずれていたら
        「腕を下ろしている」とみなし、肘を曲げて胸の前に寄せる道から構え直す。
        """
        for side in ("r", "l"):
            actual = self.robot.arm_joints(side)
            if actual is None:
                continue
            expected = self._pose(side, self.arm_at[side])[:4]
            if max(abs(a - e) for a, e in zip(actual, expected)) > 12.0:
                logger.info("%s腕が思っていた姿勢 (%s) にいないので、構え直します", side, self.arm_at[side])
                self.arm_at[side] = "rest"

    def _settle_steps(self) -> list[tuple]:
        """手の空いている腕を待ちの姿勢へ戻す。持っている腕はそのまま。"""
        steps: list[tuple] = []
        for side in ("r", "l"):
            if self.hands[side] is None and self.arm_at[side] != "ready":
                steps += self._route(side, "ready")
                steps.append(("grip", side, _CLOSED, 0.3))
        return steps

    # ------------------------------------------------------------ 進行

    async def start(self, out) -> None:
        """ゲームの画面を開いたとき。腕を構えて、おだいを読み上げる。"""
        async with self._lock:
            self.active = True
            self._sync_arms()
            await self._world(out, "reset")
            await self._move(self._home_steps(), "game-start")
            if self.cleared:
                # クリアしたあとに画面を開き直したときは、つぎへ進むか選べる表示に戻す
                await out(self._clear_message())
            else:
                await self._intro(out)

    async def leave(self, out=None) -> None:
        """おしゃべりの画面に戻ったとき。持っている物を戻して、腕を下ろす。"""
        async with self._lock:
            if not self.active:
                return
            self.active = False
            for side, oid in self.hands.items():
                if oid:
                    self._put_back(self.objects[oid])
                    self.hands[side] = None
            steps = [("grip", "r", _OPEN, 0.3), ("grip", "l", _OPEN, 0.3)]
            for side in ("r", "l"):
                steps += self._route(side, "ready")
            steps += [("arms", self.poses["tuck"]["r"], self.poses["tuck"]["l"], 0.8),
                      ("arms", table.REST["r"], table.REST["l"], 1.0),
                      ("grip", "r", _CLOSED, 0.3), ("grip", "l", _CLOSED, 0.3)]
            self.arm_at = {"r": "rest", "l": "rest"}
            await self._move(steps, "game-leave")
            if out:
                await self._world(out)

    async def next(self, out) -> None:
        async with self._lock:
            if self.mission is None:
                return
            self.mission = self.mission + 1 if self.mission + 1 < len(MISSIONS) else None
            await self._begin(out)

    async def retry(self, out) -> None:
        async with self._lock:
            await self._begin(out)

    async def restart(self, out) -> None:
        async with self._lock:
            self.mission = 0
            await self._begin(out)

    async def _begin(self, out) -> None:
        self._sync_arms()
        self._reset_state()
        await self._world(out, "reset")
        steps = [("grip", "r", _OPEN, 0.3), ("grip", "l", _OPEN, 0.3)] + self._home_steps()
        await self._move(steps, "game-reset")
        await self._intro(out)

    def _home_steps(self) -> list[tuple]:
        if self.arm_at == {"r": "rest", "l": "rest"}:
            # 下ろした腕を、肘を曲げて胸の前へ引き寄せてから構える（天板をくぐらないように）
            self.arm_at = {"r": "ready", "l": "ready"}
            return [("arms", self.poses["tuck"]["r"], self.poses["tuck"]["l"], 0.9),
                    ("arms", self.poses["ready"]["r"], self.poses["ready"]["l"], 0.8),
                    ("grip", "r", _CLOSED, 0.3), ("grip", "l", _CLOSED, 0.3)]
        return self._settle_steps()

    async def _intro(self, out) -> None:
        if self.mission is None:
            await self._say(out, "じゆう あそびだよ。すきなように おねがいしてね！", "happy")
        else:
            spec = MISSIONS[self.mission]
            await self._say(out, f"おだい {self.mission + 1}。{spec['title']}。", "nod")

    # ------------------------------------------------------------ 1 ターン

    async def handle(self, text: str, out) -> None:
        """子供のおねがいを 1 つ処理する。終わったら done を送る。"""
        async with self._lock:
            try:
                await self._turn(text, out)
            except Exception:
                logger.exception("ゲームのターンでエラー")
                await self._say(out, _FALLBACK, "tilt")
            finally:
                await out({"type": "done"})

    async def _turn(self, text: str, out) -> None:
        # 安全のしくみは会話と同じものを先に通す。ゲームの手数には数えない。
        canned = _screen(text)
        if canned is not None:
            reply, motion = canned
            self._remember(text, [], reply)
            await self._say(out, reply, motion)
            return

        await out({"type": "thinking"})
        await asyncio.to_thread(self.robot.play, "think")
        actions, reply, motion = await self._plan(text)
        pending, self.pending = self.pending, None
        actions = _ground(text, actions, pending)

        if not actions:
            # 動きのおねがいではなかった。ふつうにおしゃべりする。
            # 手をふる動きは、構えている腕とぶつかるので、ゲーム中はよろこぶ動きにする。
            if not reply or _ACK_ONLY.match(reply):
                reply = _ASK_AGAIN
            self._remember(text, [], reply)
            await self._say(out, reply, "happy" if motion == "wave" else motion)
            return

        self.turns += 1
        self.shown = None
        self._acked = False
        self._sync_arms()
        self._remember(text, actions, "")
        for action in actions:
            await self._perform(action, out)
        await self._move(self._settle_steps() + [_FACE_KID], "game-settle")
        await self._world(out)
        await self._check_goal(out)

    async def _plan(self, text: str) -> tuple[list[dict], str, str]:
        system = GAME_PROMPT
        if _HARM_TOPIC.search(text):
            system = f"{GAME_PROMPT}\n\n{_CAUTION_NOTE}"
        messages = [
            {"role": "system", "content": system},
            *self._history,
            {"role": "user", "content": f"{self._describe()}\n子供「{text}」"},
        ]
        content = await self.brain.ask(messages, _SCHEMA, temperature=0.2, num_predict=300)
        if content is None:
            return [], _FALLBACK, "tilt"
        return _parse_plan(content)

    def _remember(self, text: str, actions: list[dict], reply: str) -> None:
        # 履歴には、サーバが読み取り直したあとの動きを残す。LLM が補った色などを
        # 残すと、次のターンでも同じ補い方をまねるため。
        shown = [{k: (v if v is not None else "none") for k, v in a.items()} for a in actions]
        self._history.append({"role": "user", "content": f"子供「{text}」"})
        self._history.append({"role": "assistant", "content": json.dumps(
            {"actions": shown, "reply": reply or "いいよ！", "motion": "nod"}, ensure_ascii=False)})
        del self._history[: max(0, len(self._history) - HISTORY_TURNS * 2)]

    async def _check_goal(self, out) -> None:
        if self.mission is None or self.cleared:
            return
        spec = MISSIONS[self.mission]
        goal = spec["goal"]
        if "in" in goal:
            met = all(self.objects[o].where == b for o, b in goal["in"].items())
        else:
            met = self.shown == goal["show"]
        if not met:
            return
        par = spec["par"]
        self.cleared = True
        self.stars = 3 if self.turns <= par else 2 if self.turns <= par + 2 else 1
        if self.lucky:
            # 当てずっぽうがたまたま当たっただけなら満点にしない。
            # 色と種類をきちんと伝えれば星 3 つ、という形でことばの正確さを教える。
            self.stars = min(self.stars, 2)
        await self._world(out)
        await out(self._clear_message())
        last = self.mission == len(MISSIONS) - 1
        await self._say(out, "やったー！ ぜんぶ クリア！ すごいね！" if last
                        else "やったー！ おだい クリア！", "happy")

    def _clear_message(self) -> dict:
        return {"type": "clear", "stars": self.stars, "turns": self.turns,
                "par": MISSIONS[self.mission]["par"],
                "last": self.mission == len(MISSIONS) - 1, "lucky": self.lucky}

    # ------------------------------------------------------------ どれのことか

    def _held(self) -> list[Thing]:
        return [self.objects[i] for i in self.hands.values() if i]

    def _resolve(self, a: dict, purpose: str) -> tuple[list[Thing], str | None, str | None]:
        """おねがいが指している物を決める。(物, 言うこと, ヒント) を返す。

        候補が 1 つに決まらないときは、当てずっぽうで 1 つ選ぶ（ゲームのおもしろさ）。
        そのときは「〇〇は 2こ あるね」と言って、まちがえる理由が分かるようにする。
        """
        color, kind = a["color"], a["thing"]
        if kind == "other":
            return [], self._not_here("それは"), None

        if color is None and kind is None:
            # 「それ」: 持っている物、なければ さっき動かした物
            held = self._held()
            if held and purpose == "pick":
                return [], "もう もってるよ！", None
            if held and purpose != "point":
                return (held if a["all"] or purpose == "put" else held[:1]), None, None
            focus = self.objects.get(self.focus_obj or "")
            if focus is not None and purpose in ("put", "show", "pick"):
                if purpose == "put" and focus.where == a["to"]:
                    return [], f"{focus.label}は もう {BIN_LABEL[focus.where]}に はいってるよ！", None
                return [focus], None, None
            asks = {"put": "なにを いれる？", "pick": "どれを とる？",
                    "show": "どれを みせる？", "point": "どれの こと？"}
            return [], asks[purpose], None

        cands = [t for t in self.objects.values()
                 if (color is None or t.color == color) and (kind is None or t.kind == kind)]
        label = _spec_label(color, kind)
        if not cands:
            return [], self._not_here(f"{label}は"), None
        if purpose == "put" and a["to"]:
            rest = [t for t in cands if t.where != a["to"]]
            if not rest:
                where = BIN_LABEL[a["to"]]
                return [], f"{label}は もう {where}に はいってるよ！", None
            cands = rest
        if purpose == "pick":
            free = [t for t in cands if not t.where.startswith("hand")]
            if not free:
                return [], "もう もってるよ！", None
            cands = free

        held = [t for t in cands if t.where.startswith("hand")]
        on_table = [t for t in cands if t.where in table.SLOTS]
        in_bins = [t for t in cands if t.where in table.BINS]
        if a["all"] and purpose == "put":
            return held + on_table + in_bins, None, None
        if purpose == "point":
            return (on_table + in_bins + held)[:3], None, None
        group = (held if purpose in ("put", "show") else []) or on_table or in_bins or held
        if len(group) == 1:
            return group, None, None

        guess = self._rng.choice(group)
        self.lucky = True
        hint = self._hint(color, kind)
        return [guess], f"{label}は {len(group)}こ あるね。うーん、これかな？", hint

    def _hint(self, color: str | None, kind: str | None) -> str:
        if color is None and kind is None:
            return "ヒント: いろと なまえを いうと、どれの ことか つたわるよ"
        if color is None:
            return "ヒント: いろも いうと、どれの ことか つたわるよ"
        return "ヒント: ボールか つみきかも いうと、どれの ことか つたわるよ"

    def _not_here(self, subject: str) -> str:
        on_table = [t.label for t in self.objects.values() if t.where in table.SLOTS]
        if on_table:
            return f"{subject} ここには ないみたい。テーブルには {'と '.join(on_table)}が あるよ。"
        return f"{subject} ここには ないみたい。"

    # ------------------------------------------------------------ 動き

    async def _ack(self, out) -> None:
        """「いいよ！」は、実際に動けるとわかったときに 1 回だけ言う。"""
        if not self._acked:
            self._acked = True
            await self._say(out, self._rng.choice(_ACKS))

    async def _perform(self, a: dict, out) -> None:
        do = a["do"]
        if do in ("drop", "putdown"):
            await self._release(a, out, gently=(do == "putdown"))
            return
        if do == "point" and a["to"] and a["color"] is None and a["thing"] is None:
            await self._point_bin(a["to"], out)
            return

        things, note, hint = self._resolve(a, do)
        if things:
            await self._ack(out)
        if note:
            await self._say(out, note)
        if hint:
            await out({"type": "hint", "text": hint})
        if not things:
            if do == "put" and note == "なにを いれる？":
                self.pending = {"do": "put", "to": a["to"]}
            elif do == "show" and note == "どれを みせる？":
                self.pending = {"do": "show"}
            await self._move([("head", 25.0, -10.0, 0.0, 0.5), _FACE_KID], "game-ask")
            return

        if do == "point":
            for t in things:
                await self._point_at(t, out)
            return
        if do == "pick":
            for t in things[:1]:
                if await self._pick(t, out):
                    await self._say(out, f"{t.label}を もったよ。")
            return
        if do == "show":
            await self._show(things[0], out)
            return

        # put
        bin_name = a["to"] or self.focus_bin
        if bin_name is None:
            # 入れ物を言われていないときは、物を持って聞き返す
            if await self._pick(things[0], out):
                self.pending = {"do": "put"}
                await self._say(out, "どこに いれる？ はこ？ かご？")
            return
        for t in things:
            await self._put(t, bin_name, out)

    def _arm_for_pick(self, t: Thing) -> str:
        if t.where in table.SLOTS:
            return self.poses["slots"][t.where]["arm"]
        # 入れ物の中の物は、空いている手で取る（入れ物の側の手を優先）
        near = self.poses["bins"][t.where]["arm"]
        far = "l" if near == "r" else "r"
        return near if self.hands[near] is None or self.hands[far] is not None else far

    async def _pick(self, t: Thing, out) -> str | None:
        """物をつかんで持ち上げる。つかんだ手 (r/l) を返す。"""
        if t.where.startswith("hand_"):
            return t.where[-1]
        side = self._arm_for_pick(t)
        busy = self.hands[side]
        if busy is not None:
            await self._say(out, "いったん おくね。")
            if not await self._putdown(self.objects[busy], side, out):
                return None

        if t.where in table.SLOTS:
            spot = self.poses["slots"][t.where]
            key, low, look = ("slot", t.where), spot["at"], spot["look"]
        else:
            spot = self.poses["bins"][t.where]
            key, low, look = ("bin", t.where), spot["in"][side], spot["look"]
        steps = [("look", *look, 0.5), ("grip", side, _OPEN, 0.3)] + self._route(side, key)
        steps += [_arm(side, low, 0.6), ("grip", side, _HOLD, 0.4)]
        await self._move(steps, "pick")

        if t.where in self.bins:
            self.bins[t.where].remove(t.id)
        t.where = f"hand_{side}"
        self.hands[side] = t.id
        self.focus_obj = t.id
        await self._world(out, "grab", t.id, side=side)
        await self._move([_arm(side, self._pose(side, key), 0.5)], "lift")
        return side

    async def _put(self, t: Thing, bin_name: str, out) -> None:
        side = await self._pick(t, out)
        if side is None:
            return
        spot = self.poses["bins"][bin_name]
        steps = [("look", *spot["look"], 0.5)] + self._route(side, ("bin", bin_name))
        steps += [_arm(side, spot["in"][side], 0.45), ("grip", side, _OPEN, 0.3)]
        await self._move(steps, "put")

        t.where = bin_name
        self.bins[bin_name].append(t.id)
        self.hands[side] = None
        self.focus_obj, self.focus_bin = t.id, bin_name
        await self._world(out, "place", t.id, side=side)
        await self._move([_arm(side, self._pose(side, ("bin", bin_name)), 0.4)], "put-lift")
        await self._say(out, f"{t.label}を {BIN_LABEL[bin_name]}に いれたよ！")

    async def _show(self, t: Thing, out) -> None:
        side = await self._pick(t, out)
        if side is None:
            return
        await self._say(out, f"みて みて！ {t.label}だよ！")
        steps = self._route(side, "show") + [
            ("head", 0.0, -15.0, 0.0, 0.4),
            ("ant", 40.0, -40.0, 0.25), ("ant", -20.0, 20.0, 0.25), ("ant", 0.0, 0.0, 0.25),
            ("wait", 0.6),
        ]
        await self._move(steps, "show")
        self.shown = t.id
        await self._world(out, "show", t.id, side=side)
        await self._move(self._route(side, "ready"), "show-back")

    async def _point_at(self, t: Thing, out) -> None:
        if t.where.startswith("hand_"):
            await self._say(out, f"{t.label}は いま もってるよ。")
            return
        if t.where in table.SLOTS:
            spot = self.poses["slots"][t.where]
        else:
            spot = self.poses["bins"][t.where]
        await self._point(spot, f"{t.label}は これだよ。", out)

    async def _point_bin(self, bin_name: str, out) -> None:
        await self._ack(out)
        await self._point(self.poses["bins"][bin_name], f"{BIN_LABEL[bin_name]}は ここだよ。", out)

    async def _point(self, spot: dict, line: str, out) -> None:
        # 空いている手で指さす。両手がふさがっていたら、持ったまま指さす。
        arms = [s for s in spot["point"] if self.hands[s] is None] or list(spot["point"])
        side = arms[0]
        steps = self._route(side, "ready")
        if self.hands[side] is None:
            steps.append(("grip", side, _CLOSED, 0.2))
        steps += [("look", *spot["look"], 0.5), _arm(side, spot["point"][side], 0.9)]
        await self._say(out, line)
        await self._move(steps + [("wait", 0.8), _arm(side, self.poses["ready"][side], 0.8)], "point")

    async def _release(self, a: dict, out, gently: bool) -> None:
        held = self._held()
        if a["color"] or a["thing"] in KIND_LABEL:
            held = [t for t in held if (a["color"] in (None, t.color))
                    and (a["thing"] in (None, "other", t.kind))]
        if not held:
            await self._say(out, "なにも もってないよ。")
            await self._move([("head", 25.0, -10.0, 0.0, 0.5), _FACE_KID], "game-ask")
            return
        await self._ack(out)
        for t in held:
            side = t.where[-1]
            if gently:
                if await self._putdown(t, side, out):
                    await self._say(out, "テーブルに おいたよ。")
            else:
                await self._drop(t, side, out)

    def _free_slots(self, side: str | None = None) -> list[str]:
        used = {t.where for t in self.objects.values()}
        return [s for s, spot in self.poses["slots"].items()
                if s not in used and (side is None or spot["arm"] == side)]

    def _put_back(self, t: Thing) -> None:
        """物を元の置き場所（ふさがっていたら空いている所）へ戻す。画面の外での片づけ用。"""
        free = self._free_slots()
        t.where = t.home if t.home in free else (free[0] if free else t.home)

    async def _putdown(self, t: Thing, side: str, out) -> bool:
        """持っている物を、その手の側のテーブルへそっと置く。"""
        free = self._free_slots(side)
        if not free:
            await self._say(out, "おく ところが ないよ。")
            return False
        slot = t.home if t.home in free else free[0]
        spot = self.poses["slots"][slot]
        steps = [("look", *spot["look"], 0.5)] + self._route(side, ("slot", slot))
        steps += [_arm(side, spot["at"], 0.5), ("grip", side, _OPEN, 0.3)]
        await self._move(steps, "putdown")
        t.where = slot
        self.hands[side] = None
        self.focus_obj = t.id
        await self._world(out, "putdown", t.id, side=side)
        await self._move([_arm(side, spot["above"], 0.4)], "putdown-lift")
        return True

    async def _drop(self, t: Thing, side: str, out) -> None:
        """持っている物を、その場ではなす。ボールは ころがって どこかへ行く。"""
        here = self.arm_at[side]
        await self._move([("grip", side, _OPEN, 0.25)], "drop")
        self.hands[side] = None
        self.focus_obj = t.id

        if isinstance(here, tuple) and here[0] == "bin":
            # 入れ物の上ではなしたら、そのまま中へ落ちる
            t.where = here[1]
            self.bins[here[1]].append(t.id)
            self.focus_bin = here[1]
            await self._world(out, "place", t.id, side=side)
            await self._say(out, f"ぽとん！ {BIN_LABEL[here[1]]}に はいったよ。")
            return

        free = self._free_slots()
        below = here[1] if isinstance(here, tuple) and here[1] in free else None
        landing = below or (self._free_slots(side) or free or [t.home])[0]
        if t.kind == "ball" and len(free) > 1:
            dest = self._rng.choice([s for s in free if s != landing])
            t.where = dest
            await self._world(out, "roll", t.id, side=side, via=landing)
            line = "あっ！ ころがっちゃった！"
        else:
            t.where = landing
            await self._world(out, "drop", t.id, side=side)
            line = "ごとん！ おちちゃった。"
        await self._say(out, line)
        await self._move([("ant", 70.0, -70.0, 0.2), ("head", 0.0, -28.0, 0.0, 0.3),
                          ("wait", 0.5), ("ant", 0.0, 0.0, 0.3)], "oops")
