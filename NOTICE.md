# サードパーティのソフトウェアについて

このリポジトリ本体は [LICENSE](LICENSE)（MIT）で配布しています。
そのうえで、以下のものを含む・利用しています。

## リポジトリに同梱しているもの

| もの | ライセンス | 場所 |
| --- | --- | --- |
| three.js v0.180.0 | MIT | [web/vendor/](web/vendor/) — ライセンス全文は [web/vendor/LICENSE-three.txt](web/vendor/LICENSE-three.txt) |

同梱している他人のコードは three.js だけです。

## 同梱していないが、実行時に必要なもの

これらはリポジトリに含めていません。利用者が自分でインストール・取得します。
それぞれの提供元の条件が適用されます。

| もの | ライセンス | 入手方法 |
| --- | --- | --- |
| Reachy 2 Docker イメージ（`pollenrobotics/reachy2`） | Pollen Robotics の提供条件 | `docker pull` |
| reachy2-sdk | Apache-2.0 | `pip install` |
| FastAPI / uvicorn / httpx / python-multipart | MIT / BSD / Apache-2.0 | `pip install` |
| faster-whisper と Whisper のモデル | MIT | `pip install` / 初回実行時に自動取得 |
| Gemma 3 モデル（`gemma3:4b`） | [Gemma 利用規約](https://ai.google.dev/gemma/terms)（使用制限あり） | `ollama pull` |
| Ollama | MIT | Homebrew |
| 読み上げ音声（macOS の `say`） | Apple の条件 | macOS 同梱 |

Gemma のモデルには利用規約と
[使用禁止ポリシー](https://ai.google.dev/gemma/prohibited_use_policy)があります。
モデルを配布したり、サービスとして提供したりする場合は、そちらを確認してください。

このリポジトリでは、ポリシーの各項目について実際に問いかけて応答を確認し、
モデルの判断だけに任せられない範囲を [server/brain.py](server/brain.py) の
`_screen()` でコード側から受け止めています（性的な話題、自傷、
人を傷つける方法、薬や通院の判断）。詳しくは
[README の「安全のしくみ」](README.md#安全のしくみ)を参照してください。
ただしローカルの言語モデルが生成する以上、すり抜けは起こりえます。

## 商標について

「Reachy」および「Pollen Robotics」は Pollen Robotics の名称・商標です。
このプロジェクトは Pollen Robotics とは**無関係の非公式なもの**で、
同社が承認・提供しているものではありません。同社のシミュレータと
SDK を利用していることを説明するために名称を使っています。

## 免責

このソフトウェアは個人的な学習・実験のためのものです。子供が使うことを
想定していますが、安全性の認証を受けた製品ではありません。会話の内容は
ローカルの言語モデルが生成するもので、[server/brain.py](server/brain.py) の
安全対策をすり抜ける可能性があります。子供が使うときは大人が付き添ってください。
