# SearXNG バックエンド

**`brave` か `ddgs` を使うならこのファイルは丸ごと不要。**
`searxng` だけが別プロセスを要求する。

## 配置

SearXNG がこのリポジトリと並んでいることを既定とする:

```
<親ディレクトリ>/
  searxng/          <- SearXNG の checkout（venv 込み）
  ollama-toolbox/   <- このリポジトリ
```

別の場所に置くなら `SEARXNG_DIR` / `SEARXNG_URL` で上書きする。

このリポジトリの `settings-local.yml` / `sitecustomize.py` / `start-searxng.ps1` は
**SearXNG 側に配置する原本**。それぞれ checkout 直下と
`.venv/Lib/site-packages/` へコピーする。

**`settings-local.yml` の `secret_key` は書き換えること。**
127.0.0.1 に閉じている限り実害は無いが、公開リポジトリに載っている値をそのまま
使うことになる。

## Windows で動かすための細工

Docker も WSL も使っていない。**SearXNG は Linux 前提だが、依存に uwsgi も uvloop も
無いので Windows の Python でそのまま動く。** ただし2箇所だけ細工が要る。

### 1. `utils/templates/` を sparse-checkout で除外する

4ファイルが名前に `:` を含み（`searxng.conf:socket`）NTFS で作れず、
**clone がツリー全体ごと失敗する**。

```sh
git clone --depth 1 https://github.com/searxng/searxng.git ../searxng
cd ../searxng
git sparse-checkout set --no-cone '/*' '!/utils/templates/*'
git checkout -- .
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

### 2. `sitecustomize.py` を venv に置く

`searx/valkeydb.py` が `pwd` と `os.getuid()` を無条件に呼ぶ
（Valkey 接続失敗時のログ用）。**リポジトリ本体を書き換えると `git pull` で壊れる**ので
venv 側に置く。

### JSON 出力は既定で無効

`settings-local.yml` の `search.formats` で有効化している。

## 実際に使われているエンジンは2つだけ（2026-08-11 実測）

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

## つまり実質シングルポイント

**結果の75%が google cse 1本に依存している。**
「検索結果が0件」で打ち切られる事象はこの脆さが原因と思われる。頻発するなら:

* bing / mojeek が無言で0件な理由を調べて直す
* `settings-local.yml` の `engines:` で**動くエンジンだけに絞る**（無駄な待ちが減り速くもなる）
* Brave Search API のキーを取る（→ `brave` バックエンドに乗り換えるほうが早い）

**「Google 依存を避けたい」が動機なら SearXNG は逆効果。**
経緯は [`search-backends.md`](search-backends.md)。

## 常駐化していない

`start.bat` が `SEARCH_BACKEND=searxng` のときだけ都度起動する。
初回はエンジンを全部読むので 90 秒まで待つ。

## 既知の粗さ

* 常駐化していない（`start.bat` が都度起動する）
* 結果の75%が `google cse` 1本に依存している（上記）
