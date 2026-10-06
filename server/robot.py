"""Reachy 2 (シミュレータ) のジェスチャー制御。

会話の内容に合わせて Reachy に体を動かしてもらうための薄いラッパー。
Sim が起動していない場合でも会話だけは成立するように、接続失敗時は
「動かないけどエラーも出さない」ダミー動作にフォールバックする。

ジェスチャーは (種類, 引数) のステップ列として定義し、専用スレッドで再生する。
新しいジェスチャーが来たら再生中のものは途中で打ち切る。会話のテンポに
ロボットが遅れて、ひとつ前の感情のまま固まるのを防ぐため。

Rosetta 上の Sim では goto 1回あたり 0.5〜1 秒のオーバーヘッドが乗るので、
各ジェスチャーはステップ数を絞って組み立てている。
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

# 会話ブレインが返してよい動きの名前。UI とプロンプトの両方から参照する。
MOTIONS: dict[str, str] = {
    "nod": "うなずく",
    "shake": "いやいやする",
    "tilt": "首をかしげる",
    "happy": "よろこぶ",
    "surprised": "びっくりする",
    "think": "かんがえる",
    "wave": "手をふる",
    "bow": "おじぎする",
    "idle": "なにもしない",
}

# Reachy 2 の腕は 7 関節 (肩pitch, 肩roll, 肘yaw, 肘pitch, 手首roll, 手首pitch, 手首yaw)。
_ARM_REST = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_ARM_WAVE_A = [-10.0, -40.0, -20.0, -95.0, 0.0, 0.0, 0.0]
_ARM_WAVE_B = [-10.0, -15.0, -20.0, -95.0, 0.0, 0.0, 0.0]

# まっすぐ前を向いた状態 (roll, pitch, yaw)。Reachy の default 姿勢に合わせている。
_HEAD_NEUTRAL = (0.0, -10.0, 0.0)

# ステップの書き方:
#   ("head", roll, pitch, yaw, duration)  首を「その角度そのもの」へ動かす（絶対角）
#   ("look", roll, pitch, yaw, duration)  首を動かし始めて、終わりを待たずに次へ進む
#   ("ant", left, right, duration)        アンテナを動かす（絶対角）
#   ("arm", [7関節の角度], duration)       右腕を動かす（絶対角）
#   ("larm", [7関節の角度], duration)      左腕を動かす（絶対角）
#   ("arms", [右7関節], [左7関節], duration) 両腕を同時に動かす（絶対角）
#   ("grip", "r" か "l", 開き%, duration)   グリッパーを開閉する（0 で閉じる、100 で全開）
#   ("wait", seconds)                     ポーズ
#
# 首は必ず絶対角で指定すること。相対回転 (rotate_by) で組むと、割り込みで
# 途中終了したときに「元へ戻す」ステップが実行されず、ズレが毎回積み重なって
# 首が傾いたままになる。絶対角なら、どこで中断されても次のジェスチャーが
# 正しい姿勢へ引き戻せる。
#
# 同じ理由で、どのジェスチャーも最後は _HEAD_NEUTRAL とアンテナ 0 に戻す。
_GESTURES: dict[str, list[tuple]] = {
    "nod": [
        ("head", 0, 10, 0, 0.35),
        ("head", 0, -14, 0, 0.35),
        ("head", 0, 6, 0, 0.3),
        ("head", *_HEAD_NEUTRAL, 0.3),
    ],
    "shake": [
        ("head", 0, -10, 25, 0.35),
        ("head", 0, -10, -25, 0.5),
        ("head", 0, -10, 25, 0.5),
        ("head", *_HEAD_NEUTRAL, 0.35),
    ],
    "tilt": [
        ("head", 25, -10, 0, 0.6),
        ("wait", 0.7),
        ("head", *_HEAD_NEUTRAL, 0.6),
    ],
    "happy": [
        ("ant", 45, -45, 0.25),
        ("head", 0, -22, 0, 0.35),
        ("ant", -25, 25, 0.25),
        ("ant", 45, -45, 0.25),
        ("head", *_HEAD_NEUTRAL, 0.35),
        ("ant", 0, 0, 0.3),
    ],
    "surprised": [
        ("ant", 70, -70, 0.2),
        ("head", 0, -32, 0, 0.25),
        ("wait", 0.7),
        ("head", *_HEAD_NEUTRAL, 0.6),
        ("ant", 0, 0, 0.3),
    ],
    # 返事を待っている間の「間つなぎ」。ふつうは返事の動きに割り込まれて
    # 途中で終わるが、そのまま最後まで再生されても首は戻る。
    "think": [
        ("head", 18, -25, 0, 0.6),
        ("ant", 25, 25, 0.3),
        ("wait", 1.2),
        ("head", *_HEAD_NEUTRAL, 0.6),
        ("ant", 0, 0, 0.3),
    ],
    # 腕だけの動きだが、think のあとに来ても首が傾いたままにならないよう
    # はじめに首を戻しておく。
    "wave": [
        ("head", *_HEAD_NEUTRAL, 0.4),
        ("ant", 0, 0, 0.2),
        ("arm", _ARM_WAVE_A, 1.0),
        ("arm", _ARM_WAVE_B, 0.4),
        ("arm", _ARM_WAVE_A, 0.4),
        ("arm", _ARM_REST, 1.0),
    ],
    "bow": [
        ("head", 0, 32, 0, 0.7),
        ("wait", 0.4),
        ("head", *_HEAD_NEUTRAL, 0.7),
    ],
    # 「なにもしない」は、いまの姿勢のまま固まるのではなく、力を抜いて
    # まっすぐ前を向く動き。これがないと think の姿勢のまま残ることがある。
    "idle": [
        ("head", *_HEAD_NEUTRAL, 0.6),
        ("ant", 0, 0, 0.3),
    ],
}


def _ensure_on(reachy) -> None:
    """動かす前に、必要な部位のトルクが入っているか確かめて入れ直す。

    Sim のトルク状態は、この Mac 上のどのクライアントからでも変えられる共有の状態。
    別のスクリプトが turn_off したあとや、コンテナを作り直したあとは腕だけ落ちて
    いることがあり、その状態で goto を出しても SDK が
    「r_arm is off. Goto not sent.」と黙って捨ててしまう
    （首は生きているので「一部だけ動かない」という分かりにくい壊れ方になる）。
    毎回ここで確認しておけば、その状態から自力で復帰できる。
    """
    parts = {"r_arm": reachy.r_arm, "l_arm": reachy.l_arm, "head": reachy.head}
    off = [name for name, part in parts.items() if part is not None and not part.is_on()]
    if not off:
        return
    logger.info("トルクが落ちていたので入れ直します: %s", ", ".join(off))
    reachy.turn_on()


class ReachyRobot:
    """Reachy 2 SDK への接続とジェスチャー再生を受け持つ。"""

    def __init__(self, host: str = "localhost") -> None:
        self._host = host
        self._reachy = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        # 会話とゲームの両方から同時に呼ばれても、打ち切りと開始が混ざらないようにする
        self._start_lock = threading.Lock()

    # ------------------------------------------------------------------ 接続

    @property
    def connected(self) -> bool:
        r = self._reachy
        return bool(r is not None and r.is_connected())

    def connect(self) -> bool:
        """Sim に接続して腕・首をオンにする。成功したら True。"""
        with self._lock:
            if self.connected:
                return True
            try:
                from reachy2_sdk import ReachySDK

                # ReachySDK.__new__ は host しか受け取らないため、gRPC ポートは
                # SDK の既定値 (50051) 固定。compose.yaml もそこに合わせている。
                reachy = ReachySDK(host=self._host)
                if not reachy.is_connected():
                    logger.warning("Reachy sim に接続できませんでした (%s)", self._host)
                    return False
                reachy.turn_on()
                self._reachy = reachy
                logger.info("Reachy sim に接続しました (%s)", self._host)
            except Exception as exc:  # 接続失敗は致命的でない（会話だけは続ける）
                logger.warning("Reachy sim への接続に失敗: %s", exc)
                self._reachy = None
                return False

        self._reset_pose()
        return True

    def disconnect(self) -> None:
        self._cancel_current()
        with self._lock:
            reachy = self._reachy
            self._reachy = None
        if reachy is None:
            return
        try:
            reachy.turn_off_smoothly()
            reachy.disconnect()
        except Exception as exc:
            logger.debug("切断時のエラーは無視します: %s", exc)

    # ------------------------------------------------------------ ジェスチャー

    def play(self, motion: str) -> bool:
        """ジェスチャーをバックグラウンドで再生する。

        再生中のジェスチャーがあれば打ち切って、新しいほうに乗り換える。
        再生を始められたら True、未接続や未知の動きなら False。
        """
        steps = _GESTURES.get(motion)
        if steps is None:
            return False
        return self._start(motion, steps) is not None

    def run(self, steps: list[tuple], name: str = "sequence") -> bool:
        """ステップ列を再生し、終わるまで待つ。最後まで再生できたら True。

        ゲームで「つかんでから持ち上げる」のように、動きの区切りごとに
        画面へ知らせたいときに使う。play() と同じく、再生中のものがあれば
        打ち切ってから始める。未接続のときや、途中でほかの動きに
        割り込まれたときは False。
        """
        started = self._start(name, steps)
        if started is None:
            return False
        worker, stop, result = started
        worker.join()
        return result["ok"] and not stop.is_set()

    def _start(self, name: str, steps: list[tuple]):
        reachy = self._reachy
        if reachy is None or not self.connected:
            return None

        with self._start_lock:
            self._cancel_current()
            stop = threading.Event()
            result = {"ok": False}

            def run() -> None:
                logger.debug("ジェスチャー %s を開始 (%d ステップ)", name, len(steps))
                try:
                    _ensure_on(reachy)
                    self._run_steps(reachy, steps, stop)
                    result["ok"] = True
                    logger.debug("ジェスチャー %s を完了", name)
                except Exception as exc:
                    logger.warning("ジェスチャー %s の再生に失敗: %s", name, exc)

            self._stop = stop
            self._worker = threading.Thread(target=run, name=f"gesture-{name}", daemon=True)
            self._worker.start()
            return self._worker, stop, result

    def _cancel_current(self) -> None:
        """再生中のジェスチャーを止め、進行中の goto も取り消す。"""
        worker = self._worker
        if worker is None or not worker.is_alive():
            return
        self._stop.set()
        reachy = self._reachy
        if reachy is not None:
            try:
                reachy.cancel_all_goto()
            except Exception as exc:
                logger.debug("goto の取り消しに失敗: %s", exc)
        worker.join(timeout=2.0)

    @staticmethod
    def _run_steps(reachy, steps: list[tuple], stop: threading.Event) -> None:
        for step in steps:
            if stop.is_set():
                logger.debug("ステップ %s の手前で打ち切り", step[0])
                return
            logger.debug("ステップ %s", step)
            kind = step[0]
            if kind == "head":
                _, roll, pitch, yaw, duration = step
                reachy.head.goto([roll, pitch, yaw], duration=duration, wait=True)
            elif kind == "ant":
                _, left, right, duration = step
                head = reachy.head
                if head.l_antenna is not None:
                    head.l_antenna.goto(left, duration=duration, wait=False)
                if head.r_antenna is not None:
                    head.r_antenna.goto(right, duration=duration, wait=True)
                else:
                    time.sleep(duration)
            elif kind == "look":
                _, roll, pitch, yaw, duration = step
                reachy.head.goto([roll, pitch, yaw], duration=duration, wait=False)
            elif kind == "arm":
                _, positions, duration = step
                reachy.r_arm.goto(positions, duration=duration, wait=True)
            elif kind == "larm":
                _, positions, duration = step
                reachy.l_arm.goto(positions, duration=duration, wait=True)
            elif kind == "arms":
                _, right, left, duration = step
                reachy.r_arm.goto(right, duration=duration, wait=False)
                reachy.l_arm.goto(left, duration=duration, wait=True)
            elif kind == "grip":
                _, side, opening, duration = step
                arm = reachy.r_arm if side == "r" else reachy.l_arm
                if arm.gripper is not None:
                    arm.gripper.goto(float(opening), duration=duration, wait=True, percentage=True)
            elif kind == "wait":
                stop.wait(step[1])

    def arm_joints(self, side: str) -> list[float] | None:
        """腕の関節角（度、肩pitch・肩roll・肘yaw・肘pitch の 4 つ）。未接続なら None。"""
        reachy = self._reachy
        if reachy is None or not self.connected:
            return None
        try:
            arm = reachy.r_arm if side == "r" else reachy.l_arm
            return [arm.shoulder.pitch.present_position, arm.shoulder.roll.present_position,
                    arm.elbow.yaw.present_position, arm.elbow.pitch.present_position]
        except Exception as exc:
            logger.debug("腕の関節角の読み取りに失敗: %s", exc)
            return None

    def pose(self) -> dict[str, float] | None:
        """いまの関節角（度）をまとめて返す。ブラウザの 3D 表示用。

        present_position は SDK がバックグラウンドで受け取った状態を返すだけなので
        （実測 0.08ms）、30Hz で呼んでも負荷にならない。
        """
        reachy = self._reachy
        if reachy is None or not self.connected:
            return None
        try:
            head, neck = reachy.head, reachy.head.neck
            r, left = reachy.r_arm, reachy.l_arm
            return {
                "neck_roll": neck.roll.present_position,
                "neck_pitch": neck.pitch.present_position,
                "neck_yaw": neck.yaw.present_position,
                "l_antenna": head.l_antenna.present_position if head.l_antenna else 0.0,
                "r_antenna": head.r_antenna.present_position if head.r_antenna else 0.0,
                "r_shoulder_pitch": r.shoulder.pitch.present_position,
                "r_shoulder_roll": r.shoulder.roll.present_position,
                "r_elbow_pitch": r.elbow.pitch.present_position,
                "r_elbow_yaw": r.elbow.yaw.present_position,
                "l_shoulder_pitch": left.shoulder.pitch.present_position,
                "l_shoulder_roll": left.shoulder.roll.present_position,
                "l_elbow_pitch": left.elbow.pitch.present_position,
                "l_elbow_yaw": left.elbow.yaw.present_position,
                # グリッパーの開き具合（0〜100%）。ゲームで物をつかむ様子を見せる
                "r_gripper": r.gripper.opening if r.gripper else 0.0,
                "l_gripper": left.gripper.opening if left.gripper else 0.0,
            }
        except Exception as exc:
            logger.debug("関節角の読み取りに失敗: %s", exc)
            return None

    def _reset_pose(self) -> None:
        reachy = self._reachy
        if reachy is None:
            return
        try:
            reachy.goto_posture("default", duration=1.5, wait=True)
        except Exception as exc:
            logger.debug("初期姿勢への移動をスキップ: %s", exc)
