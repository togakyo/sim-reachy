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

## Gemma を使うときの条件

このリポジトリは **Gemma のモデルを含んでいません**。利用者が自分で
`ollama pull` して入手します。

[Gemma 利用規約](https://ai.google.dev/gemma/terms)が配布時の義務
（規約の同梱など）を定めているのは「Gemma または Model Derivatives を
配布する場合」です。Model Derivatives とは Gemma を改変したモデルや、
Gemma のパターンを転移させて作った機械学習モデルを指します。
**モデルを呼び出すだけのコードは、これに当たりません。**

一方、[使用禁止ポリシー](https://ai.google.dev/gemma/prohibited_use_policy)は
**実際にモデルを動かす人**に適用されます。このリポジトリをクローンして
動かす場合は、あなたがその対象になります。

そこで、モデルの判断だけに任せられない範囲を
[server/brain.py](server/brain.py) の `_screen()` でコード側から
受け止めています（性的な話題、自傷、人を傷つける方法、薬や通院の判断）。
ポリシーの各項目について実際に問いかけ、応答を確認したうえで決めた範囲です。

**手を入れるときは [README の「遮断を追加する」](README.md#遮断を追加する)を
読んでから**、下記で確認してください。

```bash
.venv/bin/python scripts/check_safety.py
```

ただしローカルの言語モデルが生成する以上、すり抜けは起こりえます。
`OLLAMA_MODEL` を変えた場合は、そのモデルの条件と応答の傾向を
あらためて確認してください。

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
