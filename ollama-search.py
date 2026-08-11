#!/usr/bin/env python3
"""ollama-search.py -- ローカル Ollama モデルに web 検索をさせる最小エージェント。

  python ollama-search.py "日銀の直近の決定は？"
  python ollama-search.py            # 対話モード（exit / quit / 終了 で抜ける）
  python ollama-search.py -f "..."   # 検索を強制（モデルの判断に任せない）
  python ollama-search.py --no-think # 思考を切る（速いが精度は落ちる）

思考の中身は stderr にリアルタイムで流す。答えは stdout なので、
`... 2>nul` で思考だけ捨てられるし、`... >out.txt` で答えだけ拾える。

検索バックエンドは2つ。`SEARCH_BACKEND` で切り替える。

  ddgs    （既定）pip の ddgs。外部プロセス不要。bing/brave/yandex を叩く
  searxng 自前で立てた SearXNG（別プロセスが要る。start.bat が面倒を見る）

どちらも **ollama.com の web search API は使わない**。あれは無料で手軽だが、
検索クエリが ollama.com に送られる。モデルの推論は元々ローカル完結なので
外に出るのは「何を検索したか」だけだが、それも出したくないという判断。
ddgs / SearXNG はどちらも自分のマシンから検索エンジンに直接行くので、
その点では同等。

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

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("OLLAMA_SEARCH_MODEL", "hauhau-aggressive:iq2m")
SEARXNG = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")
MAX_ROUNDS = 4
FETCH_CHARS = 6000

# 検索バックエンド: "ddgs"（既定、pip の ddgs が要る）か "searxng"
BACKEND = os.environ.get("SEARCH_BACKEND", "ddgs").lower()

# ddgs が内部で叩くエンジン。2026-08-11 の実測で**結果を返したのはこの3つだけ**:
#   動く    : bing / brave / yandex
#   0件で死ぬ: duckduckgo / google / mojeek / yahoo / startpage / wikipedia
# duckduckgo 本体が死んでいるのは皮肉だが、**google も同じく死んでいる**ので、
# 結果として Google 依存からは完全に離れられている。
# "auto" にすると ddgs が勝手に選ぶ。明示した方が挙動が読めるので既定は明示。
DDGS_BACKEND = os.environ.get("DDGS_BACKEND", "bing,brave,yandex")

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


def post(url, payload, headers, timeout):
    req = urllib.request.Request(
        url, json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ollama_chat(messages, tools=TOOLS, think=False, show_think=False):
    """Ollama に投げて assistant メッセージを組み立てて返す。

    常にストリーミングで受ける。思考を出すのが目的で、まとめて受け取ると
    最初の1問で50秒ほど無言になり、止まっているのか考えているのか分からない。
    思考は stderr に流し、答え（stdout）と混ざらないようにしている。
    """
    body = {"model": MODEL, "messages": messages, "stream": True, "think": think}
    if tools:
        body["tools"] = tools

    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", json.dumps(body).encode("utf-8"),
        {"Content-Type": "application/json"})

    content, thinking, tool_calls, opened = [], [], [], False
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
                if show_think:
                    if not opened:
                        print("  [think] ", end="", file=sys.stderr, flush=True)
                        opened = True
                    # 思考は改行だらけなので、字下げを保って読めるようにする
                    print(piece.replace("\n", "\n          "),
                          end="", file=sys.stderr, flush=True)

            if msg.get("content"):
                content.append(msg["content"])
            if msg.get("tool_calls"):
                tool_calls.extend(msg["tool_calls"])
            if chunk.get("done"):
                break

    if opened:
        print("\n", file=sys.stderr, flush=True)

    out = {"role": "assistant", "content": "".join(content)}
    if thinking:
        out["thinking"] = "".join(thinking)
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def ddgs_search(query, max_results):
    """ddgs（旧 duckduckgo-search）で検索する。外部プロセス不要。"""
    try:
        from ddgs import DDGS
    except ImportError:
        return {"error": "ddgs が入っていない。`pip install -r requirements.txt` するか、"
                         "SEARCH_BACKEND=searxng に切り替えること。"}
    try:
        hits = DDGS().text(query, max_results=max_results, backend=DDGS_BACKEND)
    except Exception as e:                                  # noqa: BLE001
        # ddgs は0件も例外で投げてくる（DDGSException: No results found）
        return {"error": f"ddgs 検索に失敗 ({DDGS_BACKEND}): {type(e).__name__}: {e}"}
    if not hits:
        return {"error": f"'{query}' の検索結果が 0 件 (backend={DDGS_BACKEND})"}
    return {"query": query,
            "results": [{"title": h.get("title", ""),
                         "url": h.get("href", ""),
                         "content": (h.get("body") or "")[:600]} for h in hits]}


def searxng_search(query, max_results):
    """ローカル SearXNG の JSON API を叩く。"""
    url = f"{SEARXNG}/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "safesearch": 0})
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


def run_tool(name, args, user_question=""):
    if name == "web_search":
        mr = args.get("max_results") or 5
        q = strip_stale_year(args.get("query", ""), user_question)
        if q != args.get("query", ""):
            print(f"  [年号を除去] {args.get('query')!r} -> {q!r}", file=sys.stderr)
        n = max(1, min(10, int(mr)))
        res = ddgs_search(q, n) if BACKEND == "ddgs" else searxng_search(q, n)
    elif name == "web_fetch":
        res = web_fetch(args.get("url", ""))
    else:
        return {"error": f"unknown tool: {name}"}

    # 検索が失敗したまま続行させない。エラーを渡すとモデルは黙って記憶から
    # 答えてしまい（実測）、それが一番避けたい失敗になる。
    if isinstance(res, dict) and res.get("error"):
        raise SearchFailed(res["error"])
    return res


def ask(question, force=False, verbose=True, think=True):
    show = verbose and think
    messages = [{"role": "user", "content": question}]
    if force:
        # モデルの判断に任せず、こちらで1回目の検索を済ませて結果を渡す。
        res = run_tool("web_search", {"query": question}, question)
        if verbose:
            print(f"  [forced search] {question}", file=sys.stderr)
        messages.append({"role": "tool", "tool_name": "web_search",
                         "content": json.dumps(res, ensure_ascii=False)[:8000]})

    for _ in range(MAX_ROUNDS):
        msg = ollama_chat(messages, think=think, show_think=show)
        calls = msg.get("tool_calls")
        if not calls:
            return msg.get("content", "")
        messages.append(msg)
        for c in calls:
            fn = c["function"]
            args = fn.get("arguments") or {}
            if verbose:
                print(f"  [{fn['name']}] {json.dumps(args, ensure_ascii=False)}",
                      file=sys.stderr)
            res = run_tool(fn["name"], args, question)
            messages.append({"role": "tool", "tool_name": fn["name"],
                             "content": json.dumps(res, ensure_ascii=False)[:8000]})

    # 上限に達した。日付が分からないせいでモデルは「もっと新しいものがあるはず」と
    # 検索を繰り返しがち（実測）。ここで**ツールを外して**もう一度呼び、
    # 集めた結果から答えさせる。記憶から答えさせるのとは違う点に注意。
    if verbose:
        print(f"  [{MAX_ROUNDS}回で打ち切り。集めた結果から回答させる]", file=sys.stderr)
    messages.append({"role": "user", "content":
                     "Stop searching. Answer now using only the search results above. "
                     "If they are insufficient, say so explicitly."})
    return ollama_chat(messages, tools=None, think=think,
                       show_think=show).get("content", "")


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
        try:
            print(ask(" ".join(a.question), a.force, not a.quiet, a.think))
        except SearchFailed as e:
            print(f"検索に失敗した: {e}", file=sys.stderr)
            sys.exit(1)
        return
    print(f"model={MODEL}", file=sys.stderr)
    print("  exit / quit / 終了 または Ctrl-C で抜ける", file=sys.stderr)
    print("  行頭 '!' で検索を強制", file=sys.stderr)
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return
        if not q:
            continue
        if q.lower() in ("exit", "quit", ":q", ":wq", "終了", "おわり"):
            return
        force = a.force or q.startswith("!")
        try:
            print(ask(q.lstrip("!").strip(), force, not a.quiet, a.think))
        except SearchFailed as e:
            print(f"検索に失敗した: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
