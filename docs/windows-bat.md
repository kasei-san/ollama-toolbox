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

## `OLLAMA_MODELS` を明示する

設定より前に起動したシェルから叩くと Ollama が既定のモデルディレクトリを見て
`ollama list` が空になり、全リクエストが 404 `model not found` になる。

**このマシンでは実際に踏んだ。** デスクトップアプリは自分の設定
（`%LOCALAPPDATA%\Ollama\db.sqlite` の `models` 列）を持っていて、
そこには別のパスが入っている。動いているのは**ログイン環境のユーザー環境変数
`OLLAMA_MODELS` が勝っているから**であって、環境変数の無いシェルから
アプリを起動し直すとモデルが見えなくなる。

## 環境変数は「自分が起動したとき」しか効かない

`start.bat` / `webui.bat` は Ollama が既に応答していれば起動を飛ばす。
そのとき `OLLAMA_FLASH_ATTENTION` などを設定しても**何も起きない**。
黙って効かないのが一番たちが悪いので、bat がその旨を出すようにしてある。

**恒久的にしたいならユーザー環境変数に置くこと。**

## `.bat` は純ASCIIで書く

cmd は OEM コードページ（cp932）で読むので日本語を埋めると化ける。
**日本語出力は Python 側に任せる。**
