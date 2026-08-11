---
title: Reachy 2 のシミュレータで、子供が話しかけられるロボットを作った
tags: Python,Docker,ロボット,LLM,three.js
private: false
updated_at: ''
id: null
organization_url_name: null
slide: false
ignorePublish: false
---

# はじめに

Pollen Robotics の人型ロボット **Reachy 2** には、公式のシミュレータが
Docker イメージで配布されています。実機がなくても、ロボットを動かす
プログラムを書いて試せます。

これを使って、**子供が声や文字で話しかけると返事をするロボット**を作りました。
返事の内容に合わせて、Reachy がうなずいたり手をふったりします。

![デモ](https://raw.githubusercontent.com/togakyo/sim-reachy/main/docs/demo.gif)

会話も音声認識も読み上げも、すべて手元のマシンの中で完結させています。
子供の声や話した内容が外部に出ていかないようにしたかったためです。

コードは GitHub に置いています → [togakyo/sim-reachy](https://github.com/togakyo/sim-reachy)

## この記事で書くこと

1. シミュレータを動かすまでの手順
2. 子供向けに工夫したこと
3. つまずいた点

Docker と Python の基本的な使い方は説明しません。
ROS 2 の知識は不要です。コンテナの中に入っているので、意識せずに使えます。

## 動作を確認した環境

| | |
| --- | --- |
| マシン | MacBook（Apple M5 / メモリ 24GB） |
| OS | macOS 15 |
| Docker Desktop | 29.5.3 |
| Python | 3.12 |
| イメージ | `pollenrobotics/reachy2:1.7.5.9` |
| reachy2-sdk | 1.0.15 |
| LLM | Ollama + `gemma3:4b` |

Apple Silicon 特有の準備がひとつだけあるので、そこは節を分けて書きます。
読み上げに macOS の `say` コマンドを使っている点を除けば、OS には依存しません。

## 全体の構成

```
ブラウザ ──WebSocket──> 会話サーバ ──> Ollama（ローカル LLM）
 こえ/もじ  ↑                 │
   3D表示 ──┘ 関節角 30Hz     └────────> Reachy 2 シミュレータ
```

---

# シミュレータを動かす

## 構成とポート

イメージの中には、ROS 2 の制御まわり一式と、外から操作するための
gRPC サーバが入っています。使うポートは次の 3 つです。

| ポート | 用途 |
| --- | --- |
| 50051 | Python SDK の接続先（gRPC） |
| 6080 | ブラウザから 3D 表示（RViz）を見る |
| 8888 | Jupyter Notebook |

ふだん使うのは 50051 です。

## 起動する

```yaml
services:
  reachy2:
    image: pollenrobotics/reachy2:1.7.5.9
    container_name: reachy2-sim
    ports:
      - "6080:6080"
      - "50051:50051"
    command: >
      start_rviz:=true
      start_sdk_server:=true
      fake:=true
      orbbec:=false
    shm_size: "2gb"
```

`fake:=true` は、実機の代わりに仮想のロボットを動かす指定です。
`orbbec:=false` はカメラを使わない指定で、付けておくと起動が軽くなります。

イメージは数 GB あるので、初回のダウンロードには時間がかかります。
ログに次の行が出れば、準備ができています。

```
[reachy_grpc_joint_sdk_server-12] Server started on port 50051.
```

3D 表示はブラウザで見られます。

```
http://localhost:6080/vnc.html?autoconnect=1&resize=remote
```

## Apple Silicon の場合

配布されているイメージのタグを調べると、**amd64 版しかありません**。

```bash
curl -s "https://hub.docker.com/v2/repositories/pollenrobotics/reachy2/tags" |
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  [print(t['name'], [i['architecture'] for i in t['images']]) for t in d['results']]"
```

```
latest ['amd64']
1.7.5.9 ['amd64']
```

そのため Rosetta による変換が必要です。Docker Desktop の設定を開いて、
次の項目にチェックを入れます。

> Settings → General → **Use Rosetta for x86/amd64 emulation on Apple Silicon**

`x86_64` と表示されれば有効になっています。

```bash
docker run --rm --platform linux/amd64 busybox uname -m
```

`compose.yaml` には `platform: linux/amd64` を足します。

## Python からつないで動かす

`reachy2-sdk` は Python 3.10 以上が必要です。
macOS に最初から入っている Python は 3.9 なので、そのままでは動きません。

```bash
python3.12 -m venv .venv
.venv/bin/pip install reachy2-sdk
```

あとは接続して、うなずかせるだけです。

```python
from reachy2_sdk import ReachySDK

reachy = ReachySDK(host="localhost")
reachy.turn_on()                     # 関節の電源を入れる

# 首を下げて、戻す（roll, pitch, yaw の順、単位は度）
reachy.head.goto([0, 10, 0], duration=0.4, wait=True)
reachy.head.goto([0, -10, 0], duration=0.4, wait=True)

reachy.turn_off_smoothly()
reachy.disconnect()
```

腕を動かすときは、7 つの関節の角度をまとめて渡します。
順番は肩 pitch、肩 roll、肘 yaw、肘 pitch、手首 roll、手首 pitch、手首 yaw です。

```python
reachy.r_arm.goto([-10, -40, -20, -95, 0, 0, 0], duration=1.0, wait=True)
```

ここまでで、ロボットを動かす準備は整いました。

---

# 子供向けに工夫したこと

ここからが本題です。**大人向けのチャットをそのまま子供に見せても使えません。**
実際に触ってもらうことを考えて直した点を書きます。

## 返事は短く、やさしいことばで

システムプロンプトで、話しかたを細かく指定しました。

```
- 1〜2文の短い返事にします。長い説明はしません。
- むずかしい漢字や専門用語はつかわず、やさしいことばで話します。
- 明るく元気に答えて、さいごに短い質問を1つ返して会話をつづけます。
- 絵文字や顔文字はつかいません。声に出して読まれるからです。
```

最後の一行は、読み上げに回すと絵文字が読まれてしまうためです。

知識を問われたときの調整も入れました。最初は
「わからないなあ」ばかり返してくる状態だったので、
**身近なものとくらべて教える**よう例を書いたところ、答えるようになりました。

```
- 「これなに?」と聞かれたら、知っていることをやさしく教えます。
  たとえば「くじらはバスより大きくて、20メートルくらいあるんだよ」のように、
  身近なものとくらべて教えると分かりやすいです。
```

実際の返事はこうなりました。

```
👦 きょう かけっこで 1ばんに なったよ
🤖 わー！すごいね！とってもかっこいいね！

👦 くじらって どのくらい おおきいの？
🤖 くじらはバスより大きくて、20メートルくらいあるんだよ。
```

## 返事と一緒に「動き」を選ばせる

ロボットなので、話すだけでなく動いてほしいところです。
返事のテキストと**それに合う動き**を、LLM に同時に出させています。

Ollama の structured output（JSON スキーマの指定）を使うと、
4B のモデルでも形式が安定しました。

```python
_SCHEMA = {
    "type": "object",
    "properties": {
        "reply":  {"type": "string"},
        "motion": {"type": "string",
                   "enum": ["nod", "shake", "tilt", "happy", "surprised",
                            "think", "wave", "bow", "idle"]},
    },
    "required": ["reply", "motion"],
}
```

喜ぶ話には `happy`、あいさつには `wave`、断るときは `shake` と、
思ったより素直に選んでくれます。

## 待たせない

子供は待ってくれません。返事までの数秒をどう埋めるかを考えました。

**考えている間に動かす**

返事を作っている間、`think`（首をかしげてアンテナを動かす）を再生します。
無言で固まっている時間をなくすためです。

```python
# 考えている間も体は動かす。子供を無言で待たせないための間つなぎ。
await asyncio.to_thread(robot.play, "think")
await ws.send_json({"type": "thinking"})

reply, motion = await brain.respond(text)
await asyncio.to_thread(robot.play, motion)
```

**モデルを先に読み込んでおく**

Ollama はモデルの初回読み込みに 10 秒以上かかります。
最初に話しかけた子だけが待たされるのを避けるため、
サーバの起動時に空打ちして温めておきます。

```python
async def warmup(self) -> None:
    await self._client.post(f"{self.url}/api/chat", json={
        "model": self.model,
        "messages": [{"role": "user", "content": "こんにちは"}],
        "options": {"num_predict": 1},
        "keep_alive": "30m",     # 会話が途切れても抜けないようにする
    })
```

これで初回も 3.4 秒になりました。以降は中央値 4.2 秒です。

**あいさつを先に出す**

起動直後に開くと、状態の取得を待つ間、画面が空のままでした。
あいさつはサーバの応答を待たずに先に表示するよう直しました。

## 声で話せるようにする

キーボードを打てない年齢でも使えるように、音声入力を付けました。

ブラウザの Web Speech API を使うと簡単なのですが、
**音声が外部に送られます**。ローカル LLM を選んだ意図と合わないので、
既定は faster-whisper（手元で動く音声認識）にしました。

読み上げは macOS の `say` コマンドで十分でした。
日本語の音声が最初から入っています。

```bash
say -v Kyoko -r 170 -o out.wav --data-format=LEI16@22050 "こんにちは"
```

読み上げ速度を少し落としているのは、聞き取りやすくするためです。

停止ボタンの押し忘れにも対応しました。
**1.6 秒だまったら自動で送信**します。

```js
if (peak > 8) { spoke = true; quietSince = null; }
else if (spoke) {
  quietSince = quietSince ?? performance.now();
  if (performance.now() - quietSince > 1600) { stopRecording(); return; }
}
```

## 安全のしくみ

一番慎重に作ったところです。

最初はプロンプトに「こわい話はしない」と書くだけでした。
しかし試しに乱暴なことを聞くと、**内容がぶれて、望ましくない返事**が出ました。
4B のモデルに判断を任せるのは無理があります。

かといって、キーワードで機械的に弾くのも違います。
たとえば友達とけんかしたことを話してくれたとき、
決まった文で打ち切ったら、話を聞いてほしい子を突き放すことになります。

そこでコード側で 3 つに分けました。

| 子供の発話 | 返しかた |
| --- | --- |
| 人を傷つけるやり方を求めるもの | LLM を通さず、決まった文で断り、身近な大人に相談するようすすめる |
| つらい気持ちが強く出ているもの | LLM を通さず、決まった文で受けとめ、いますぐ身近な大人に話すようすすめる |
| 出来事や気持ちを話しているだけのもの | 遮らず、注意書きを添えて LLM に渡し、共感して返す |

```python
def _screen(user_text):
    """決まった文で返すべきときだけ返し、LLM に任せてよいときは None を返す。"""
    if _DISTRESS.search(user_text):
        return _REPLY_DISTRESS, "tilt"
    if _HARM_TOPIC.search(user_text) and _ASKING_HOW.search(user_text):
        return _REPLY_HARM, "shake"
    return None
```

上の 2 つは **LLM の判断を通しません**。生成に任せると内容が安定しないので、
あらかじめ用意した文をそのまま返します。

3 番目を遮らないことが要点です。話題に触れているだけの場合は、
システムプロンプトに注意書きを足したうえで、共感を優先させています。

なお、これで万全というつもりはありません。
**子供が使うときは大人が付き添う前提**の作りです。

## 画面

見た目も子供向けに寄せました。

- ボタンを大きく、丸く
- ひらがな中心の表示（「おくる」「さいしょから」「こえ あり」）
- 「🎤 こえ」と「⌨️ もじ」をタブで切り替え
- 返事の下に動きの名前を出す（「（手をふる）」）

冒頭のデモが実際の画面です。

---

# つまずいた点

## 3D 表示が思ったより出ない

`start_rviz:=true` にすると、コンテナの中の RViz を noVNC 経由で見られます。
きちんと動きますが、ゆっくりです。実際の様子がこちらです。

![RVizの表示](https://raw.githubusercontent.com/togakyo/sim-reachy/main/docs/rviz.gif)

Docker はコンテナに GPU を渡せないため、描画が CPU だけで行われます。
実測すると、CPU を 1.5 コアほど使って **約 10fps** でした
（RViz 自身の表示にも 9〜11fps と出ます）。
ほかの処理と CPU を取り合うと 2〜3fps まで落ちます。

動きを確認するだけなら十分ですが、子供に見せる画面としては物足りません。
そこで**描画をホスト側でやる**ことにしました。
シミュレータからは関節の角度だけをもらって、ブラウザの three.js で描きます。

関節の角度を読むコストを測ると、十分に軽いものでした。

```
0.08 ミリ秒/回 → 30Hz で読み続けても CPU の 0.2% 程度
```

WebSocket で流します。

```python
@app.websocket("/ws/pose")
async def pose_stream(ws: WebSocket):
    await ws.accept()
    while True:
        await ws.send_json(robot.pose() or {})
        await asyncio.sleep(1 / 30)
```

ブラウザ側は、届いた角度をモデルに割り当てるだけです。

```js
head.rotation.set(pose.neck_pitch * D2R, pose.neck_yaw * D2R, pose.neck_roll * D2R);
```

これで 60fps になりました。かたちは子供向けに簡略化していますが、
**動きはシミュレータの実際の関節角そのもの**です。
上の RViz の GIF と、冒頭のデモを見くらべると違いが分かると思います。

会話画面の中にロボットがいるほうが、子供にも分かりやすくなりました。

## CPU を 10 コアぶん使い切る

シミュレータを起動しておくとマシンが重くなりました。

```bash
docker stats --no-stream
```

```
reachy2-sim  CPU=1072.01%
```

何も操作していなくても、ROS 2 の制御ループが動き続けます。
同じマシンで LLM も動かすので、上限を決めました。

```yaml
services:
  reachy2:
    cpus: "4.0"
```

会話の応答が 13 秒から 4 秒に改善しました。

## 腕への指示が黙って捨てられる

**首は動くのに腕だけ動かない**状態になりました。
やっかいなことに、エラーが出ません。関数は正常に返ってきます。

各ステップの時間をログに出したところ、様子がおかしいと分かりました。

```
12:47:47,591 ステップ ('arm', [-10.0, -40.0, ...], 1.0)
12:47:47,592 ステップ ('arm', [-10.0, -15.0, ...], 0.4)
12:47:47,592 完了
```

1 秒かかるはずの動作が、2 ミリ秒で終わっています。
SDK のログを詳しく出すと、理由が書いてありました。

```
WARNING reachy2_sdk.parts.part: r_arm is off. Goto not sent.
```

腕の電源が入っていませんでした。
SDK は警告を出すだけで指示を捨てるので、呼んだ側からは分かりません。

この電源の状態は、シミュレータにつないだどのプログラムからでも変えられます。
接続時に一度入れるだけでは足りないので、動かす前に毎回確かめるようにしました。

```python
def ensure_on(reachy):
    parts = {"r_arm": reachy.r_arm, "l_arm": reachy.l_arm, "head": reachy.head}
    off = [name for name, p in parts.items() if not p.is_on()]
    if off:
        reachy.turn_on()
```

## 会話のたびに首が傾いていく

しばらく使っていると、首が傾いたまま戻らなくなりました。
角度を読むと、本来 0 のはずの `roll` が 34.5 度になっていました。

原因は、動きを**相対回転**で書いていたことです。

会話のテンポに遅れないよう、新しい動きが来たら再生中のものを打ち切る
作りにしていました。すると「元に戻す」ステップが実行されないまま次に進み、
そのズレが会話のたびに積み重なっていました。

絶対角に統一して解決しました。

```python
_HEAD_NEUTRAL = (0.0, -10.0, 0.0)   # まっすぐ前を向いた状態

"tilt": [
    ("head", 25, -10, 0, 0.6),
    ("wait", 0.7),
    ("head", *_HEAD_NEUTRAL, 0.6),   # 必ず中立に戻す
],
```

絶対角なら、どこで中断されても次の動きが正しい姿勢へ引き戻せます。
割り込みを 20 回繰り返してもズレは出なくなりました。

## SDK にポート番号を渡すとエラーになる

接続先のポートを明示しようとすると、失敗します。

```python
ReachySDK(host="localhost", sdk_port=50051)
```

```
TypeError: ReachySDK.__new__() got an unexpected keyword argument 'sdk_port'
```

`__init__` は `sdk_port` を受け取るのに、ホストごとに 1 つだけ
インスタンスを作る `__new__` が受け取らない作りでした。
Python は両方に同じ引数を渡すので、書き方を変えても避けられません。
既定の 50051 のまま使っています（バージョン 1.0.15 での話です）。

---

# できあがりと速さ

確認した環境で、ほかに重い処理を動かしていないときの値です。

| | |
| --- | --- |
| 返事 | 中央値 4.2 秒 |
| 音声認識 | 短い一文で 2〜8 秒 |
| ジェスチャー | 1.8〜5.3 秒 |
| 3D 表示 | 60fps |

返事の速さは空いている CPU の量に左右されます。
ほかに重い処理を動かしていると、20 秒以上かかることもありました。

# おわりに

実機の Reachy 2 は気軽に買える価格ではありませんが、
シミュレータは Docker で動くので、ロボットを動かすプログラムを
書いてみるにはちょうど良い題材でした。

作ってみて一番時間を使ったのは、ロボットを動かす部分よりも、
**子供が使えるようにする部分**でした。
待たせない工夫や、話を遮らない線引きは、動くようになってから
何度も直しています。

コードは MIT ライセンスで公開しています。

- [togakyo/sim-reachy](https://github.com/togakyo/sim-reachy)

# 参考

- [Reachy 2 公式ドキュメント（シミュレータ）](https://docs.pollen-robotics.com/developing-with-reachy-2/simulation/simulation-introduction/)
- [Reachy 2 の Docker イメージ](https://hub.docker.com/r/pollenrobotics/reachy2)
- [reachy2-sdk（GitHub）](https://github.com/pollen-robotics/reachy2-sdk)
- [Ollama の structured outputs](https://ollama.com/blog/structured-outputs)
