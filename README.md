# ollama-toolbox

ローカルの Ollama モデルに tool を持たせる道具箱。今は web 検索。
検索も推論も**自分のマシンで完結する**（検索クエリを外のサービスに預けない）。
Windows 10 + RTX 5060 Ti 16GB で構築。

**セットアップ（Ollama とモデルの導入）は [`docs/setup.md`](docs/setup.md)。**

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

**Forge や ComfyUI に移る前は `stop.bat` を叩くこと。**
モデルは VRAM をほぼ使い切る。急がないなら5分で自動解放される。

> **確実に最新が要る場面では検索を強制すること**（`-f` / web の `force`）。
> モデルは4〜6割の確率で検索せず記憶から答える。
> 理由は [`docs/model-date-sense.md`](docs/model-date-sense.md)。

## CLI（`start.bat`）

**`exit` / `quit` / `終了` / `おわり` / `:q`**（大小文字問わず）か `Ctrl-C` で抜ける。
**会話の文脈は引き継がれる。**

**思考は stderr、答えは stdout。** 分離されているので
`2>nul` で思考だけ捨てられるし、`>out.txt` で答えだけ拾える。
最初の1問はモデルのロードで時間がかかるが、思考が流れ始めるので止まって見えない。

### コマンド

行頭 `/` はコマンド。`/help` で一覧が出る。**知らないコマンドはエラーになる**
（モデルには送られない）。

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

> **`/system` は Modelfile の SYSTEM を置き換える**（重ねない）。
> 日付まわりの禁止も消えるので注意。`/system reset` で戻る。

## web の画面（`webui.bat`）

`http://127.0.0.1:4645`。CLI と**同じエージェント**を呼ぶ。画面が増えるだけ。

| | |
|---|---|
| 会話ログ | 左に一覧。**サーバを再起動しても続きから話せる**（隣の `chats.sqlite`） |
| システムプロンプト | `system` ボタンから会話ごとに。空にすると Modelfile の SYSTEM に戻る |
| モデル | 上部で切り替え |
| `think` / `force` | CLI の `--no-think` / `-f` と同じ |
| 思考 | 折りたたみで、届いた端から流れる |
| 検索結果 | タイトルと URL のリンク付き |
| 進行状況 | ログの最下段に今なにをしているかと経過秒 |
| **送信** | **Shift+Enter で送信、Enter は改行** |

**127.0.0.1 にしか bind しない。認証も無い。** 外に出さないこと。

### VRAM インジケーターの読み方

バーは2本重ねてある。下が GPU 全体の使用量、上が今のモデルの分。

| 表示 | 意味 |
|---|---|
| `10.2/15.9GB · 9.2GB` | GPU 全体で 10.2GB 使用、うち今のモデルが 9.2GB |
| `· 未ロード` | 選んでいるモデルはまだ載っていない |
| `(CPU に N GB)` | **100% GPU に載っていない** |
| `(カードの容量超過)` | Ollama の申告が物理 VRAM を超えた。共有メモリに逃げている |
| バーが赤 | 空きが 2GB を切った、または容量超過 |

マウスを乗せると `num_ctx` と空き容量も出る。

## 検索バックエンドは3つ

`SEARCH_BACKEND` で切り替える。**未設定なら `BRAVE_API_KEY` の有無で自動選択。**

| | 中身 | キー | 別プロセス |
|---|---|---|---|
| **`brave`** | [Brave Search API](https://brave.com/search/api/)（**公式**） | 要る | 不要 |
| **`ddgs`** | pip の [ddgs](https://pypi.org/project/ddgs/)（旧 `duckduckgo-search`） | 不要 | 不要 |
| `searxng` | 自前で立てた SearXNG | 不要 | 要る（→ [`docs/searxng.md`](docs/searxng.md)） |

選定の経緯・Brave の規約・どのエンジンが実際に動くかは
[`docs/search-backends.md`](docs/search-backends.md)。

### キーの置き場

`.env.example` を `.env` にコピーして書く。**`.env` は `.gitignore` 済み。**

```
BRAVE_API_KEY=xxxxxxxxxxxxxxxx
```

`ollama-search.py` が隣の `.env` を読む。
**既存の環境変数があればそちらが優先**されるので、一時的な上書きは
`SEARCH_BACKEND=ddgs python ollama-search.py ...` でできる。

### セーフサーチ

`SEARCH_SAFESEARCH` で `off` / `moderate` / `strict`。**既定は `off`。**
バックエンドごとに語彙が違うので、ツール側で変換している:

| このツール | Brave | ddgs | SearXNG |
|---|---|---|---|
| `off` | `off` | `off` | `0` |
| `moderate` | `moderate` | `moderate` | `1` |
| `strict` | `strict` | **`on`** | `2` |

**無効値を渡すと起動時に落ちる**（`exit 1`）。

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

**なぜそうなっているか・踏んだ罠・実測値はこちら。**
このリポジトリを改修するなら [`CLAUDE.md`](CLAUDE.md) も読むこと。

| | |
|---|---|
| [`setup.md`](docs/setup.md) | Ollama とモデルの導入手順 |
| [`vram.md`](docs/vram.md) | VRAM の実測。**flash attention を入れる理由**、`num_ctx` の効き方、外れた仮説 |
| [`model-date-sense.md`](docs/model-date-sense.md) | モデルの日付感覚と、機械的に潰している3つ |
| [`conversation.md`](docs/conversation.md) | 会話の文脈の持ち方。溢れたときの削り方 |
| [`webui.md`](docs/webui.md) | web の画面の設計判断と既知の粗さ |
| [`search-backends.md`](docs/search-backends.md) | 選定の経緯、Brave の規約、各エンジンの実測 |
| [`searxng.md`](docs/searxng.md) | SearXNG を Windows で動かす細工と実測 |
| [`windows-bat.md`](docs/windows-bat.md) | `.bat` で踏んだ罠 |
