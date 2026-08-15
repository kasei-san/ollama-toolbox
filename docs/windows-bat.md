# `.bat` を書いていて踏んだ罠

**全部「黙って失敗する」類。** `start.bat` / `webui.bat` 内にもコメントで残してある。
launcher を触るときはここを読んでから。

## `start` は `/MIN` をタイトルより先に置く

`start "title" /MIN prog` だとタイトルをコマンド扱いして
`ファイル \title\ が見つかりません` になる。

## `start` に `/D` が要る

作業ディレクトリが SearXNG の checkout でないと `python -m searx.webapp` が
`ModuleNotFoundError` で即死する。**errorlevel は 0 のまま**で、
待ちループが空回りするだけで理由が出ない。

## `timeout.exe` を使わない

stdin がリダイレクトされていると `Input redirection is not supported` で死ぬ。
Git Bash から起動すると GNU coreutils の `timeout` に解決されて `/t` を拒否する。
**`ping -n 2 127.0.0.1` で待つ。**

## `OLLAMA_MODELS` はデスクトップアプリの設定に負ける

**Ollama 0.32.7 / 2026-08-15 に特定。挙動もスキーマも版に依存するので、
判断を左右するなら測り直すこと**（`ollama --version`）。

`ollama list` が空になり、全リクエストが 404 `model not found` になることがある。
**モデルは消えていない。サーバが別のディレクトリを見ているだけ。**

デスクトップアプリは自分の設定を `%LOCALAPPDATA%\Ollama\db.sqlite` の
`settings` テーブルの `models` 列に持っている。**`ollama app.exe` が `serve` を
起動するとき、この値でユーザー環境変数 `OLLAMA_MODELS` を上書きする。**

`start.bat` / `webui.bat` はどちらも `ollama app.exe` を叩くので、
**bat から起動した場合はアプリ設定が勝つ。** bat 側で `set OLLAMA_MODELS` しても届かない。

### 実測: 環境変数が勝った起動は1回も無かった

ローテートされた `server-N.log` に残っていた、各起動時に効いた値
（ユーザー環境変数はこの全期間 `E:\llm\ollama` のまま）:

| 起動 | 効いた `OLLAMA_MODELS` |
|---|---|
| 2026-08-11 22:26 | `E:\ollama\models` |
| 2026-08-13 13:51 | `E:\ollama\models` |
| 2026-08-15 10:38 | `E:\ollama\models` |
| 2026-08-15 10:48 | `E:\ollama\models` |
| 2026-08-15 21:20 | `E:\ollama\models` |
| 2026-08-15 21:28（Settings 変更後） | **`E:\llm\ollama`** |

> **以前ここには「ユーザー環境変数が勝っている」と書いてあった。逆だった。**
> 環境変数側のパスでモデルが見えていた時期があったので、そちらが勝つと誤読していた。
> 上の表のとおり、**アプリ経由の起動で環境変数が勝った記録は無い。**
> 見えていた時期は、アプリを経由せず `serve` を直接立てていたと考えるのが筋
> （ログの保持は5世代なので、それ以前の直接の証拠は残っていない）。

**上書きが確認できたのは `OLLAMA_MODELS` だけ。**
同じ起動で `OLLAMA_FLASH_ATTENTION` は素通しで届いていた。
**確かめたのはこの2つだけで、他の設定項目は未確認。**
「環境変数が丸ごと効いていない」と読むと原因を外す。

### 診断は server.log の最後の `server config` 行

`%LOCALAPPDATA%\Ollama\server.log` の `msg="server config"` 行に**実際に効いた値**が出る。
**追記されるので、必ず最後の1行を見ること**（起動のたびに増える）。
シェルで `echo %OLLAMA_MODELS%` を見ても、それはサーバの設定ではない。

```
# 最後の msg="server config" 行から抜粋（バックスラッシュの重なりは実ログのまま）
OLLAMA_MODELS:E:\\ollama\\models   <- アプリ設定に上書きされた値（意図と違う）

# 数行あとの別の行。実体が無いことの裏付け
msg="total blobs: 0"
```

**効いている設定値は `server.log`、モデル実体の所在は `ollama show --modelfile`** と
見るところを分ける。

### 直し方

**アプリの Settings → Model location を正しいパスに変える。**
変更すると `serve` が再起動して反映される。反映されたかは pid が変わったことで確認できる:

```powershell
Get-Process ollama -ErrorAction SilentlyContinue | Select-Object Id,StartTime
```

**アプリを経由せず `ollama serve` を直接立てれば環境変数が効く**が、
デスクトップアプリが動いていると `serve` を監視して自分の設定で立て直すので、
逃げ道にするなら `ollama app.exe` を先に落とすこと。

## 環境変数は「自分が起動したとき」しか効かない

`start.bat` / `webui.bat` は Ollama が既に応答していれば起動を飛ばす。
そのとき `OLLAMA_FLASH_ATTENTION` などを設定しても**何も起きない**。
黙って効かないのが一番たちが悪いので、bat がその旨を出すようにしてある。

**恒久的にしたいならユーザー環境変数に置くこと。**

## `.bat` は純ASCIIで書く

cmd は OEM コードページ（cp932）で読むので日本語を埋めると化ける。
**日本語出力は Python 側に任せる。**
