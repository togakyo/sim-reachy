"""おかたづけゲームのテーブル: 物と入れ物の置き場所と、そこへ手を伸ばす関節角。

座標はブラウザの 3D 表示 (web/reachy3d.js) と同じ向き・単位:
  x: Reachy の左が +（画面では右）  y: 上が +  z: Reachy の前（カメラ側）が +

ブラウザの Reachy はかたちをデフォルメしていて、腕の長さも本物とは違う。
本物の Reachy の逆運動学で角度を出すと、画面の手がテーブルの物に届かない。
子供が見ているのは画面なので、ここでは 3D モデルの腕の寸法で逆運動学を解き、
その角度を Sim に送る。Sim の腕はその角度どおりに動き、画面の手は物に届く。
どの関節を正に回すとどちらへ動くかは Sim の順運動学と同じであることを確かめてあり、
角度は本物の Reachy 2 の可動域に収めている。
"""

from __future__ import annotations

import math

# web/reachy3d.js の腕の寸法。あちらを変えたらここも合わせる。
_SHOULDER_X = 0.42
_SHOULDER_Y = 0.62
_UPPER = 0.56     # 肩 → 肘
_FORE = 0.67      # 肘 → 指のあいだ（物をつかむ位置）

# テーブル。天板は肘より少し低い高さにしている。肘と同じ高さだと、
# 前腕を前に出すだけで手が天板の下へもぐってしまう。
TABLE = {"y": -0.15, "back": 0.36, "front": 1.15, "half_width": 1.15}
# 物の大きさ（ボールの半径 / つみきの一辺の半分）と、入れ物の深さ
OBJ_RADIUS = 0.085
BIN_HEIGHT = 0.2
_OBJ_Y = TABLE["y"] + OBJ_RADIUS
_BIN_TOP = TABLE["y"] + BIN_HEIGHT

# 本物の Reachy 2 の可動域（SDK の角度、度）。URDF の値から少し内側に取っている。
# 肩の roll は内側へ 10° まで（それ以上は胴にぶつかる）。左腕は roll の向きが逆。
_PITCH = (-90.0, 60.0)
_ROLL = {"r": (-90.0, 10.0), "l": (-10.0, 90.0)}
_ELBOW_YAW = (-65.0, 65.0)
_ELBOW_PITCH = (-125.0, 0.0)

# 腕を下ろした姿勢。Reachy の default posture と同じ。
REST = {
    "r": [0.0, 10.0, -10.0, 0.0, 0.0, 0.0, 0.0],
    "l": [0.0, -10.0, 10.0, 0.0, 0.0, 0.0, 0.0],
}

# 物の置き場所（テーブルの上）。腕は置き場所の側で決まる（x < 0 は右手）。
# 片側 3 か所ずつ。ころがったボールの行き先にもなるので、物の数より多めに取る。
SLOTS: dict[str, tuple[float, float]] = {
    "r1": (-0.52, 0.64), "r2": (-0.8, 0.52), "r3": (-0.64, 0.94),
    "l1": (0.52, 0.64), "l2": (0.8, 0.52), "l3": (0.64, 0.94),
}

# 入れ物はまんなか。どちらの手からも届くようにしている。
BINS: dict[str, tuple[float, float]] = {
    "box": (-0.2, 0.72),
    "basket": (0.2, 0.72),
}


# ------------------------------------------------------------------ 計算

def _rx(a: float) -> list[list[float]]:
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0], [0, c, -s], [0, s, c]]


def _ry(a: float) -> list[list[float]]:
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def _rz(a: float) -> list[list[float]]:
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    return _scale(a, 1 / _norm(a))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _shoulder(side: str) -> tuple[float, float, float]:
    return ((-_SHOULDER_X if side == "r" else _SHOULDER_X), _SHOULDER_Y, 0.0)


def arm_points(side: str, joints: list[float]) -> tuple[tuple, tuple]:
    """関節角（度）から、3D モデルの肘と、指のあいだの位置を出す（順運動学）。

    reachy3d.js と同じ回し方: 肩は pitch → roll、肘は yaw → pitch の順。
    """
    p, r, ey, ep = (math.radians(a) for a in joints[:4])
    rs = _mul(_rx(p), _rz(r))
    elbow = _add(_shoulder(side), _apply(rs, (0, -_UPPER, 0)))
    re = _mul(_ry(ey), _rx(ep))
    return elbow, _add(elbow, _apply(_mul(rs, re), (0, -_FORE, 0)))


def hand_position(side: str, joints: list[float]) -> tuple[float, float, float]:
    return arm_points(side, joints)[1]


def _within(side: str, q: tuple[float, float, float, float]) -> bool:
    p, r, ey, ep = q
    lo, hi = _ROLL[side]
    return (_PITCH[0] <= p <= _PITCH[1] and lo <= r <= hi
            and _ELBOW_YAW[0] <= ey <= _ELBOW_YAW[1]
            and _ELBOW_PITCH[0] <= ep <= _ELBOW_PITCH[1])


def solve(side: str, target, forearm_dir) -> list[float] | None:
    """指のあいだを target に持っていく関節角（度、7 関節）を返す。

    この腕は位置を決めるのに 4 関節あり、1 つ余る（肘の位置を、肩と手先を結ぶ
    軸のまわりに回せる）。そこで肘の位置を一周ぶん試し、可動域に収まるものの中から
    人が手を伸ばすときに近い形を選ぶ。前腕が forearm_dir に近いこと、
    肘を肩より上げないこと、腕を後ろへ回さないことを条件にしている。
    届かない・可動域に収まらないときは None。
    """
    s = _shoulder(side)
    to = _sub(target, s)
    dist = _norm(to)
    if not abs(_UPPER - _FORE) < dist < _UPPER + _FORE:
        return None
    n = _scale(to, 1 / dist)
    # 肘が乗る円: 肩を中心とする球と、手先を中心とする球の交わり
    a = (_UPPER ** 2 - _FORE ** 2 + dist ** 2) / (2 * dist)
    radius = math.sqrt(max(0.0, _UPPER ** 2 - a ** 2))
    center = _add(s, _scale(n, a))
    helper = (0.0, 1.0, 0.0) if abs(n[1]) < 0.9 else (1.0, 0.0, 0.0)
    u = _unit(_cross(n, helper))
    v = _cross(n, u)
    want = _unit(forearm_dir)

    best, best_cost = None, math.inf
    for i in range(360):
        phi = math.radians(i)
        elbow = _add(center, _add(_scale(u, radius * math.cos(phi)), _scale(v, radius * math.sin(phi))))
        w = _scale(_sub(elbow, s), 1 / _UPPER)
        roll = math.asin(max(-1.0, min(1.0, w[0])))
        pitch = math.atan2(-w[2], -w[1])
        fore = _scale(_sub(target, elbow), 1 / _FORE)
        # 前腕の向きを上腕の座標に戻して、肘の yaw / pitch を読む
        rs = _mul(_rx(pitch), _rz(roll))
        local = _apply([list(col) for col in zip(*rs)], fore)
        ep = -math.acos(max(-1.0, min(1.0, -local[1])))
        ey = math.atan2(local[0], local[2]) if abs(math.sin(ep)) > 1e-6 else 0.0
        q = tuple(math.degrees(x) for x in (pitch, roll, ey, ep))
        if not _within(side, q):
            continue
        cost = (math.acos(max(-1.0, min(1.0, _dot(fore, want))))
                + 0.4 * (q[2] / 90) ** 2                    # 肘をひねりすぎない
                + 3.0 * max(0.0, elbow[1] - (s[1] - 0.1))   # 肘を肩より上げない
                + 1.5 * max(0.0, q[0]) / 90                 # 腕を後ろへ回さない
                + 0.3 * (q[1] / 90) ** 2)                   # 肩を横に開きすぎない
        if cost < best_cost:
            best, best_cost = q, cost
    if best is None:
        return None
    return [round(x, 1) for x in best] + [0.0, 0.0, 0.0]


def look_at(target) -> tuple[float, float, float]:
    """首をその位置へ向ける角度 (roll, pitch, yaw)。見下ろしすぎないよう制限する。"""
    head = (0.0, 1.02, 0.0)
    d = _sub(target, head)
    yaw = math.degrees(math.atan2(d[0], d[2]))
    pitch = math.degrees(math.atan2(-d[1], math.hypot(d[0], d[2])))
    return (0.0, round(max(-20.0, min(28.0, pitch)), 1), round(max(-45.0, min(45.0, yaw)), 1))


# ------------------------------------------------------------ 姿勢の一覧

def _reach_dir(side: str, target) -> tuple[float, float, float]:
    """物へ手を伸ばすときの前腕の向き。肩から物のほうへ向けて、斜め下に下ろす。"""
    s = _shoulder(side)
    hx, hz = target[0] - s[0], target[2] - s[2]
    h = math.hypot(hx, hz)
    return (hx / h, -1.0, hz / h)


def _must(side: str, target, forearm_dir) -> list[float]:
    q = solve(side, target, forearm_dir)
    if q is None:
        raise ValueError(f"{side} の手が {target} に届きません。置き場所を見直してください")
    return q


def _reach(side: str, target) -> list[float]:
    return _must(side, target, _reach_dir(side, target))


def _points(target) -> dict[str, list[float]]:
    """target を指さす姿勢を、届く腕の分だけ返す。

    腕をほぼまっすぐ伸ばして、手先を target へ向ける。反対側の物は
    胴の前を横切ることになり、可動域に収まらないことが多い。
    """
    found = {}
    for side in ("r", "l"):
        s = _shoulder(side)
        to = _sub(target, s)
        direction = _unit(to)
        # 物の手前で止める。伸ばしきると、近い物では手先が天板に刺さる。
        reach = min(1.05, _norm(to) - 0.3)
        q = solve(side, _add(s, _scale(direction, reach)), direction)
        if q is not None:
            found[side] = q
    if not found:
        raise ValueError(f"{target} を指させる腕がありません。置き場所を見直してください")
    return found


def _arm_for(x: float) -> str:
    return "r" if x < 0 else "l"


def build() -> dict:
    """ゲームで使う姿勢をまとめて計算する。サーバ起動時に 1 回だけ呼ぶ。"""
    poses: dict = {"slots": {}, "bins": {}, "show": {}, "ready": {}, "tuck": {}}

    for name, (x, z) in SLOTS.items():
        side = _arm_for(x)
        at = (x, _OBJ_Y, z)
        poses["slots"][name] = {
            "pos": at,
            "arm": side,
            "at": _reach(side, at),
            "above": _reach(side, (x, _OBJ_Y + 0.3, z)),
            "look": look_at(at),
            "point": _points(at),
        }

    for name, (x, z) in BINS.items():
        top = (x, _BIN_TOP, z)
        poses["bins"][name] = {
            "pos": (x, TABLE["y"], z),
            "arm": _arm_for(x),
            "look": look_at(top),
            # 入れ物は両手から使う
            "in": {s: _reach(s, (x, _BIN_TOP + 0.02, z)) for s in ("r", "l")},
            "above": {s: _reach(s, (x, _BIN_TOP + 0.28, z)) for s in ("r", "l")},
            "point": _points(top),
        }

    for side, sign in (("r", -1), ("l", 1)):
        # 子供に見せる: 胸の前で、カメラのほうへ差し出す
        poses["show"][side] = _must(side, (sign * 0.3, 0.72, 0.85), (0.0, 0.35, 1.0))
        # ゲーム中の待ちの姿勢。肘を曲げて、手をテーブルの手前の上に出しておく。
        poses["ready"][side] = _must(side, (sign * 0.42, TABLE["y"] + 0.3, 0.45), (0.0, -0.3, 1.0))
        # 下ろした腕から待ちの姿勢へ移るときの中継。腕を下ろしたまま前腕を
        # 振り上げると、手が天板の下をくぐる。先に肘を曲げきって手を胸の前へ
        # 引き寄せてから、前へ出す。
        roll = REST[side][1]
        poses["tuck"][side] = [45.0, roll, 0.0, -125.0, 0.0, 0.0, 0.0]
    return poses
