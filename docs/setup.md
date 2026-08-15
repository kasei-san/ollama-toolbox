# セットアップ: Ollama と Qwen3.6 Uncensored を入れる

**このファイルだけ読めば、まっさらな Windows 機で `ollama-toolbox` が動くところまで行ける。**
人間が読んでもエージェント（Claude Code 等）が実行してもいいように、
各手順に**そのまま貼れるコマンド**と**期待される出力**を書いてある。

* 検索バックエンドの設定は README を見ること。この文書は **Ollama とモデル**だけを扱う
* すべて 2026-08-11 に Windows 10 + RTX 5060 Ti 16GB で実施した内容。
  **バージョンや数値は必ず自分の環境で確認し直すこと**

---

## 0. 前提と、事前に決めること

| 項目 | このガイドの前提 |
|---|---|
| OS | Windows 10 / 11 |
| GPU | VRAM **12GB 以上**を推奨（16GB で検証済み） |
| 空きディスク | **15GB 以上**（モデル 12GB + 余裕） |
| シェル | PowerShell（コマンドは PowerShell 前提で書く） |

**VRAM が 16GB 未満なら、手順4の `num_ctx` を下げる必要がある。** 目安は手順6。

---

## 1. Ollama を入れる / 更新する

### 1-1. 現状を確認

```powershell
ollama --version
```

* **コマンドが見つからない** → 未インストール。1-2 へ
* **バージョンが出る** → 表示された版が古いなら 1-2 で上書きインストール

> **なぜ更新が要るか**: Qwen3.6 は線形注意とフル注意のハイブリッドという新しい構成で、
> **古い Ollama では読めない**（0.24.0 では読めなかった。0.32.7 で動いた）。
> 「モデルが壊れている」ように見えたら、まず `ollama --version` を疑うこと。

### 1-2. インストール

https://ollama.com/download から `OllamaSetup.exe` を取得して実行する。
サイレントで入れるなら:

```powershell
Start-Process .\OllamaSetup.exe -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait
```

> **エージェント向けの注意**: Git Bash からこれを叩くと `/VERYSILENT` が
> MSYS の引数変換で `C:/Program Files/Git/VERYSILENT` というパスに化け、
> **フラグが無視されて GUI が開いたまま無限に待つ**。終了コードは 0 のまま、
> CPU もほぼ 0 で止まる。見分け方は「プロセスに `MainWindowTitle` が付いている」こと。
> **必ず PowerShell から `Start-Process` で渡すこと。**

### 1-3. 確認

```powershell
ollama --version
nvidia-smi --query-gpu=name,memory.total --format=csv
```

期待される出力の例:

```
ollama version is 0.32.7
name, memory.total [MiB]
NVIDIA GeForce RTX 5060 Ti, 16311 MiB
```

---

## 2.（任意）モデルの置き場を変える

C: 以外に置きたい場合だけ。**不要ならこの節は飛ばす。**

**設定箇所は2つあり、両方を同じパスに合わせる。** `start.bat` / `webui.bat` は
`ollama app.exe` を経由して Ollama を起動するので、**アプリ側の設定が勝つ**。
環境変数だけ変えても効かない（機序と実測は → `docs/windows-bat.md`）。

まずアプリ側:

* **Settings → Model location** を目的のパスに変える

次に環境変数（`ollama serve` を直接立てるときに効く）:

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS','E:\llm\ollama','User')
```

既にモデルがあるなら移動する（`robocopy /MOVE` はドライブをまたいでも安全）:

```powershell
robocopy "$env:USERPROFILE\.ollama\models" "E:\llm\ollama" /E /MOVE
```

設定後は **Ollama を再起動する**。そのうえで確認:

```powershell
ollama list
```

> **ハマりどころ**: 2つが食い違っていると **`ollama list` が空になり、
> 全リクエストが 404 "model not found" になる**。
> モデルが消えたように見えても、ディスク上には無事にある。
> どちらが効いたかは `%LOCALAPPDATA%\Ollama\server.log` の
> **最後の** `msg="server config"` 行で確認する。

---

## 3. モデルを取得する

**HuggingFace 本家から直接引く**（第三者の再アップロードより本家を優先）:

```powershell
ollama pull hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:IQ2_M
```

12GB あるので回線次第で数分〜十数分かかる。

> **進捗が出ないとき**: バックグラウンド実行だと**出力がバッファされて 0 バイトのまま**進む。
> 失敗したと誤認しやすい。前面で実行するか、モデルディレクトリのサイズか
> `blobs/*partial*` の有無で見ること。

### quant の選び方

| quant | サイズ | bpw | 16GB VRAM だと |
|---|---|---|---|
| `IQ2_M` | 11GB | 2.69 | **収まる（本ガイドの既定）** |
| `IQ3_M` | 15GB | 3.56 | 溢れる |
| `IQ4_XS` | 19GB | 4.32 | 溢れる |
| `Q4_K_M` | 21GB | 4.88 | 溢れる |

* **溢れても動く。遅くなるだけ**（自動で CPU にオフロードされる）
* `_K_P` 系は作者独自の quant。標準の ggml 型（`IQ2_M` `IQ4_XS` 等）を選べば
  エンジン互換のリスクを避けられる

---

## 4. 使うための別名を作る（**この手順は飛ばさない**）

素の `hf.co/...` をそのまま使うと **context が既定の 4096 に落ちる**。
`num_ctx` と、日付まわりの SYSTEM を焼いた別名を作る。

`Modelfile` という名前でファイルを作る:

```
FROM hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:IQ2_M
PARAMETER num_ctx 40960
SYSTEM """You do not know today's date. Your knowledge has a training cutoff and may be out of date.

If a question depends on current, recent, or real-time information and a search tool is available, call it instead of answering from memory.

When you call a search tool, NEVER add a year or a date to the query unless the user explicitly gave one. Search for the topic alone and let the search engine return the most recent results."""
```

```powershell
ollama create hauhau-aggressive:iq2m -f Modelfile
```

> **ディスクは増えない。** `FROM` に**モデル名**を書けば blob が共有される。
> `FROM <blob の絶対パス>` と書くと**再インポートになって実体が重複する**
> （12GB のモデルで +10.85GB 食った）。

### SYSTEM の中身の根拠

このモデルの**内部の日付感覚は 2024年5月で止まっている**。放っておくと
`AI latest news 2024` のようなクエリを打ち、**古い記事を「最新」として自信たっぷりに答える**。

* **今日の日付を教えるのは逆効果。** SYSTEM で日付を渡すと「自分は現在を把握している」と
  誤認し、**検索せずに記憶で答えるようになる**（3回試して3回とも失敗）
* 正解は**「クエリに年を足すな」という禁止**。ただし**これでも4〜6割しか効かない**ので、
  残りは `ollama-search.py` 側で機械的に潰している（README 参照）

---

## 5. 動作確認

```powershell
ollama run hauhau-aggressive:iq2m "Reply with exactly: OK"
```

**初回は 50秒ほどかかる**（12GB を VRAM に読み込むため）。2回目以降は数秒。

### GPU に全部載っているかを確認する

```powershell
ollama ps
```

期待される出力:

```
NAME                      SIZE     PROCESSOR    CONTEXT
hauhau-aggressive:iq2m    18 GB    100% GPU     40960
```

* **`100% GPU` が出れば成功。**
* `23%/77% CPU/GPU` のように出たら溢れている → 手順6で `num_ctx` を下げる

> **`nvidia-smi` の VRAM 使用量では判定できない。** アロケータは使える分を使い切るので、
> **ピークは常に天井付近（15.7〜15.9GB / 16.3GB）を示し、設定を変えても動かない**。
> 溢れているかどうかは **`ollama ps` の PROCESSOR 列**でしか分からない。

---

## 6. VRAM に合わせて `num_ctx` を決める

RTX 5060 Ti 16GB / IQ2_M での実測:

| `num_ctx` | PROCESSOR | 速度 |
|---|---|---|
| 4096〜32768 | 100% GPU | 88〜91 tok/s |
| **40960** | **100% GPU** | **92 tok/s** |
| 45056 | 3%/97% CPU/GPU | 69 tok/s |
| 65536 | 9%/91% CPU/GPU | 56 tok/s |
| 131072 | 23%/77% CPU/GPU | 28 tok/s |

**40960 が 100% GPU を保てる上限。超えると即3割落ちる。**
モデル自体は 262K context を張れるが実用にならない。

VRAM が 16GB 未満なら、`100% GPU` が出るまで `num_ctx` を下げて手順4をやり直す。
`ollama create` は blob を共有するので、作り直しは一瞬でディスクも増えない。

---

## 7. トラブルシュート

| 症状 | 原因と対処 |
|---|---|
| `ollama list` が突然空。全部 404 | **デスクトップアプリの設定が `OLLAMA_MODELS` を上書きしている。** `server.log` の最後の `msg="server config"` で効いている値を確認し、アプリの Settings → Model location を直す。手順2を参照。**モデルはディスク上には残っている** |
| pull したのに別の場所に落ちた | アプリ設定と環境変数が食い違っていて、**pull 時に効いていたのが期待と別の側だった**（アプリ経由で起動したか、`serve` を直接立てたかで変わる）。`ollama show --modelfile <model>` で blob の実際のパスを確認する。※上の行と同じ原因と見ているが、この症状単体での切り分けはしていない |
| モデルが読めない / アーキ非対応と言われる | Ollama が古い。`ollama --version` を確認して更新する |
| `ollama create` でディスクが激増した | `FROM` に blob の絶対パスを書いている。**モデル名**を書くこと |
| `ollama show --modelfile` の `TEMPLATE` が `{{ .Prompt }}` になっている | **表示が嘘。** 実際は GGUF 内蔵の jinja が効いている。system prompt が効くか実際に投げて確かめる方が早い |
| 応答が遅い | `ollama ps` の PROCESSOR を見る。`100% GPU` でなければ `num_ctx` を下げる |

---

## 8. これは何のモデルか

[HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive](https://huggingface.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive)

* ベースは `Qwen/Qwen3.6-35B-A3B`。MoE 256エキスパート / 8ルーティング、
  総パラメータ 34.7B に対し**アクティブは約 3B**
* **線形注意とフル注意の 3:1 ハイブリッド**40層。native 262K context
* capabilities: tools / thinking / completion / **vision**（CLIP projector 446.57M）
* abliterated（refusal 除去）版。作者は 0/465 refusals と主張。**こちらでは未検証**
* 日本語は IQ2_M（2.69bpw）でも自然

**ハイブリッド注意のおかげで KV キャッシュが小さい。** 40層のうち KV を持つのは
10層だけで、残り30層は context 長に依らず 62.81MiB の定数。
同規模のフル注意モデルの 1/4 程度。
