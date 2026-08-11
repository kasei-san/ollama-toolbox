# CLAUDE.md

ローカルの Ollama モデルに web 検索の tool を持たせる道具箱。
使い方・環境変数・起動方法は README。ここは**触るときに知っておくこと**。

## 全体像

```
start.bat / webui.bat        入口。Ollama を必要なら起動して待つ
  ↓
ollama-search.py             エージェント本体（CLI と web で共通）
  Conversation               会話履歴。ターン単位で持ち、溢れたら削る
  ask()                      1ターン回して答えを返す
  emit(kind, payload)        進捗の出し先。CLI は stderr、web は SSE
  ↓
brave / ddgs / searxng       検索       +       Ollama /api/chat
```

`webui.py` は `ollama-search.py` を importlib で読んで `ask()` を呼ぶだけ。
**画面が増えるだけで、エージェントの挙動は CLI と同じ。**
会話ログは `chats.sqlite` にあり、毎回そこから `Conversation` を組み直す
（sqlite が正で、メモリ上の状態は持たない）。

## 壊してはいけないもの

いずれも**実際に踏んだ末に入っている**。消すと静かに壊れる。

### 1. 検索まわりの3つ（`docs/model-date-sense.md`）

モデルの日付感覚が 2024年5月で止まっているのが、このリポジトリの設計理由。

* **検索失敗を握り潰さない** — エラーを渡すとモデルは無視して記憶から答える
* **クエリから年月を落とす**（`strip_stale_year`）— 古い記事を「最新」として
  自信たっぷりに答えるのが一番たちの悪い失敗。**ユーザーが年月を書いたときは触らない**
* **上限到達時はツールを外して最終回答させる** — 記憶から答えさせるのとは別物

### 2. 履歴を削るときはツール呼び出しと結果をセットで落とす

`role: tool` と、それを呼んだ assistant の `tool_calls` は必ず一緒に落とす。
片方だけ残すと、応答の無い呼び出しや呼ばれていない結果が履歴に残って壊れる。
**`Conversation` がターン単位で持っているのはこのため。**

### 3. 安全側に倒れないフォールバックを作らない

`SEARCH_SAFESEARCH` に無効値が来たら**黙って既定に戻さず `exit 1`**。
`stict` のようなタイポで「厳しくしたつもりが素通し」になるため。
同じ理由で、履歴を削ったら必ず伝える（黙って忘れない）。

### 4. エラーの理由を捨てない・混ぜない

`str(HTTPError)` は `HTTP Error 500` までしか言わない。理由は本文にある。
`_ollama_error()` が本文を読む。
**`SearchFailed`（検索の失敗）と `OllamaFailed`（Ollama が受け付けない）を混ぜない。**
混ぜると VRAM 不足を「検索に失敗した」と表示して、見当違いの場所を調べ始める。

### 5. web の画面は 127.0.0.1 にしか bind しない

`web_fetch` がモデルの求めに応じて任意の URL を取りに行く。認証も無い。

## 書き方の決めごと

* **依存を増やさない。** `ddgs`（`ddgs` バックエンド専用）以外は標準ライブラリだけ。
  `webui.py` は `http.server` + `sqlite3`、画面は1ファイルでフレームワーク不使用
* **`.bat` は純ASCII。** cmd が OEM コードページで読むので日本語は化ける。
  日本語出力は Python 側に任せる（→ `docs/windows-bat.md`）
* **コメントは日本語で、「なぜ」を書く。** 「何を」はコードが言う。
  特に**踏んだ罠は罠だと分かる形で**残す
* **`ollama-search.py` は改名しない。** README に載っている入口なので、
  import しやすさのために変えない（`webui.py` 側が importlib で吸収する）

## 実測値の扱い

* **数値には計測日を付ける。** 「判断を左右するなら測り直すこと」も添える
* **1点の観測から制約を決めない。** 要因を1つずつ振って確かめる。
  実例: `size_vram` が大きいのを見て「`num_ctx` を下げれば載る」と考えたが外れた。
  振ってみたら全域で 1.9 GiB しか動かず、効いていたのは flash attention だった
* **`nvidia-smi` の使用量では VRAM の溢れを判定できない。** アロケータが使える分を
  使い切るので天井付近に張り付く。見るのは `/api/ps` の `size_vram`
* **生成物の良否を数値で判定しない。** 「無音でない」「0件でない」までは言えるが、
  答えの質が良いかはかせいさんに聞く

## 動作確認

```sh
# CLI
.venv/Scripts/python.exe ollama-search.py --no-think -q "2+2 は？"

# 対話モード（stdin は UTF-8 で流し込む。argv 経由の非ASCIIは壊れる）
printf '%s\n' '質問' 'exit' > /tmp/in.txt
.venv/Scripts/python.exe ollama-search.py --no-think < /tmp/in.txt

# web
.venv/Scripts/python.exe webui.py    # 別ポートで試すなら WEBUI_PORT / WEBUI_DB
```

* **非ASCIIを argv や `curl -d` で渡さない。** MSYS が壊す。
  UTF-8 でファイルに書いて `--data-binary @file`、応答は `.txt` に落として読む
* **重いモデルを使わずに済むなら使わない。** ロジックの確認は `qwen3:14b` で足りる
  （`OLLAMA_SEARCH_MODEL=qwen3:14b`）。常用モデルは VRAM を使い切るので、
  他の作業と競合する
* **サーバを立てたら必ず止める。** `webui.py` を上げっぱなしにすると
  `chats.sqlite` を掴んだままになる

## docs

| | |
|---|---|
| `docs/setup.md` | Ollama とモデルの導入手順 |
| `docs/vram.md` | VRAM の実測。flash attention、`num_ctx` の効き方、量子化の選択肢 |
| `docs/model-date-sense.md` | 日付感覚の問題と、機械的に潰している3つ |
| `docs/search-backends.md` | 選定の経緯、Brave の規約、各エンジンの実測 |
| `docs/searxng.md` | SearXNG を Windows で動かす細工 |
| `docs/windows-bat.md` | `.bat` で踏んだ罠 |

**仕様を変えたら README と該当の docs を同時に更新する。**
同じ内容を README と docs に重複させない（移したら元は消す）。
