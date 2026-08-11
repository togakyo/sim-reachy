# リーチーと おしゃべり — Reachy 2 Sim × 子供向け会話

Reachy 2 のシミュレータを立ち上げて、子供が声または文字で Reachy とおしゃべりできます。
返事の内容に合わせて Sim の中の Reachy が うなずいたり、手をふったり、よろこんだりします。

会話・音声認識・読み上げは **すべてこの Mac の中で完結**します。子供の声も
話した内容も、外部のサービスには送られません。

```
ブラウザ ──WebSocket──> 会話サーバ ──> Ollama (ローカル LLM)
 こえ/もじ  ↑                 │
   3D表示 ──┘ 関節角 30Hz     └────────> Reachy 2 Sim (gRPC :50051)
```

3D の Reachy は**ブラウザ側で描いています**。Sim の実際の関節角を 30Hz で受け取って
反映しているので、動きは本物ですが、かたちは子供向けにデフォルメしてあります。
（理由は「3D ビューについて」を参照）

## つかいかた

```bash
./scripts/start.sh
```

- おしゃべり画面: <http://localhost:8000> — 3D の Reachy もこの画面の中にいます

止めるときは、サーバは `Ctrl-C`、Sim は `./scripts/stop.sh`。

画面では **「🎤 こえ」** と **「⌨️ もじ」** をタブで切り替えられます。
こえの場合はマイクのボタンを押して話し、話し終わって 1.6 秒だまると自動で送られます。

ブラウザは **Chrome を推奨**します（マイク録音の形式が安定しているため）。
`localhost` なので https でなくてもマイクが使えます。

## 最初の 1 回だけ必要な準備

すでにこの Mac では済んでいます。別のマシンで動かすときの手順です。

1. **Docker Desktop** で Rosetta を有効にする
   Settings → General →「Use Rosetta for x86/amd64 emulation on Apple Silicon」にチェック。
   Reachy 2 の公式イメージは amd64 版しかないため、Apple Silicon では
   Rosetta 経由で動かします。確認:
   ```bash
   docker run --rm --platform linux/amd64 busybox uname -m   # → x86_64
   ```
2. **Ollama**
   ```bash
   brew install ollama && brew services start ollama
   ollama pull gemma3:4b
   ```
3. **Python 環境**（3.10 以上。macOS 標準の 3.9 では動きません）
   ```bash
   python3.12 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

## 中身

| ファイル | 役割 |
| --- | --- |
| [sim/compose.yaml](sim/compose.yaml) | Reachy 2 Sim（公式 Docker イメージ）の起動設定 |
| [server/app.py](server/app.py) | 会話サーバ。WebSocket・読み上げ・音声認識・ジェスチャー API |
| [server/brain.py](server/brain.py) | Ollama への問い合わせ、子供向けプロンプト、安全ガード |
| [server/robot.py](server/robot.py) | Reachy のジェスチャー定義と再生 |
| [server/speech.py](server/speech.py) | 読み上げ（macOS の `say`）と音声認識（faster-whisper） |
| [web/index.html](web/index.html) | 子供向けの画面 |
| [web/reachy3d.js](web/reachy3d.js) | ブラウザ側の 3D Reachy（three.js） |

## 3D ビューについて

Reachy 2 の公式イメージには RViz が入っていますが、**既定では切ってあります**。
macOS の Docker はコンテナに GPU を渡せないため、RViz は x86 エミュレーションの上で
ソフトウェア描画することになり、1 コアを常時使い切っても 2〜3fps しか出ません。
（実測: 動作を流しながら VNC の更新量を測ると、CPU 無制限でも約 2.4 更新/秒）

そこで**描画だけをブラウザに移しました**。Sim は制御と SDK サーバに専念し、
関節角を `/ws/pose` から 30Hz で送るだけ。ブラウザは Mac の GPU で 60fps で描きます。

- かたちは [web/reachy3d.js](web/reachy3d.js) の中で組み立てています。色や大きさは
  `COLORS` と各 `build*()` を触れば変えられます。
- 首・アンテナ・両腕の角度は Sim の実際の値です。手首や指は省略しています。
- 画面を横にドラッグすると、Reachy をまわして見られます。

コンテナの中の RViz を見たいときは [sim/compose.yaml](sim/compose.yaml) の
`start_rviz:=false` を `true` に戻すと <http://localhost:6080/vnc.html?autoconnect=1&resize=remote>
で見られます（そのぶん CPU を食い、会話が遅くなります）。

### ジェスチャー

LLM は返事と一緒に動きを 1 つ選びます:
`nod`（うなずく）`shake`（いやいや）`tilt`（首をかしげる）`happy`（よろこぶ）
`surprised`（びっくり）`think`（かんがえる）`wave`（手をふる）`bow`（おじぎ）`idle`。

追加や調整は [server/robot.py](server/robot.py) の `_GESTURES` で行います。
`("head", roll, pitch, yaw, 秒)` / `("ant", 左, 右, 秒)` / `("arm", [7関節], 秒)` /
`("wait", 秒)` を並べるだけです。

**角度はすべて「その角度そのもの」（絶対角）で書いてください。**
新しい動きが来ると再生中のものは途中で打ち切られます。相対回転で書くと
「元に戻す」ステップが実行されないまま次に進み、ズレが会話のたびに積み重なって
首が傾いたままになります（実際にそうなりました）。絶対角なら、どこで中断されても
次のジェスチャーが正しい姿勢へ引き戻せます。

同じ理由で、**どのジェスチャーも最後は `_HEAD_NEUTRAL` とアンテナ 0 に戻して**ください。
「なにもしない」の `idle` も、固まるのではなく中立へ戻す動きとして定義してあります。

手をふる `wave` は右手だけを使います（人が片手であいさつするのに合わせています）。
両手にしたい場合は `wave` に `l_arm` 用のステップを足してください。

単体で試すには:
```bash
curl -X POST http://localhost:8000/api/motion/wave
```

### 安全のしくみ

小さいモデルは言い方を変えて頼まれるとルールを破ることがあるため、
プロンプトだけに頼らず [server/brain.py](server/brain.py) の `_screen()` で
三段構えにしています。

| 子供の発話 | 動き |
| --- | --- |
| 「けんかの しかた おしえて」など、人を傷つける**やり方を求める**もの | LLM を通さず定型文で断り、大人に相談するようすすめる |
| 「しにたい」など深刻なもの | LLM を通さず、大人にすぐ話すよう伝える定型文 |
| 「おにいちゃんと けんかしちゃった」など**出来事や気持ちの共有** | 遮らず、注意書きを添えて LLM に渡し、共感して返す |

3 番目を遮らないのが要点です。話を聞いてほしい子を定型文で突き放さないようにしています。

## 設定（環境変数）

| 変数 | 既定値 | 説明 |
| --- | --- | --- |
| `OLLAMA_MODEL` | `gemma3:4b` | 会話モデル。変えたら `ollama pull` も必要 |
| `STT_MODE` | `local` | `local` = この Mac の Whisper / `browser` = ブラウザの音声認識（速いが音声が Google に送られます） |
| `STT_MODEL` | `small` | Whisper のモデルサイズ。`base` にすると速く、`medium` にすると正確 |
| `TTS_VOICE` | `Kyoko` | 読み上げの声。`say -v '?'` で一覧 |
| `TTS_RATE` | `170` | 読み上げの速さ |
| `REACHY_HOST` | `localhost` | Sim の接続先 |

## 動作の目安（M5 / 24GB、ほかの重い処理を動かしていない状態で測定）

- 返事: 中央値 **4.2 秒**、最大 5.2 秒（サーバ起動時にモデルを事前読み込み）
- 音声認識: 短い一文で **2〜8 秒**（初回はモデル読み込みで +30 秒ほど）
- ジェスチャー: 1.8〜5.3 秒。新しい動きが来たら再生中のものは打ち切って乗り換えます
- 3D 表示: ブラウザ側で 60fps

返事の速さは **この Mac の空き CPU 次第**です。学習や評価のジョブを並行して
回していると、Ollama が CPU を取れず 20 秒以上かかることがあります
（実測: 別の Python ジョブが 3.5 コアぶん使っている状態で中央値 21 秒）。

### 気をつける点

- **Sim の CPU 上限**: 何もしなくても ROS 2 の制御ループが 10 コアぶん（1000% 超）を
  回しきり、Ollama が CPU を取れずに返事が 15 秒以上かかるようになります。
  [sim/compose.yaml](sim/compose.yaml) で `cpus: "4.0"` に制限してあります。
  Sim を別マシンで動かす場合はこの制限を外してかまいません。
- **トルクは共有の状態**: Sim のトルクは、この Mac から繋いだどのクライアントでも
  変えられます。腕だけ落ちていると SDK は `r_arm is off. Goto not sent.` と
  警告を出して goto を捨てるだけなので、「首は動くのに腕が動かない」という
  分かりにくい壊れ方をします。[server/robot.py](server/robot.py) の `_ensure_on()` が
  ジェスチャーのたびに確認して入れ直します。
- **SDK に繋いだスクリプトは終了しないことがある**: `ReachySDK` が非デーモンの
  スレッドを立てるため、`disconnect()` せずに終わるスクリプトはプロセスが残り、
  1 本あたり 20〜45% の CPU を食い続けます。使い終わったら必ず `disconnect()` を
  呼んでください。残ってしまったら `pkill -f 自分のスクリプト名` で落とせます。
- **SDK の API バージョン警告**: ローカルの `reachy2-sdk` (API 1.0.21) と
  Docker イメージ (1.0.19) で版が違うという警告が出ますが、
  この構成で使う機能はすべて動作を確認しています。
- **カメラのエラーログ**: `orbbec:=false` でカメラを無効にしているため、
  起動時に映像ポート (50065) への接続失敗が出ます。会話・動作に影響はありません。
- `ReachySDK` は `sdk_port` 引数を渡すと `__new__()` でエラーになるため
  （SDK 1.0.15 の不具合）、gRPC ポートは既定の 50051 固定です。

## ライセンスと出典

このリポジトリのコードは [MIT ライセンス](LICENSE)です。
同梱している three.js（MIT）や、実行時に必要な Reachy 2 のイメージ、
Gemma のモデルなどの扱いは [NOTICE.md](NOTICE.md) にまとめています。

「Reachy」は Pollen Robotics の名称・商標です。このプロジェクトは
**非公式で、同社とは無関係**です。また安全性の認証を受けた製品ではないので、
子供が使うときは大人が付き添ってください。

### うまく動かないとき

`LOG_LEVEL=DEBUG` を付けて起動すると、ジェスチャーのステップごとの実行ログが出ます。

```bash
LOG_LEVEL=DEBUG .venv/bin/uvicorn app:app --app-dir server --port 8000
```
