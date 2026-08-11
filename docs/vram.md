# VRAM とモデルの載せ方（実測）

**2026-08-11 の実測。構成が変われば値は変わるので、判断を左右するなら測り直すこと。**
測ったマシンは RTX 5060 Ti 16GB（`nvidia-smi` の total は 16311 MiB = 15.93 GiB）、
Ollama 0.32.7、モデルは `hauhau-aggressive:iq2m`（Qwen3.6-35B-A3B Uncensored / IQ2_M）。

## 結論を先に

**`OLLAMA_FLASH_ATTENTION=1` を入れること。** これだけで載る。

| 設定（`num_ctx 40960`） | `size_vram` |
|---|---|
| 既定（flash attention なし） | **17.40 GiB** ← 16GB のカードに入らない |
| **`OLLAMA_FLASH_ATTENTION=1`** | **11.84 GiB** |
| ＋ `OLLAMA_KV_CACHE_TYPE=q8_0` | 11.53 GiB |

**5.56 GiB 減って、重みの精度は一切落ちない。** 減っているのはアテンションの計算バッファ。

`OLLAMA_KV_CACHE_TYPE=q8_0` は**足しても 0.31 GiB しか減らない**ので入れていない。
しかも flash attention が前提で、無いと llama-server が
`quantized V cache requires flash attention` で落ちる。

`start.bat` / `webui.bat` が設定してから Ollama を起動する。
ただし **env は自分が起動したときにしか効かない。** デスクトップアプリがログイン時に
自動起動していると既に動いているので効かず、bat がその旨を出す。
**恒久的にするならユーザー環境変数に置くこと。**

## 測り方

```python
# unload -> options.num_ctx を指定して1発投げる -> /api/ps を読む
post("/api/chat", {"model": M, "messages": [], "keep_alive": 0})
post("/api/chat", {"model": M, "options": {"num_ctx": ctx},
                   "messages": [{"role": "user", "content": "hi"}], "stream": False})
get("/api/ps")["models"][0]["size_vram"]
```

`options.num_ctx` は**リクエストごと**に効くので、Modelfile を書き換えなくても振れる。
ただし値が変わるとモデルは載せ直しになる（1回あたり10〜60秒）。

## `num_ctx` を下げてもあまり効かない

flash attention なしで振った結果:

| `num_ctx` | `size_vram` |
|---|---|
| 40960 | 17.40 GiB |
| 32768 | 16.98 |
| 24576 | 16.56 |
| 16384 | 16.14 |
| 8192 | 15.71 |
| 4096 | 15.50 |

**全域振っても 1.9 GiB しか動かない。** 文脈を 1/10 にしてもカードに収まらない。
つまり **KV キャッシュはこのモデルでは主役ではない。**
`num_ctx` を削るのは flash attention を入れてもまだ足りないときの最後の手段。

> 最初に立てた「`num_ctx` を下げれば載る」という筋は**外れた**。
> 1点の観測（`size_vram` が大きい）から原因を決めつけず、
> **要因を1つずつ振って確かめる**こと。

## `nvidia-smi` の使用量では溢れを判定できない

**上の6条件すべてで `nvidia-smi` は約 15,500 MiB を示した**（`num_ctx 4096` でも）。
アロケータが使える分を使い切るので**常に天井付近に張り付く**。

**見るべきは `/api/ps` の `size_vram`。** こちらは条件に応じて素直に動く。

flash attention を入れた後は `nvidia-smi` も 13.2〜14.0 GB まで下がったので、
張り付いていたのは本当に溢れていたから。

## `size_vram` がカードの容量を超えることがある

flash attention なし・`num_ctx 40960` で:

```
size_vram = 17.40 GiB      ← Ollama の申告
物理 VRAM = 15.93 GiB      ← nvidia-smi
```

**16GB のカードに 17.4GB は物理的に入らない。** Windows のドライバは溢れた分を
**共有システムメモリ（RAM）に逃がす**ので、ロード自体は成功する。
載ったように見えて、実は一部が PCIe の向こうにいる状態。

web の画面のインジケーターはこの状態を `(カードの容量超過)` と出す。

**「だから遅い」とまでは測っていない。** 言えるのは「物理 VRAM には収まっていない」まで。

## OOM のときに出るメッセージ

`str(HTTPError)` は `HTTP Error 500: Internal Server Error` までしか言わない。
**理由は本文にある。** `ollama-search.py` の `_ollama_error()` が本文を読んで、
`out of memory` を見つけたら次に見る場所を添える。

```
Ollama が HTTP 500 を返した: llama-server ... cudaMalloc failed: out of memory
  VRAM が足りない。空きは `nvidia-smi` で見る。
  他のアプリを閉じるか、num_ctx の小さいモデルに切り替えること。
```

`llama-server startup failed after projector CPU offload retry` という前置きが付くのは、
**このモデルがビジョンエンコーダを積んでいる**ため（下記）。

## このモデルはビジョンエンコーダを積んでいる

```
capabilities: ['tools', 'thinking', 'completion', 'vision']
clip.has_vision_encoder = True
clip.projector_type = qwen3vl_merger
```

HF 上の mmproj は 0.84 GiB。**web 検索エージェントには一切要らない。**
外せば浮くはずだが、**flash attention で足りたので試していない**（未計測）。

## さらに小さい量子化は必要なかった

`HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive` の 12個のうち、
**IQ2_M（10.86 GiB）が最小**。他は全部大きい。

| | サイズ |
|---|---|
| **IQ2_M** | **10.86 GiB** ← 常用 |
| Q2_K_P | 13.95 |
| IQ3_M | 14.38 |
| IQ4_XS 〜 Q8_K_P | 17.44 〜 40.61 |

もっと小さいものが要るなら
`mradermacher/Qwen3.6-35B-A3B-Uncensored-Aggressive-i1-GGUF` に IQ1_S（6.97 GiB）まである。
**ただし `base_model` が `prithivMLmods/Qwen3.6-35B-A3B-Uncensored-Aggressive` で、
HauhauCS とは別の作者。量子化違いではなく別モデルへの乗り換えになる**
（mmproj は無いので、そのぶんは軽い）。

**flash attention で 4GB 空いたので、この乗り換えは不要になった。**

## 終了してもサービスは残る

`start.bat` は `start` で切り離して起動するので、launcher が終わっても生き続ける。
次回起動が一瞬で済むのでこれは意図どおり。ただし:

| 残るもの | コスト |
|---|---|
| Ollama（2プロセス） | RAM 約98MB |
| SearXNG（`searxng` バックエンド時のみ） | RAM 約94MB + 最小化されたコンソール窓 |
| **VRAM** | **最後の質問から約5分で自動解放** |

RAM は無視できるが **VRAM は無視できない**。**Forge や ComfyUI に移る前は
`stop.bat` を叩く**こと。5分待てば自動で解放されるので、急がないなら放っておいてもよい。

`stop.bat` は **8888 を LISTEN しているポートから PID を引いて**落とす。
イメージ名で `python.exe` を殺すと ComfyUI や他のスクリプトを巻き込むため。
