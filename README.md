# ollama-toolbox

ローカルの Ollama モデルに tool を持たせる道具箱。今は web 検索。
Windows 10 + RTX 5060 Ti 16GB で構築。

**セットアップ（Ollama とモデルの導入）は [`docs/setup.md`](docs/setup.md)。**
まっさらな Windows 機からここまで持っていける手順を、
人間が読んでもエージェントが実行してもいいように書いてある。

このリポジトリ自体の依存は1つだけ:

```
pip install -r requirements.txt
```

## 起動

```
start.bat                  対話モード（CLI）
start.bat "日銀の直近の決定は？"
start.bat -f "..."         検索を強制（モデルの判断に任せない）
start.bat --no-think       思考を切る（速いが精度は落ちる）

webui.bat                  web の画面（http://127.0.0.1:4645）

stop.bat                   VRAM を解放して SearXNG を止める
stop.bat /all              上記に加えて Ollama も終了する
```

`start.bat` / `webui.bat` が Ollama を必要なら起動し、応答するまで待ってから渡す。
既に動いていれば素通りする。

**確実に最新が要る場面では検索を強制すること**（`-f` / web の `force`）。
モデルは4〜6割の確率で検索せず記憶から答える
（→ [`docs/model-date-sense.md`](docs/model-date-sense.md)）。

## CLI（`start.bat`）

対話モードは **`exit` / `quit` / `終了` / `おわり` / `:q`**（大小文字問わず）か
`Ctrl-C` で抜ける。**会話の文脈は引き継がれる。**

**モデルの思考は stderr にリアルタイムで流れる。** 答えは stdout なので分離されており、
`2>nul` で思考だけ捨てられるし、`>out.txt` で答えだけ拾える。
最初の1問はモデルのロードで時間がかかるが、思考が流れ始めるので止まって見えない。

### コマンド

行頭 `/` はコマンド。`/help` で一覧が出る。

| | |
|---|---|
| `/clear` | 会話履歴を捨てる（システムプロンプトとモデルはそのまま） |
| `/history` | 今積んでいる履歴とトークン量 |
| `/system` | システムプロンプトの表示・設定・解除（`/system reset`） |
| `/model` | モデルの表示・切り替え |
| `/think` | 思考の on / off |
| `/force` | 検索強制を常時 on / off |
| `/help` | 一覧 |
| `/exit` | 抜ける |

* **行頭 `!`** — その1問だけ検索を強制する（`/force` の1回版）
* **行頭 `//`** — `/` で始まる文章をそのまま送る。`//help` は `/help` として届く

**知らないコマンドはモデルに送らず、その場でエラーにする。**
コマンドの出力は全部 stderr で、答え（stdout）と混ざらない。

**`/system` は Modelfile の SYSTEM を置き換える**（重ねない）。
日付まわりの禁止も消えるので注意（`/system reset` で戻る）。
**`/model` を切り替えると `num_ctx` も引き直す**（履歴は残る）。

## web の画面（`webui.bat`）

`http://127.0.0.1:4645`。CLI と**同じエージェント**を呼ぶので、
年号の除去・検索失敗での打ち切り・上限到達時の回答強制はそのまま効く。

| | |
|---|---|
| 会話ログ | 左に一覧。**サーバを再起動しても続きから話せる**（隣の `chats.sqlite`） |
| システムプロンプト | `system` ボタンから会話ごとに。空にすると Modelfile の SYSTEM に戻る |
| 思考 | 折りたたみで、届いた端から流れる |
| 検索結果 | タイトルと URL のリンク付き（モデルが何を見て答えたかが追える） |
| モデル | 上部で切り替え |
| VRAM | 上部のインジケーター（下記） |
| 進行状況 | ログの最下段に今なにをしているかと経過秒 |
| 送信 | **Shift+Enter で送信、Enter は改行** |

**CLI との一番の違いは、文脈が sqlite にあること。** CLI の対話モードはプロセスが
死ねば消えるが、こちらは残る。ログが正なので、別のタブから続きを書いても同じ文脈になる。

### 決めごと

* **127.0.0.1 にしか bind しない。** モデルの求めに応じて任意の URL を取りに行く
  （`web_fetch`）ので、外から叩ける場所に置くと踏み台になる。認証も無い
* **生成は同時1本。** 2本目は `409` を返して待たせない
* **ブラウザを閉じても生成は止まらない。** 答えは sqlite に入るので開き直せば読める

### VRAM インジケーター

バーは**2本重ねてある**。下が GPU 全体の使用量、上が今のモデルの分。
「空きが無い」のか「このモデルが食っている」のかを分けて見るため。

| 表示 | 意味 |
|---|---|
| `10.2/15.9GB · 9.2GB` | GPU 全体で 10.2GB 使用、うち今のモデルが 9.2GB |
| `· 未ロード` | 選んでいるモデルはまだ載っていない |
| `(CPU に N GB)` | **100% GPU に載っていない**（`size` と `size_vram` の差） |
| `(カードの容量超過)` | Ollama の申告が物理 VRAM を超えた。共有メモリに逃げている |
| バーが赤 | 空きが 2GB を切った、または容量超過 |

数字は `nvidia-smi`（GPU 全体）と `/api/ps` の `size_vram`（モデル別）。
サーバ側で2秒キャッシュし、タブが裏にいる間は測らない。

> **`nvidia-smi` の使用量だけでは溢れを判定できない。** アロケータが使える分を
> 使い切るので天井付近に張り付く。詳しくは [`docs/vram.md`](docs/vram.md)。

### 進行状況の表示

冷えたモデルは最初のトークンまで無反応なので、ログの最下段に段階と経過秒を出す。

```
モデルの応答を待っている → 考えている → web_search: <クエリ>
  → 結果を読んでいる → 考えている → 答えを書いている → 消える
```

出力中のブロックの末尾に点滅を出す（文字が途切れたのか、まだ来るのかの区別）。
`prefers-reduced-motion` が有効なら動きは止まる。

## 会話の文脈

**CLI の対話モードも web の画面も前のやり取りを覚えている。**
one-shot（引数で質問を渡す形）は1問で終わるので持たない。

`/api/chat` は**ステートレス**で、履歴はクライアントが毎回まとめて送り直す仕様。
覚えているかどうかは**こちら側の実装次第**であって、Ollama の機能ではない。

### 溢れたら古い方から捨てる（黙って忘れない）

`num_ctx` を超えると Ollama は**前から黙って切り捨てる**。真っ先に消えるのは
先頭の SYSTEM で、日付まわりの禁止が効かなくなる。それを避けるため、
履歴が予算（`num_ctx` の半分）を超えたら自分で削り、**削ったことを伝える**。

1. **古いターンから検索結果だけ剥がす**（嵩む割に後から効きにくい）
2. それでも足りなければ**ターンごと**捨てる

**ツール呼び出しと結果は必ずセットで落とす。** 履歴をターン単位で持っているのはこのため。
過去ターンの `thinking` は積まない（Qwen のテンプレートが元々渡さない）。

### 見積もりは粗い

トークン数は **文字数 ÷ 係数**。応答の `prompt_eval_count`（実測）で係数を補正している。
`num_ctx` は `/api/show` の `PARAMETER num_ctx` から読み、ロード後に `/api/ps` の
`context_length` で上書きする。

> **`model_info` の `<arch>.context_length` は使っていない。** あれはアーキ上の最大
> （このモデルなら 262144）で、Ollama が確保する量ではない。

## 検索バックエンドは3つ

`SEARCH_BACKEND` で切り替える。**未設定なら `BRAVE_API_KEY` の有無で自動選択。**

| | 中身 | キー | 別プロセス |
|---|---|---|---|
| **`brave`** | [Brave Search API](https://brave.com/search/api/)（**公式**） | 要る | 不要 |
| **`ddgs`** | pip の [ddgs](https://pypi.org/project/ddgs/)（旧 `duckduckgo-search`） | 不要 | 不要 |
| `searxng` | 自前で立てた SearXNG | 不要 | 要る（→ [`docs/searxng.md`](docs/searxng.md)） |

**いずれも自分のマシンから検索エンジンへ直接行く。**
選定の経緯・Brave の規約・どのエンジンが実際に動くかは
[`docs/search-backends.md`](docs/search-backends.md)。

### キーの置き場

`.env.example` を `.env` にコピーして書く。**`.env` は `.gitignore` 済み。**

```
BRAVE_API_KEY=xxxxxxxxxxxxxxxx
```

`ollama-search.py` が隣の `.env` を読む（`python-dotenv` は使わない）。
**既存の環境変数があればそちらが優先**されるので、一時的な上書きは
`SEARCH_BACKEND=ddgs python ollama-search.py ...` でできる。

### セーフサーチ

`SEARCH_SAFESEARCH` で `off` / `moderate` / `strict`。**既定は `off`。**
バックエンドごとに語彙が違うので、ツール側で正準語を決めて変換している:

| このツール | Brave | ddgs | SearXNG |
|---|---|---|---|
| `off` | `off` | `off` | `0` |
| `moderate` | `moderate` | `moderate` | `1` |
| `strict` | `strict` | **`on`** | `2` |

**無効値を渡すと起動時に落ちる**（`exit 1`）。黙って既定に戻すと
`stict` のようなタイポで「厳しくしたつもりが素通し」になるため。
既定を `off` にした理由と効いていることの実測は
[`docs/search-backends.md`](docs/search-backends.md)。

## 環境変数

| 変数 | 既定 |
|---|---|
| `SEARCH_BACKEND` | キーがあれば `brave`、無ければ `ddgs`（`searxng` も可） |
| `DDGS_BACKEND` | `bing,brave,yandex` |
| `SEARCH_SAFESEARCH` | `off`（`moderate` / `strict` も可） |
| `BRAVE_API_KEY` | なし（`.env` から読む） |
| `SEARXNG_DIR` | `<このリポジトリ>/../searxng` |
| `SEARXNG_URL` | `http://127.0.0.1:8888` |
| `OLLAMA_HOST` | `http://localhost:11434` |
| `OLLAMA_SEARCH_MODEL` | `hauhau-aggressive:iq2m` |
| `OLLAMA_FLASH_ATTENTION` | `1`（bat が設定。**入れないとモデルが 16GB に載らない**） |
| `WEBUI_PORT` | `4645`（埋まっていたら上に空きを探す） |
| `WEBUI_DB` | `<このリポジトリ>/chats.sqlite`（`.gitignore` 済み） |

**`OLLAMA_FLASH_ATTENTION` は既に動いている Ollama には効かない。**
恒久的にするならユーザー環境変数に置くこと（→ [`docs/vram.md`](docs/vram.md)）。

## 構成

| ファイル | |
|---|---|
| `start.bat` | CLI の入口。起動と待機 |
| `webui.bat` | web の画面の入口 |
| `stop.bat` | VRAM 解放と SearXNG 停止。`/all` で Ollama も |
| `ollama-search.py` | エージェント本体。`ddgs` 以外は標準ライブラリのみ |
| `webui.py` | web サーバ。**標準ライブラリのみ**（`http.server` + `sqlite3`） |
| `webui.html` | 画面。1ファイル、フレームワーク不使用 |
| `requirements.txt` | `ddgs`。`ddgs` バックエンドに必要（brave は stdlib のみ） |
| `.env.example` | キーの雛形。`.env` にコピーして使う |
| `settings-local.yml` | SearXNG 設定。**SearXNG の checkout 直下に置く原本** |
| `sitecustomize.py` | Unix API シム。**SearXNG の `.venv/Lib/site-packages/` に置く原本** |
| `start-searxng.ps1` | SearXNG 単体起動。**SearXNG の checkout 直下に置く原本** |

モデルは `hauhau-aggressive:iq2m`（Qwen3.6-35B-A3B Uncensored / IQ2_M、
`num_ctx 40960` と日付まわりの SYSTEM を焼いた別名）。

## docs

| | |
|---|---|
| [`setup.md`](docs/setup.md) | Ollama とモデルの導入手順 |
| [`vram.md`](docs/vram.md) | VRAM の実測。**flash attention を入れる理由**、`num_ctx` の効き方 |
| [`model-date-sense.md`](docs/model-date-sense.md) | モデルの日付感覚と、機械的に潰している3つ |
| [`search-backends.md`](docs/search-backends.md) | 選定の経緯、Brave の規約、各エンジンの実測 |
| [`searxng.md`](docs/searxng.md) | SearXNG を Windows で動かす細工と実測 |
| [`windows-bat.md`](docs/windows-bat.md) | `.bat` で踏んだ罠 |

## 未検証・既知の粗さ

* `web_fetch` は正規表現でタグを落とすだけの簡易実装。JS 描画のページでは本文が取れない
* `ddgs` は非公式ライブラリで、各エンジンのスクレイピングに依存している。
  **動くエンジンの顔ぶれは今後変わる**
* SearXNG は常駐化していない（`searxng` バックエンド時、`start.bat` が都度起動する）
* 年月の除去は正規表現。`GPT-2024` のような命名があれば誤爆しうる
* web の画面はモデルの切り替えが**プロセス全体に効く**（`agent.MODEL` がグローバル）。
  生成は同時1本なので競合はしないが、**他の会話の既定も変わる**
* web の画面の `thinking` は、ターンごとに1つにまとめて最後の assistant に付けている
* web の画面には認証が無い。127.0.0.1 に閉じていることだけが防御
* このモデルはビジョンエンコーダを積んだまま載っている。web 検索には要らないが、
  外した場合の効果は未計測（→ [`docs/vram.md`](docs/vram.md)）
