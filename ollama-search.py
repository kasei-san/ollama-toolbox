#!/usr/bin/env python3
"""ollama-search.py -- ローカル Ollama モデルに web 検索をさせる最小エージェント。

  python ollama-search.py "日銀の直近の決定は？"
  python ollama-search.py            # 対話モード（exit / quit / 終了 で抜ける）
  python ollama-search.py -f "..."   # 検索を強制（モデルの判断に任せない）
  python ollama-search.py --no-think # 思考を切る（速いが精度は落ちる）

対話モードは**会話の文脈を引き継ぐ**。`/api/chat` はステートレスなので、
履歴はクライアント側で積んで毎回まとめて送る（`Conversation`）。

思考の中身は stderr にリアルタイムで流す。答えは stdout なので、
`... 2>nul` で思考だけ捨てられるし、`... >out.txt` で答えだけ拾える。

検索バックエンドは3つ。`SEARCH_BACKEND` で切り替える。

  brave   Brave Search API（公式）。**BRAVE_API_KEY があればこれが既定**
  ddgs    pip の ddgs。キーが無いときの既定。bing/brave/yandex をスクレイプ
  searxng 自前で立てた SearXNG（別プロセスが要る。start.bat が面倒を見る）

キーは隣の `.env`（.gitignore 済み）から読む。既存の環境変数があればそちらが優先。

いずれも **ollama.com の web search API は使わない**。あれは無料で手軽だが、
検索クエリが ollama.com に送られる。モデルの推論は元々ローカル完結なので
外に出るのは「何を検索したか」だけだが、それも出したくないという判断。

なぜ強制オプションがあるか:
  「日本の首相は？」のような質問で、モデルは4〜6割の確率で検索せず記憶から答える
  （実測。SYSTEM プロンプトで禁じても完全には直らない）。
  確実に最新が要る場面では -f を使う。
"""
import argparse, io, json, os, re, sys, urllib.error, urllib.parse, urllib.request

# Windows のコンソールは cp932 なので、明示的に UTF-8 で出す
for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if hasattr(_f, "buffer"):
        setattr(sys, _s, io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))

# パイプで流し込まれた stdin も cp932 として読まれ、日本語が壊れる
# （実際モデルが "garbled characters (likely mojibake)" と指摘して発覚した）。
# **端末から直接打つときは触らない**: その場合 Windows の Python はコンソール API
# 経由で Unicode を正しく読むので、UTF-8 を強制すると逆に壊す。
if not sys.stdin.isatty() and hasattr(sys.stdin, "buffer"):
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")


class SearchFailed(Exception):
    """検索が失敗した。モデルに記憶で答えさせないため、ここで打ち切る。"""


def _load_dotenv():
    """隣の .env を読む。**既存の環境変数は上書きしない**（そちらが優先）。

    python-dotenv を足すほどのことではないので自前。`.env` は .gitignore 済み。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("OLLAMA_SEARCH_MODEL", "hauhau-aggressive:iq2m")
SEARXNG = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")
MAX_ROUNDS = 4
FETCH_CHARS = 6000

# 履歴に回せる文脈の割合。残りは今のターン（質問＋最大 MAX_ROUNDS 回分の検索結果
# ＋回答）のために空けておく。1件の tool 結果だけで 8000 文字積むので、
# 半分空けておかないと今のターンの途中で溢れる。
HISTORY_CTX_RATIO = 0.5
# num_ctx が取れなかったときの保険。Ollama が PARAMETER 無しのモデルに使う既定値。
DEFAULT_NUM_CTX = 4096

# 検索バックエンド: "brave" / "ddgs" / "searxng"
# **BRAVE_API_KEY があれば brave を優先する。** 公式 API なのでスクレイピングと違い
# レート制限や HTML 変更で壊れない。キーが無ければ ddgs に落ちる。
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
BACKENDS = ("brave", "ddgs", "searxng")
BACKEND = os.environ.get(
    "SEARCH_BACKEND", "brave" if BRAVE_API_KEY else "ddgs").strip().lower()
if BACKEND not in BACKENDS:
    sys.exit(f"SEARCH_BACKEND={BACKEND!r} は無効。{'/'.join(BACKENDS)} のいずれかを指定すること。")

# ddgs が内部で叩くエンジン。2026-08-11 の実測で**結果を返したのはこの3つだけ**:
#   動く    : bing / brave / yandex
#   0件で死ぬ: duckduckgo / google / mojeek / yahoo / startpage / wikipedia
# duckduckgo 本体が死んでいるのは皮肉だが、**google も同じく死んでいる**ので、
# 結果として Google 依存からは完全に離れられている。
# "auto" にすると ddgs が勝手に選ぶ。明示した方が挙動が読めるので既定は明示。
DDGS_BACKEND = os.environ.get("DDGS_BACKEND", "bing,brave,yandex")

# セーフサーチ。**バックエンドごとに語彙が違う**ので、ここで正準語を決めて変換する:
#   このツール : off / moderate / strict
#   ddgs      : off / moderate / on      ← 最も厳しいのが "on"。"strict" は無効値
#   SearXNG   : 0   / 1        / 2
# ddgs に "strict" を渡しても例外にならず素通しされるだけなので、
# **無効値は黙って通る**。だから受け取った時点で検証して落とす。
#   Brave     : off / moderate / strict  ← 正準語とそのまま一致
SAFESEARCH_MAP = {
    "off":      {"ddgs": "off",      "searxng": 0, "brave": "off"},
    "moderate": {"ddgs": "moderate", "searxng": 1, "brave": "moderate"},
    "strict":   {"ddgs": "on",       "searxng": 2, "brave": "strict"},
}
SAFESEARCH = os.environ.get("SEARCH_SAFESEARCH", "off").strip().lower()
if SAFESEARCH not in SAFESEARCH_MAP:
    # 黙って既定に戻すと、`stict` のようなタイポで「厳しくしたつもりが素通し」に
    # なる。安全側に倒れないフォールバックはしない。
    sys.exit(f"SEARCH_SAFESEARCH={SAFESEARCH!r} は無効。"
             f"{'/'.join(SAFESEARCH_MAP)} のいずれかを指定すること。")

TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for current, recent or real-time information.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "search query"},
            "max_results": {"type": "integer", "description": "1-10, default 5"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "web_fetch",
        "description": "Fetch the full text of one URL found via web_search.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]}}},
]


# 打ち切り時にモデルへ渡す指示。履歴に残す価値が無いので、識別できるよう定数にする。
STOP_SEARCHING = ("Stop searching. Answer now using only the search results above. "
                  "If they are insufficient, say so explicitly.")


# --- 進捗の出し先 -----------------------------------------------------------
# 途中経過は `emit(kind, payload)` に流す。CLI は stderr に出し、web UI は SSE で
# ブラウザに送る。**出し先を知っているのは emitter だけ**にして、エージェント側は
# 誰に向かって喋っているのかを知らずに済ませる。
#   think    思考の断片        content  答えの断片
#   tool     ツール呼び出し     result   ツールの結果
#   note     こちらからの注記   endturn  ターンの終わり（表示を閉じるため）

def noop_emitter(kind, payload):
    return None


def cli_emitter(verbose=True):
    """CLI 用の出し先。**答え（stdout）と混ぜない**ので全部 stderr。

    思考は改行だらけなので字下げを保って読めるようにする。
    ブロックを閉じる改行が要るので、開いているかどうかを覚えている。
    """
    if not verbose:
        return noop_emitter
    state = {"think_open": False}

    def emit(kind, payload):
        if kind == "think":
            if not state["think_open"]:
                print("  [think] ", end="", file=sys.stderr, flush=True)
                state["think_open"] = True
            print(payload.replace("\n", "\n          "),
                  end="", file=sys.stderr, flush=True)
            return
        if state["think_open"]:                    # think 以外が来たら閉じる
            print("\n", file=sys.stderr, flush=True)
            state["think_open"] = False
        if kind == "tool":
            print(f"  [{payload['name']}] "
                  f"{json.dumps(payload['args'], ensure_ascii=False)}",
                  file=sys.stderr)
        elif kind == "note":
            print(f"  [{payload}]", file=sys.stderr)
        # content（答えの逐次）と result（結果の中身）は CLI では出さない。
        # 答えは ask() の戻り値でまとめて stdout に出る。

    return emit


def loaded_num_ctx(model=None):
    """ロード済みモデルに**実際に確保された** num_ctx。未ロードなら None。

    `/api/ps` の `context_length` が唯一の実測値。ロード後にしか取れないので
    起動時は使えないが、取れたときはこれを信じる。
    """
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:                                       # noqa: BLE001
        return None
    for m in data.get("models") or []:
        if m.get("name") == (model or MODEL) and m.get("context_length"):
            return int(m["context_length"])
    return None


def detect_num_ctx(model=None):
    """モデルに効くはずの num_ctx を Ollama から引く。取れなければ既定値。

    **`model_info` の `<arch>.context_length` を使ってはいけない。** あれは
    アーキテクチャ上の最大（このモデルなら 262144）で、Ollama が実際に確保する量
    ではない。効いているのは Modelfile の `PARAMETER num_ctx`（40960）の方。
    取り違えると「まだ余裕がある」と誤認して溢れる。
    """
    try:
        data = post(f"{OLLAMA}/api/show", {"model": model or MODEL}, {}, 30)
    except Exception:                                       # noqa: BLE001
        return DEFAULT_NUM_CTX
    for line in (data.get("parameters") or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "num_ctx" and parts[1].isdigit():
            return int(parts[1])
    # PARAMETER が無いモデルでは Ollama 側の既定。0.32.7 では 4096（`/api/ps` で確認）
    # だが**バージョンで変わりうる**ので、ロードできたら実測値で上書きする。
    env = os.environ.get("OLLAMA_CONTEXT_LENGTH", "").strip()
    return int(env) if env.isdigit() else DEFAULT_NUM_CTX


def _chars(messages):
    """メッセージ群のおおよその文字数。トークン量の当たりを付けるためだけのもの。"""
    n = 0
    for m in messages:
        n += len(m.get("content") or "") + len(m.get("thinking") or "")
        if m.get("tool_calls"):
            n += len(json.dumps(m["tool_calls"], ensure_ascii=False))
    return n


def _without_tools(turn):
    """ターンからツールのやり取りだけ落とす。会話の筋（質問と回答）は残す。

    **`role: tool` と、それを呼んだ assistant の `tool_calls` は必ずセットで
    落とす。** 片方だけ残すと、応答の無いツール呼び出しや呼ばれていないツール結果が
    履歴に残り、モデルが混乱する（テンプレートによっては生成自体が壊れる）。
    """
    out = []
    for m in turn:
        if m.get("role") == "tool":
            continue
        m = {k: v for k, v in m.items() if k != "tool_calls"}
        if m.get("role") == "assistant" and not (m.get("content") or "").strip():
            continue                       # ツール呼び出しだけの assistant は消える
        out.append(m)
    return out


class Conversation:
    """対話モードの会話履歴。

    `/api/chat` はステートレスで、履歴は毎回まとめて送り直す仕様。
    ただの list ではなく**ターン単位**（1ターン = user の質問から最終回答まで）で
    持つのは、溢れたときに「ツール呼び出しと結果のペア」を壊さずに捨てるため。
    """

    def __init__(self, system=None, num_ctx=None):
        # system が None なら Modelfile に焼かれた SYSTEM がそのまま効く。
        # 空文字とは意味が違うので、未設定は None のまま扱うこと。
        self.system = system
        self.num_ctx = num_ctx if num_ctx else detect_num_ctx()
        self.turns = []
        # 起動時は未ロードで /api/ps が使えない。1回応答が返ったら実測値に直す。
        self._ctx_confirmed = False
        # 直近リクエストの実測トークン数（prompt_eval_count）。見積もりの補正に使う。
        self.measured_tokens = None
        # 1トークンあたりの文字数。日本語と英語で倍以上違うので初期値は粗く、
        # 実測が来たら補正する。
        self.chars_per_token = 2.0

    # -- 組み立て ----------------------------------------------------------
    def messages(self, extra=()):
        out = []
        if self.system:
            out.append({"role": "system", "content": self.system})
        for t in self.turns:
            out.extend(t)
        out.extend(extra)
        return out

    def add_turn(self, turn):
        """1ターンを履歴に積む。

        落とすもの2つ:
          * `thinking` — Qwen 系のチャットテンプレートは過去ターンの思考を
            そもそも渡さない設計。積むと嵩むだけで効かない
          * 打ち切り時の `STOP_SEARCHING` — こちらが差し込んだ指示であって
            かせいさんの発言ではない。残すと次ターン以降ずっと効いてしまう
        """
        kept = [{k: v for k, v in m.items() if k != "thinking"}
                for m in turn if m.get("content") != STOP_SEARCHING]
        if kept:
            self.turns.append(kept)

    def clear(self):
        n = len(self.turns)
        self.turns = []
        self.measured_tokens = None
        return n

    # -- 溢れ対策 ----------------------------------------------------------
    def budget(self):
        return int(self.num_ctx * HISTORY_CTX_RATIO)

    def est_tokens(self, extra=()):
        return int(_chars(self.messages(extra)) / self.chars_per_token)

    def observe(self, prompt_tokens, sent_messages):
        """実測のプロンプトトークン数で見積もりを補正する。

        見積もりは所詮 文字数 ÷ 係数 なので、**実測が取れるならそちらに寄せる。**
        極端な値で暴れないよう範囲で挟む。

        `prompt_tokens` にはツール定義（毎回送られる。約200トークン）が含まれるが
        `chars` には無いので、係数は本来より小さめに出る。つまり見積もりは
        **多めに出る**。早めに削る方向なので、ズレるならこちらで構わない。
        """
        if not self._ctx_confirmed:
            self._ctx_confirmed = True
            real = loaded_num_ctx()
            if real and real != self.num_ctx:
                print(f"  [num_ctx を実測値に修正: {self.num_ctx} -> {real}]",
                      file=sys.stderr)
                self.num_ctx = real
        if not prompt_tokens or prompt_tokens <= 0:
            return
        self.measured_tokens = prompt_tokens
        chars = _chars(sent_messages)
        if chars:
            self.chars_per_token = min(6.0, max(0.5, chars / prompt_tokens))

    def trim(self, emit=noop_emitter):
        """予算を超えていたら古い方から削る。**黙って忘れない**（emit で伝える）。

        2段階。まず古いターンから検索結果だけ剥がし、それでも足りなければ
        ターンごと捨てる。検索結果は嵩む割に後から効きにくく、
        会話の筋（何を聞いて何と答えたか）の方が残す価値が高いため。
        """
        budget, stripped, dropped = self.budget(), 0, 0
        for i, t in enumerate(self.turns):
            if self.est_tokens() <= budget:
                break
            lean = _without_tools(t)
            if lean != t:
                self.turns[i] = lean
                stripped += 1
        while self.turns and self.est_tokens() > budget:
            self.turns.pop(0)
            dropped += 1
        if stripped or dropped:
            emit("note", f"履歴を圧縮: 検索結果を剥がした {stripped}ターン / "
                         f"丸ごと捨てた {dropped}ターン。"
                         f"残り {len(self.turns)}ターン, 約{self.est_tokens()}トークン "
                         f"(予算 {budget})")


def post(url, payload, headers, timeout):
    req = urllib.request.Request(
        url, json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ollama_chat(messages, tools=TOOLS, think=False, emit=noop_emitter, usage=None):
    """Ollama に投げて assistant メッセージを組み立てて返す。

    常にストリーミングで受ける。思考を出すのが目的で、まとめて受け取ると
    最初の1問で50秒ほど無言になり、止まっているのか考えているのか分からない。
    届いた端から `emit` に流す（CLI なら stderr、web UI なら SSE）。

    `usage` に dict を渡すと、最終チャンクの `prompt_eval_count` を入れて返す。
    **これが送ったプロンプトの実測トークン数**で、履歴の見積もりの補正に使う。
    """
    body = {"model": MODEL, "messages": messages, "stream": True, "think": think}
    if tools:
        body["tools"] = tools

    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", json.dumps(body).encode("utf-8"),
        {"Content-Type": "application/json"})

    content, thinking, tool_calls = [], [], []
    with urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:
            raw = raw.strip()
            if not raw:
                continue
            chunk = json.loads(raw.decode("utf-8"))
            msg = chunk.get("message") or {}

            piece = msg.get("thinking")
            if piece:
                thinking.append(piece)
                emit("think", piece)

            if msg.get("content"):
                content.append(msg["content"])
                emit("content", msg["content"])
            if msg.get("tool_calls"):
                tool_calls.extend(msg["tool_calls"])
            if chunk.get("done"):
                if usage is not None:
                    usage["prompt_tokens"] = chunk.get("prompt_eval_count")
                    usage["eval_tokens"] = chunk.get("eval_count")
                break

    out = {"role": "assistant", "content": "".join(content)}
    if thinking:
        out["thinking"] = "".join(thinking)
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def brave_search(query, max_results):
    """Brave Search API（公式）。BRAVE_API_KEY が要る。

    スクレイピングでないので、レート制限や HTML 変更で壊れない。
    無料枠は $5/月のクレジット（$5/1,000リクエストなので実質 約1,000/月）。
    """
    if not BRAVE_API_KEY:
        return {"error": "BRAVE_API_KEY が未設定。.env に入れるか、"
                         "SEARCH_BACKEND=ddgs に切り替えること。"}
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
        {"q": query, "count": max(1, min(20, max_results)),
         "safesearch": SAFESEARCH_MAP[SAFESEARCH]["brave"]})
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        hint = ""
        if e.code == 401:
            hint = " キーが無効か失効している。"
        elif e.code == 429:
            hint = " レート制限。無料枠の上限か q/s 超過を疑う。"
        return {"error": f"Brave API HTTP {e.code}:{hint} {body}"}
    except Exception as e:                                  # noqa: BLE001
        return {"error": f"Brave API: {type(e).__name__}: {e}"}

    hits = (data.get("web") or {}).get("results") or []
    if not hits:
        return {"error": f"'{query}' の検索結果が 0 件 (brave)"}
    return {"query": query,
            "results": [{"title": h.get("title", ""),
                         "url": h.get("url", ""),
                         "content": (h.get("description") or "")[:600]}
                        for h in hits[:max_results]]}


def ddgs_search(query, max_results):
    """ddgs（旧 duckduckgo-search）で検索する。外部プロセス不要。"""
    try:
        from ddgs import DDGS
    except ImportError:
        return {"error": "ddgs が入っていない。`pip install -r requirements.txt` するか、"
                         "SEARCH_BACKEND=searxng に切り替えること。"}
    try:
        hits = DDGS().text(query, max_results=max_results, backend=DDGS_BACKEND,
                           safesearch=SAFESEARCH_MAP[SAFESEARCH]["ddgs"])
    except Exception as e:                                  # noqa: BLE001
        # ddgs は0件も例外で投げてくる（DDGSException: No results found）
        return {"error": f"ddgs 検索に失敗 (backend={DDGS_BACKEND}, "
                         f"safesearch={SAFESEARCH}): {type(e).__name__}: {e}"}
    if not hits:
        return {"error": f"'{query}' の検索結果が 0 件 (backend={DDGS_BACKEND})"}
    return {"query": query,
            "results": [{"title": h.get("title", ""),
                         "url": h.get("href", ""),
                         "content": (h.get("body") or "")[:600]} for h in hits]}


def searxng_search(query, max_results):
    """ローカル SearXNG の JSON API を叩く。"""
    url = f"{SEARXNG}/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json",
         "safesearch": SAFESEARCH_MAP[SAFESEARCH]["searxng"]})
    try:
        with urllib.request.urlopen(url, timeout=45) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"SearXNG ({SEARXNG}) に繋がらない: {e.reason}。"
                         f"start.bat から起動するか、SEARXNG_URL を設定すること。"}
    except Exception as e:                                  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}

    hits = [{"title": h.get("title", ""),
             "url": h.get("url", ""),
             "content": (h.get("content") or "")[:600]}
            for h in data.get("results", [])[:max_results]]
    if not hits:
        return {"error": f"'{query}' の検索結果が 0 件。"
                         f"unresponsive_engines={data.get('unresponsive_engines')}"}
    return {"query": query, "results": hits}


_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?:\s*年)?(?!\d)")
_MONTH = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
    r"|(?<!\d)\d{1,2}\s*月", re.I)


def strip_stale_year(query, user_question):
    """モデルが勝手に足した年月をクエリから落とす。

    このモデルの内部日付感覚は 2024年5月で止まっており、放っておくと
    `AI latest news 2024` のようなクエリを打つ。検索は成立してしまうので
    **古い記事を「最新」として自信たっぷりに答える**という一番たちの悪い
    失敗になる（実測）。SYSTEM プロンプトで禁じても4〜6割しか効かないので、
    ここで機械的に落とす。

    年だけ落とすと `AI news January` のような無意味なクエリが残り、
    モデルが月を変えて延々と検索し直す（実測）。月名も一緒に落とす。

    ユーザー自身が年月を指定したときは、その意図なので触らない。
    """
    uq = user_question or ""
    if _YEAR.search(uq) or _MONTH.search(uq):
        return query
    cleaned = _WS_ONLY.sub(" ", _MONTH.sub(" ", _YEAR.sub(" ", query))).strip()
    return cleaned or query


_WS_ONLY = re.compile(r"\s+")
_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_WS = re.compile(r"[ \t\r\f\v]+")


def web_fetch(url):
    """URL を取得して本文らしきテキストを返す。依存を増やさないため簡易実装。"""
    if not url.lower().startswith(("http://", "https://")):
        return {"error": f"http(s) 以外の URL は取得しない: {url}"}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            charset = r.headers.get_content_charset() or "utf-8"
            raw = r.read(2_000_000).decode(charset, "replace")
    except Exception as e:                                  # noqa: BLE001
        return {"error": f"取得失敗 {url}: {type(e).__name__}: {e}"}
    text = _WS.sub(" ", _TAG.sub(" ", raw))
    text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
    return {"url": url, "text": text[:FETCH_CHARS],
            "truncated": len(text) > FETCH_CHARS}


def run_tool(name, args, user_question="", emit=noop_emitter):
    if name == "web_search":
        mr = args.get("max_results") or 5
        q = strip_stale_year(args.get("query", ""), user_question)
        if q != args.get("query", ""):
            emit("note", f"年号を除去: {args.get('query')!r} -> {q!r}")
        n = max(1, min(10, int(mr)))
        res = {"brave": brave_search, "ddgs": ddgs_search,
               "searxng": searxng_search}[BACKEND](q, n)
    elif name == "web_fetch":
        res = web_fetch(args.get("url", ""))
    else:
        return {"error": f"unknown tool: {name}"}

    # 検索が失敗したまま続行させない。エラーを渡すとモデルは黙って記憶から
    # 答えてしまい（実測）、それが一番避けたい失敗になる。
    if isinstance(res, dict) and res.get("error"):
        raise SearchFailed(res["error"])
    return res


def ask(question, conv=None, force=False, verbose=True, think=True, emit=None):
    """1ターン分を回して最終回答を返す。

    `conv` を渡すと、その履歴を前に付けて送り、**終わったターンを履歴に積む**。
    渡さなければ使い捨て（one-shot はこちら）。

    ターンは最後まで通ってから積む。途中で `SearchFailed` が飛べば
    **質問ごと履歴に残らない**が、モデルが答えていない以上それが正しい。

    `emit(kind, payload)` に進捗が流れる。CLI は stderr、web UI は SSE。
    kind は think / content / tool / result / note。
    """
    if emit is None:
        emit = cli_emitter(verbose)
    if conv is None:
        conv = Conversation()
    conv.trim(emit)

    turn = [{"role": "user", "content": question}]

    def send(tools):
        sent = conv.messages(turn)
        usage = {}
        msg = ollama_chat(sent, tools=tools, think=think, emit=emit, usage=usage)
        conv.observe(usage.get("prompt_tokens"), sent)
        return msg

    if force:
        # モデルの判断に任せず、こちらで1回目の検索を済ませて結果を渡す。
        emit("note", f"forced search: {question}")
        res = run_tool("web_search", {"query": question}, question, emit)
        emit("result", {"name": "web_search", "result": res})
        turn.append({"role": "tool", "tool_name": "web_search",
                     "content": json.dumps(res, ensure_ascii=False)[:8000]})

    for _ in range(MAX_ROUNDS):
        msg = send(TOOLS)
        calls = msg.get("tool_calls")
        if not calls:
            turn.append(msg)
            conv.add_turn(turn)
            emit("endturn", None)      # CLI 側で開いたままの思考ブロックを閉じる
            return msg.get("content", "")
        turn.append(msg)
        for c in calls:
            fn = c["function"]
            args = fn.get("arguments") or {}
            emit("tool", {"name": fn["name"], "args": args})
            res = run_tool(fn["name"], args, question, emit)
            emit("result", {"name": fn["name"], "result": res})
            turn.append({"role": "tool", "tool_name": fn["name"],
                         "content": json.dumps(res, ensure_ascii=False)[:8000]})

    # 上限に達した。日付が分からないせいでモデルは「もっと新しいものがあるはず」と
    # 検索を繰り返しがち（実測）。ここで**ツールを外して**もう一度呼び、
    # 集めた結果から答えさせる。記憶から答えさせるのとは違う点に注意。
    emit("note", f"{MAX_ROUNDS}回で打ち切り。集めた結果から回答させる")
    turn.append({"role": "user", "content": STOP_SEARCHING})
    msg = send(None)
    turn.append(msg)
    conv.add_turn(turn)
    emit("endturn", None)
    return msg.get("content", "")


def model_exists(name):
    """そのモデルが Ollama にあるか。**確認できなければ None**（False ではない）。

    Ollama が落ちているだけのときに「無い」と言い切ると、
    直せるはずの状況で切り替えを拒否することになる。区別する。
    """
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:                                       # noqa: BLE001
        return None
    return any(m.get("name") == name for m in data.get("models") or [])


def modelfile_system(model=None):
    """Modelfile に焼かれた SYSTEM。`/system` 未設定のとき効いているのはこれ。"""
    try:
        return (post(f"{OLLAMA}/api/show", {"model": model or MODEL}, {}, 30)
                .get("system") or "")
    except Exception:                                       # noqa: BLE001
        return ""


def _onoff(rest, current):
    """`on` / `off` / 空（トグル）を解釈する。それ以外は ValueError。"""
    rest = rest.strip().lower()
    if not rest:
        return not current
    if rest in ("on", "yes", "true", "1"):
        return True
    if rest in ("off", "no", "false", "0"):
        return False
    raise ValueError(f"on / off のどちらかを指定すること（受け取ったのは {rest!r}）")


class Repl:
    """対話モードの状態と、行頭 `/` のコマンド。

    **未知のコマンドはモデルに送らない。** `/claer` のようなタイポが黙って質問として
    流れると、推論が1回無駄になるうえ「なぜ効かないのか」が分からない。
    知らないものは知らないと言って止める。

    コマンドの出力は全部 stderr。答え（stdout）と混ぜないという既存の方針に合わせる。
    """

    ALIASES = {"?": "help", "h": "help", "q": "exit", "quit": "exit",
               "reset": "clear", "sys": "system", "hist": "history"}

    def __init__(self, args):
        self.conv = Conversation()
        self.force = args.force
        self.think = args.think
        self.verbose = not args.quiet
        self.done = False

    # -- 入口 --------------------------------------------------------------
    def handle(self, line):
        """1行を処理する。コマンドなら実行、そうでなければモデルに投げる。"""
        # 行頭 '//' は '/' 1つのエスケープ。'/' で始まる文章を本当に送りたいとき用。
        if line.startswith("//"):
            return self.query(line[1:])
        if line.startswith("/"):
            return self.command(line)
        # 昔からある終了語。指が覚えているものを取り上げる理由がない。
        if line.lower() in ("exit", "quit", ":q", ":wq", "終了", "おわり"):
            self.done = True
            return
        return self.query(line)

    def command(self, line):
        name, _, rest = line[1:].partition(" ")
        name = name.strip().lower()
        fn = getattr(self, "cmd_" + self.ALIASES.get(name, name), None)
        if not fn:
            print(f"  知らないコマンド: /{name}   （/help で一覧。"
                  f"'/' で始まる文章を送りたいなら '//' で始める）", file=sys.stderr)
            return
        try:
            fn(rest.strip())
        except ValueError as e:
            print(f"  {e}", file=sys.stderr)

    def query(self, question):
        force = self.force or question.startswith("!")
        try:
            print(ask(question.lstrip("!").strip(), self.conv, force,
                      self.verbose, self.think))
        except SearchFailed as e:
            print(f"検索に失敗した: {e}", file=sys.stderr)

    # -- コマンド ----------------------------------------------------------
    #   docstring の1行目がそのまま /help の説明になる。
    def cmd_help(self, rest):
        """コマンド一覧"""
        print("  コマンド:", file=sys.stderr)
        for n in sorted(x[4:] for x in dir(self) if x.startswith("cmd_")):
            doc = (getattr(self, "cmd_" + n).__doc__ or "").splitlines()[0]
            print(f"    /{n:<8} {doc}", file=sys.stderr)
        print("  そのほか:", file=sys.stderr)
        print("    行頭 '!'  その1問だけ検索を強制", file=sys.stderr)
        print("    行頭 '//' '/' で始まる文章をそのまま送る", file=sys.stderr)
        print("    exit / quit / 終了 / おわり / :q / Ctrl-C  で抜ける",
              file=sys.stderr)

    def cmd_clear(self, rest):
        """会話履歴を捨てる（システムプロンプトとモデルはそのまま）"""
        print(f"  履歴を捨てた（{self.conv.clear()}ターン）", file=sys.stderr)

    def cmd_history(self, rest):
        """今積んでいる履歴を見る"""
        c = self.conv
        if not c.turns:
            print("  履歴は空", file=sys.stderr)
        for i, t in enumerate(c.turns, 1):
            first = next((m.get("content", "") for m in t
                          if m.get("role") == "user"), "")
            last = next((m.get("content", "") for m in reversed(t)
                         if m.get("role") == "assistant" and m.get("content")), "")
            tools = sum(1 for m in t if m.get("role") == "tool")
            mark = f" (検索結果 {tools}件)" if tools else ""
            print(f"  {i}. > {first[:60]}{mark}", file=sys.stderr)
            print(f"     < {last[:60]}", file=sys.stderr)
        # 見積もりは履歴だけ、実測はツール定義と今の質問も含む**別のもの**。
        # 並べると同じ量に見えるので、何を数えたか書いておく。
        measured = (f", 直近の実測 {c.measured_tokens}トークン"
                    f"（ツール定義と質問込み）" if c.measured_tokens else "")
        print(f"  約{c.est_tokens()}トークン / 予算 {c.budget()} "
              f"(num_ctx {c.num_ctx}){measured}", file=sys.stderr)

    def cmd_system(self, rest):
        """システムプロンプトの表示・設定・解除（/system reset で解除）"""
        if not rest:
            if self.conv.system is None:
                baked = modelfile_system()
                print("  未設定。Modelfile に焼かれた SYSTEM が効いている:",
                      file=sys.stderr)
                print(f"    {baked or '(空)'}", file=sys.stderr)
            else:
                print(f"  {self.conv.system}", file=sys.stderr)
            return
        if rest.lower() == "reset":
            self.conv.system = None
            print("  解除した。Modelfile の SYSTEM に戻る", file=sys.stderr)
            return
        # **Modelfile の SYSTEM を置き換える**（重ねるのではない）。
        # 日付まわりの禁止もこれで消えるので、必要なら書き足すこと。
        self.conv.system = rest
        print("  設定した。**Modelfile の SYSTEM は置き換わる**"
              "（日付まわりの禁止も消える）", file=sys.stderr)

    def cmd_model(self, rest):
        """モデルの表示・切り替え"""
        global MODEL
        if not rest:
            print(f"  {MODEL}  (num_ctx {self.conv.num_ctx})", file=sys.stderr)
            return
        exists = model_exists(rest)
        if exists is False:
            raise ValueError(f"{rest!r} は Ollama に無い（ollama list で確認）")
        if exists is None:
            print("  警告: Ollama に繋がらずモデルの存在を確認できなかった",
                  file=sys.stderr)
        MODEL = rest
        # num_ctx はモデルごとに違う。**引き直さないと予算が前のモデルのままになる。**
        self.conv.num_ctx = detect_num_ctx()
        self.conv._ctx_confirmed = False
        print(f"  {MODEL} に切り替えた (num_ctx {self.conv.num_ctx})。"
              f"履歴はそのまま（捨てるなら /clear）", file=sys.stderr)

    def cmd_think(self, rest):
        """思考の on / off（速さと精度のトレードオフ）"""
        self.think = _onoff(rest, self.think)
        print(f"  think = {'on' if self.think else 'off'}", file=sys.stderr)

    def cmd_force(self, rest):
        """検索強制を常時 on / off にする（1問だけなら行頭 '!'）"""
        self.force = _onoff(rest, self.force)
        print(f"  force = {'on' if self.force else 'off'}", file=sys.stderr)

    def cmd_exit(self, rest):
        """抜ける"""
        self.done = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("-f", "--force", action="store_true",
                    help="モデルの判断を待たず必ず検索する")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="ツール呼び出しと思考を表示しない")
    ap.add_argument("--no-think", dest="think", action="store_false",
                    help="思考を切る（速くなるが精度は落ちる）")
    a = ap.parse_args()

    if a.question:
        # one-shot は履歴を持たない。プロセスが1問で終わるので持つ意味がない。
        try:
            print(ask(" ".join(a.question), None, a.force, not a.quiet, a.think))
        except SearchFailed as e:
            print(f"検索に失敗した: {e}", file=sys.stderr)
            sys.exit(1)
        return

    repl = Repl(a)
    print(f"model={MODEL}  num_ctx={repl.conv.num_ctx}"
          f" (履歴の予算 {repl.conv.budget()}トークン)", file=sys.stderr)
    print("  /help でコマンド一覧。exit / quit / 終了 / Ctrl-C で抜ける",
          file=sys.stderr)
    while not repl.done:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return
        if line:
            repl.handle(line)


if __name__ == "__main__":
    main()
