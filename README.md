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

```
start.bat                  対話モード
start.bat "日銀の直近の決定は？"
start.bat -f "..."         検索を強制（モデルの判断に任せない）
start.bat --no-think       思考を切る（速いが精度は落ちる）

stop.bat                   VRAM を解放して SearXNG を止める
stop.bat /all              上記に加えて Ollama も終了する
```

対話モードは **`exit` / `quit` / `終了` / `おわり` / `:q`**（大小文字問わず）か
`Ctrl-C` で抜ける。

**モデルの思考は stderr にリアルタイムで流れる。** 答えは stdout なので分離されており、
`2>nul` で思考だけ捨てられるし、`>out.txt` で答えだけ拾える。
最初の1問はモデルのロードで50秒ほどかかるが、思考が流れ始めるので止まって見えない。

`start.bat` が Ollama を必要なら起動し、応答するまで待ってから
`ollama-search.py` に渡す。既に動いていれば素通りする。

## 検索バックエンドは2つ

`SEARCH_BACKEND` で切り替える。**既定は `ddgs`。**

| | 中身 | 別プロセス |
|---|---|---|
| **`ddgs`**（既定） | pip の [ddgs](https://pypi.org/project/ddgs/)（旧 `duckduckgo-search`） | **不要** |
| `searxng` | 自前で立てた SearXNG | 要る（`start.bat` が面倒を見る） |

**`ddgs` を既定にしたのは SearXNG のセットアップが丸ごと不要になるから。**
clone も sparse-checkout も Unix API シムも常駐プロセスも起動待ちも消える。
プライバシーは同等 — どちらも自分のマシンから検索エンジンへ直接行き、
仲介するサービスは無い（ollama.com の web search API を使わない理由は後述）。

### どのエンジンが実際に動くか（2026-08-11 実測）

`ddgs` は名前に反して**メタ検索**で、text 用に9エンジンを持つ。結果を返したのは3つだけ:

| 動く | 0件で死ぬ |
|---|---|
| **bing / brave / yandex** | duckduckgo / **google** / mojeek / yahoo / startpage / wikipedia |

**`duckduckgo` 本体が死んでいるのは皮肉**だが、**`google` も同じく死んでいる**ので、
結果として **Google 依存からは完全に離れられている**
（SearXNG 経由だと結果の75%が `google cse` 由来だった。後述）。

既定は `DDGS_BACKEND="bing,brave,yandex"` と**明示**してある。`auto` でも動くが、
何を叩いているか読めなくなるため。上記が枯れたら `DDGS_BACKEND` で差し替える。

### DuckDuckGo の公式 API は使えない（2026-08-11 調査）

**DuckDuckGo に公式の web 検索 API は存在しない。** 唯一公開されているのは
Instant Answer API（`api.duckduckgo.com`）だが、これは検索 API ではない。実測:

| クエリ | 結果 |
|---|---|
| `python programming language` | Wikipedia の要約が返る |
| `current prime minister of Japan` | **完全に空** |
| `RTX 5060 Ti review` | **完全に空** |

**`Results` は常に `[]`** で、web リンクを一切返さない。エンティティ辞書であって検索ではない。

スクレイピング用によく使われる `html.duckduckgo.com/html/` も試したが、
**CAPTCHA ページが返って結果 0 件**。`ddgs` の `duckduckgo` バックエンドが
死んでいるのはこれが理由。

**より安定させたいなら公式 API のある [Brave Search API](https://brave.com/search/api/)
（無料枠 2,000 クエリ/月）に移るのが筋。** brave は既に動いている3エンジンの1つなので、
スクレイピングから公式 API に置き換える形になり、レート制限と HTML 変更の
リスクが消える。**`ddgs` はスクレイピングなので各サイトの規約上もグレー**である点も併せて。

## 終了してもサービスは残る

`start.bat` は `start` で切り離して起動するので、launcher が終わっても生き続ける。
次回起動が一瞬で済むのでこれは意図どおり。ただし:

| 残るもの | コスト |
|---|---|
| Ollama（2プロセス） | RAM 約98MB |
| SearXNG（`searxng` バックエンド時のみ） | RAM 約94MB + 最小化された `SearXNG` コンソール窓 |
| **VRAM** | **約15.8GB。最後の質問から約5分で自動解放** |

RAM は無視できるが **VRAM は無視できない**。**Forge や ComfyUI に移る前は
`stop.bat` を叩く**こと（実測 15826 → 604 MiB）。5分待てば自動で解放されるので、
急がないなら放っておいてもよい。

`stop.bat` は **8888 を LISTEN しているポートから PID を引いて**落とす。
イメージ名で `python.exe` を殺すと ComfyUI や他のスクリプトを巻き込むため。

## 構成

| ファイル | |
|---|---|
| `start.bat` | 入口。起動と待機 |
| `stop.bat` | VRAM 解放と SearXNG 停止。`/all` で Ollama も |
| `ollama-search.py` | エージェント本体。`ddgs` 以外は標準ライブラリのみ |
| `requirements.txt` | `ddgs`。既定バックエンドに必要 |
| `settings-local.yml` | SearXNG 設定（`searxng` バックエンド時のみ）。**SearXNG の checkout 直下に置く原本** |
| `sitecustomize.py` | Unix API シム。**SearXNG の `.venv/Lib/site-packages/` に置く原本** |
| `start-searxng.ps1` | SearXNG 単体起動。**SearXNG の checkout 直下に置く原本** |

モデルは `hauhau-aggressive:iq2m`（Qwen3.6-35B-A3B Uncensored / IQ2_M、
`num_ctx 40960` と日付まわりの SYSTEM を焼いた別名）。
環境変数で差し替えられる（下の `## 配置と設定`）。

## なぜ ollama.com の web search API を使わないか

公式 API（`ollama signin` + APIキー、無料）の方が手軽だが、
**検索クエリが ollama.com に送られる**。推論は元々ローカル完結なので外に出るのは
「何を検索したか」だけだが、それも出したくないという判断。
`ddgs` も SearXNG も、自分のマシンから検索エンジンへ直接行く。

## モデルの日付感覚が 2024年5月で止まっている

このエージェントの設計はほぼこの一点への対処。実測で分かったこと:

* **今日の日付を SYSTEM で教えるのは逆効果。** 「自分は現在を把握している」と誤認して
  **ツールを呼ばずに記憶で答える**（3回とも失敗）。渡すべきは日付ではなく
  「クエリに年を足すな」という**禁止**
* **その禁止も4〜6割しか効かない。** 「日本の首相は？」のようにモデルが
  「知っている」と思い込む質問では SYSTEM を無視する
  → 確実に最新が要る場面は **`-f` で検索を強制する**

### スクリプト側で機械的に潰している3つ

**改修するとき壊さないこと。** いずれも実際に踏んだもの。

1. **検索失敗を握り潰さない** — ツールがエラーを返すとモデルは無視して記憶から答える。
   `SearchFailed` で打ち切り exit 1
2. **クエリから年月を落とす**（`strip_stale_year`）— `AI latest news 2024` は検索が
   成立してしまい、**古い記事を「最新」として自信たっぷりに答える**という一番たちの
   悪い失敗になる。ユーザー自身が年月を書いたときは触らない
3. **上限に達したらツールを外して最終回答させる** — 年だけ落として月名を残すと
   `AI news January` `AI news March` と月を変えて延々と検索し直した。
   打ち切り時は集めた結果から答えさせる（**記憶から答えさせるのとは別物**）

## SearXNG バックエンドを使う場合

**`ddgs` を使うならこの節は丸ごと不要。**

### Windows で動かすための細工

Docker も WSL も使っていない。**SearXNG は Linux 前提だが、依存に uwsgi も uvloop も
無いので Windows の Python でそのまま動く。**

1. **`utils/templates/` を sparse-checkout で除外する。** 4ファイルが名前に `:` を含み
   （`searxng.conf:socket`）NTFS で作れず、**clone がツリー全体ごと失敗する**:

   ```sh
   # このリポジトリと並べて置くと start.bat が既定で見つける
   #   <親>/searxng
   #   <親>/ollama-toolbox
   git clone --depth 1 https://github.com/searxng/searxng.git ../searxng
   cd ../searxng
   git sparse-checkout set --no-cone '/*' '!/utils/templates/*'
   git checkout -- .
   python -m venv .venv
   .venv/Scripts/python.exe -m pip install -r requirements.txt
   ```

2. **`sitecustomize.py` を venv に置く。** `searx/valkeydb.py` が `pwd` と `os.getuid()` を
   無条件に呼ぶ（Valkey 接続失敗時のログ用）。リポジトリ本体を書き換えると
   `git pull` で壊れるので venv 側に置く

**JSON 出力は既定で無効。** `settings-local.yml` の `search.formats` で有効化している。

### SearXNG 側で実際に使われているエンジン（2026-08-11 実測）

`use_default_settings: true` なので general カテゴリの **61エンジンすべてが叩かれる**が、
**実際に結果を返しているのは2つだけ**。3クエリ80件の内訳:

| エンジン | 寄与した結果数 |
|---|---|
| **google cse** | 60 |
| **brave** | 20 |
| その他59個 | 0 |

明示的に落ちるもの: `duckduckgo` は **CAPTCHA（3/3回）**、`startpage` も CAPTCHA（3/3）、
`brave` は too many requests（3回中2回）。

61個のうち `dictzone`（辞書）`currency`（為替）`lingva`/`mozhi`（翻訳）`tineye`（画像逆引き）
`wikiquote`/`wikisource` などは **general カテゴリだが普通の web 検索ではない**ので、
0件なのが正常。残る本来の検索エンジン（bing, mojeek, qwant, yandex, yahoo, seznam, yep 等）が
**エラーも出さず0件**な理由は**未検証**。

**結果の75%が google cse 1本に依存しており、実質シングルポイント。**
「検索結果が0件」で打ち切られる事象はこの脆さが原因と思われる。頻発するなら:

* bing / mojeek が無言で0件な理由を調べて直す
* `settings-local.yml` の `engines:` で**動くエンジンだけに絞る**（無駄な待ちが減り速くもなる）
* Brave Search API のキーを取ってレート制限を回避する

## `start.bat` を書いていて踏んだ罠

**全部「黙って失敗する」類。** bat 内にもコメントで残してある。

* **`start` は `/MIN` をタイトルより先に置く。** `start "title" /MIN prog` だと
  タイトルをコマンド扱いして `ファイル \title\ が見つかりません` になる
* **`start` に `/D` が要る。** 作業ディレクトリが SearXNG の checkout でないと
  `python -m searx.webapp` が `ModuleNotFoundError` で即死する。errorlevel は 0 のままで、
  待ちループが空回りするだけで理由が出ない
* **`timeout.exe` を使わない。** stdin がリダイレクトされていると
  `Input redirection is not supported` で死ぬ。Git Bash から起動すると GNU coreutils の
  `timeout` に解決されて `/t` を拒否する。`ping -n 2` で待つ
* **`OLLAMA_MODELS` を明示する。** 設定より前に起動したシェルから叩くと Ollama が
  既定のモデルディレクトリを見て `ollama list` が空になり、全リクエストが
  404 "model not found" になる
* **`.bat` は純ASCIIで書く。** cmd は OEM コードページ（cp932）で読むので
  日本語を埋めると化ける。日本語出力は Python 側に任せる

## 未検証・既知の粗さ

* `web_fetch` は正規表現でタグを落とすだけの簡易実装。依存を増やしたくなかったため。
  JS 描画のページでは本文が取れない
* `ddgs` は非公式ライブラリで、各エンジンのスクレイピングに依存している。
  **動くエンジンの顔ぶれは今後変わる**（現に duckduckgo と google は既に死んでいる）
* SearXNG は常駐化していない（`searxng` バックエンド時、`start.bat` が都度起動する）
* 年月の除去は正規表現。`(?:19|20)\d{2}` に限定しているが、
  `GPT-2024` のような命名があれば誤爆しうる

## 配置と設定

**`searxng` バックエンドを使う場合のみ**、SearXNG がこのリポジトリと並んでいることを既定とする:

```
<親ディレクトリ>/
  searxng/          <- SearXNG の checkout（venv 込み）
  ollama-toolbox/   <- このリポジトリ
```

別の場所に置くなら環境変数で上書きする:

| 変数 | 既定 |
|---|---|
| `SEARCH_BACKEND` | `ddgs`（`searxng` も可） |
| `DDGS_BACKEND` | `bing,brave,yandex` |
| `SEARXNG_DIR` | `<このリポジトリ>/../searxng` |
| `SEARXNG_URL` | `http://127.0.0.1:8888` |
| `OLLAMA_HOST` | `http://localhost:11434` |
| `OLLAMA_SEARCH_MODEL` | `hauhau-aggressive:iq2m` |

`settings-local.yml` と `sitecustomize.py` と `start-searxng.ps1` は
**SearXNG 側に配置する原本**（それぞれ checkout 直下と
`.venv/Lib/site-packages/` へコピーする）。

**`settings-local.yml` の `secret_key` は書き換えること。**
127.0.0.1 に閉じている限り実害は無いが、公開リポジトリに載っている値をそのまま
使うことになる。
