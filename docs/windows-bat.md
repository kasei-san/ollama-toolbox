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

`ollama list` が空になり、全リクエストが 404 `model not found` になることがある。
**モデルは消えていない。サーバが別のディレクトリを見ているだけ。**

デスクトップアプリは自分の設定（`%LOCALAPPDATA%\Ollama\db.sqlite` の `settings.models`）
を持っていて、**`ollama app.exe` が `serve` を起動するときに、この値で
ユーザー環境変数 `OLLAMA_MODELS` を上書きする。**

`start.bat` / `webui.bat` はどちらも `ollama app.exe` を叩くので、
**bat から起動した場合は必ずアプリ設定が勝つ。** bat 側で `set OLLAMA_MODELS` しても届かない。

> **以前ここには「ユーザー環境変数が勝っている」と書いてあった。逆だった。**
> 環境変数側のパスで動いていた時期があったので、そちらが勝つと誤読していた。
> 実際は「アプリ設定と環境変数がたまたま同じ場所を指していた」か、
> アプリを経由せずに `serve` が立っていたか、のどちらか。

**上書きされるのは models だけ。** 同じ起動で `OLLAMA_FLASH_ATTENTION` は素通しで届いていた。
「環境変数が丸ごと効いていない」と読むと原因を外す。

### 診断は server.log

`%LOCALAPPDATA%\Ollama\server.log` の `msg="server config"` 行に**実際に効いた値**が出る。
ここが唯一の権威。シェルで `echo %OLLAMA_MODELS%` を見ても、それはサーバの設定ではない。

```
OLLAMA_MODELS:E:\\ollama\\models   <- 効いている値
msg="total blobs: 0"               <- 実体が無い証拠
```

**直し方はアプリの Settings -> Model location。**
変更すると `serve` が再起動して反映される（pid が変わるので確認できる）。

## 環境変数は「自分が起動したとき」しか効かない

`start.bat` / `webui.bat` は Ollama が既に応答していれば起動を飛ばす。
そのとき `OLLAMA_FLASH_ATTENTION` などを設定しても**何も起きない**。
黙って効かないのが一番たちが悪いので、bat がその旨を出すようにしてある。

**恒久的にしたいならユーザー環境変数に置くこと。**

## `.bat` は純ASCIIで書く

cmd は OEM コードページ（cp932）で読むので日本語を埋めると化ける。
**日本語出力は Python 側に任せる。**
