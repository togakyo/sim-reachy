#!/usr/bin/env python
"""おかたづけゲームのしくみが意図どおり働くか確かめる。Sim も LLM も使わない。

    .venv/bin/python scripts/check_game.py

おだいやことば（server/game.py）、置き場所（server/table.py）を変えたら流してください。
確かめるのは次の 3 つです。

1. 腕の姿勢: すべての置き場所に手が届き、関節が可動域に収まり、
   動くとちゅうで腕が天板をくぐらないか
2. ことばの読み取り: LLM の出力を、子供が言ったことばに合わせて直せているか
   （言っていない色は捨てる、「いれて」を「とって」と読んだら直す、など）
3. ゲームの進み方: 当てずっぽうで選んだときの星の数や、聞き返しへの答え方
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import game  # noqa: E402
import table  # noqa: E402

failed: list[str] = []


def check(ok: bool, label: str) -> None:
    print(f"  {'OK ' if ok else 'NG '} {label}")
    if not ok:
        failed.append(label)


# ------------------------------------------------------------ 1. 腕の姿勢

def under_table(side: str, a: list[float], b: list[float], skip_end: float = 0.0) -> bool:
    """関節を a から b へ動かすあいだに、腕が天板より下をくぐるか。"""
    t = table.TABLE
    steps = 40
    for i in range(steps + 1):
        if i / steps > 1 - skip_end:
            break
        q = [x + (y - x) * i / steps for x, y in zip(a, b)]
        elbow, hand = table.arm_points(side, q)
        for k in range(7):
            p = [e + (h - e) * k / 6 for e, h in zip(elbow, hand)]
            if p[2] > t["back"] and abs(p[0]) < t["half_width"] and p[1] < t["y"] + 0.04:
                return True
    return False


def check_poses() -> None:
    print("腕の姿勢")
    try:
        poses = table.build()
    except ValueError as exc:
        check(False, f"姿勢を計算できません: {exc}")
        return
    check(True, "すべての置き場所と入れ物に、可動域の中で手が届く")

    worst = max(
        abs(a - b)
        for spot in poses["slots"].values()
        for a, b in zip(table.hand_position(spot["arm"], spot["at"]), spot["pos"]))
    check(worst < 0.01, f"手先が物の位置に届く（ずれ {worst * 100:.1f} cm）")

    paths = []
    for side in ("r", "l"):
        ready = poses["ready"][side]
        paths += [(side, table.REST[side], poses["tuck"][side], 0.0),
                  (side, poses["tuck"][side], ready, 0.0),
                  (side, ready, poses["show"][side], 0.0)]
        mine = [s for s in poses["slots"].values() if s["arm"] == side]
        for s in mine:
            paths += [(side, ready, s["above"], 0.0), (side, s["above"], s["at"], 0.3),
                      (side, s["above"], poses["show"][side], 0.0)]
            paths += [(side, s["above"], o["above"], 0.0) for o in mine if o is not s]
            for b in poses["bins"].values():
                paths += [(side, s["above"], b["above"][side], 0.0),
                          (side, b["above"][side], s["above"], 0.0)]
        for b in poses["bins"].values():
            paths += [(side, b["above"][side], b["in"][side], 0.3), (side, b["above"][side], ready, 0.0)]
        for spot in [*poses["slots"].values(), *poses["bins"].values()]:
            if side in spot["point"]:
                paths.append((side, ready, spot["point"][side], 0.0))
    bad = sum(under_table(*p) for p in paths)
    check(bad == 0, f"動くとちゅうで天板をくぐらない（{len(paths)} 通りの動き）")


# ------------------------------------------------------- 2. ことばの読み取り

def act(do, color=None, thing=None, to=None, all=False):
    return {"do": do, "color": color, "thing": thing, "to": to, "all": all}


# (子供のことば, LLM の読み取り, 直したあとに期待するもの)
GROUND_CASES = [
    # LLM が補った色は捨てる。捨てるから、ボールが 2 つあればリーチーは迷う
    ("ボールを はこに いれて", [act("put", "blue", "ball", "box")], [act("put", None, "ball", "box")]),
    # 「いれて」を pick と読んだら put に直す
    ("ボールを はこに いれて", [act("pick", None, "ball", "basket")], [act("put", None, "ball", "box")]),
    # 動きのことばがなくても、行き先を言っていれば入れる
    ("あかいのを かごに", [act("pick", "red", "ball", "basket")], [act("put", "red", None, "basket")]),
    # 「はこから」は取り出す元。行き先にしない
    ("はこから あかい つみきを とって", [act("pick", "red", "block", "box")], [act("pick", "red", "block")]),
    # LLM が言われたことを落としたら、ことばから拾い直す
    ("あおい ものを ぜんぶ はこに いれて", [act("put", "blue", "ball", "basket", True)],
     [act("put", "blue", None, "box", True)]),
    # テーブルや入れ物を「ないもの」と読んだときは外す
    ("テーブルに おいて", [act("put", None, "other")], [act("putdown")]),
    ("はこは どこ？", [act("point", None, "other")], [act("point", None, None, "box")]),
    # カタカナ・漢字でも同じ
    ("アオいボールをカゴにいれて", [act("put", "blue", "ball", "basket")], [act("put", "blue", "ball", "basket")]),
    ("赤いボールを箱に入れて", [act("put", "red", "ball", "box")], [act("put", "red", "ball", "box")]),
    # 何も言っていないのに動こうとしたら止める
    ("こんにちは", [act("pick")], []),
]


def check_grounding() -> None:
    print("ことばの読み取り")
    for text, raw, want in GROUND_CASES:
        got = game._ground(text, raw, None)
        check(got == want, f"「{text}」 → {[(a['do'], a['color'], a['thing'], a['to']) for a in got]}")
    # 「どこに いれる？」と聞き返したあとの「かご！」は、LLM がおしゃべりと読んでも入れる
    got = game._ground("かご！", [], {"do": "put"})
    check(got == [act("put", None, None, "basket")], "聞き返しへの答え「かご！」を、入れる動きとして読む")


# ------------------------------------------------------------ 3. ゲームの進み方

class FakeRobot:
    connected = False

    def play(self, motion):
        return False

    def run(self, steps, name=""):
        return False

    def arm_joints(self, side):
        return None


class FakeBrain:
    def __init__(self):
        self.plans = []

    async def ask(self, messages, schema, **kw):
        return json.dumps(self.plans.pop(0), ensure_ascii=False)


def plan(*actions):
    return {"actions": [{k: (v if v is not None else "none") for k, v in a.items()} for a in actions],
            "reply": "いいよ！", "motion": "nod"}


async def play(g, brain, text, *actions):
    brain.plans.append(plan(*actions))
    said, events = [], []

    async def out(msg):
        if msg["type"] == "say":
            said.append(msg["text"])
        events.append(msg)
    await g.handle(text, out)
    return said, events


async def check_flow() -> None:
    print("ゲームの進み方")
    # Sim なしでも進むが、そのぶん待ち時間が入る。確認ではいらないので外す。
    game.Game._move = lambda self, steps, name="game": asyncio.sleep(0)

    brain = FakeBrain()
    g = game.Game(FakeRobot(), brain, seed=1)
    _, events = await play(g, brain, "あかい ボールを はこに いれて", act("put", "red", "ball", "box"))
    clear = [e for e in events if e["type"] == "clear"]
    check(bool(clear) and clear[0]["stars"] == 3, "おだい 1 を 1 回のおねがいでクリアすると星 3 つ")

    # おだい 3: ボールが 2 つある。色を言わないと当てずっぽうになり、当たっても星は 2 つまで
    g.mission = 2
    g._reset_state()
    said, events = await play(g, brain, "ボールを かごに いれて", act("put", None, "ball", "basket"))
    check(any("2こ あるね" in s for s in said), "あいまいなときは「2こ あるね」と言って選ぶ")
    check(any(e["type"] == "hint" for e in events), "当てずっぽうで選んだら、ヒントを出す")
    if g.objects["blue_ball"].where != "basket":
        await play(g, brain, "あおい ボールを かごに いれて", act("put", "blue", "ball", "basket"))
    check(g.cleared and g.stars <= 2, f"当てずっぽうが入るとクリアしても星は 2 つまで（星 {g.stars}）")

    # 入れ物を言わないと、持ったまま聞き返す。答えだけ言っても入れられる
    g._reset_state()
    said, _ = await play(g, brain, "あおい ボールを とって いれて", act("put", "blue", "ball"))
    check(any("どこに いれる" in s for s in said), "入れ物を言わないと「どこに いれる？」と聞く")
    brain.plans.append({"actions": [], "reply": "かごだね", "motion": "nod"})
    await g.handle("かご！", lambda msg: asyncio.sleep(0))
    check(g.objects["blue_ball"].where == "basket", "「かご！」と答えると、持っている物をかごに入れる")


def main() -> int:
    check_poses()
    check_grounding()
    asyncio.run(check_flow())
    print()
    if failed:
        print(f"{len(failed)} 件が期待と違います。")
        return 1
    print("すべて期待どおりです。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
