#!/usr/bin/env python3
"""webui.py -- ollama-search.py のエージェントに web の画面を付ける。

  python webui.py            http://127.0.0.1:4645 で待つ
  python webui.py --port N   ポートを指定（埋まっていたら上に空きを探す）

CLI と**同じエージェント**（`ask()` / `Conversation`）を呼ぶ。画面が増えるだけで、
年号の除去・検索失敗での打ち切り・上限到達時の回答強制はそのまま効く。

会話ログは隣の `chats.sqlite`。**サーバを再起動しても文脈は続く**（CLI の対話モードは
プロセスが死ぬと消える）。ここが web にする一番の実利。

**127.0.0.1 にしか bind しない。** モデルの求めに応じて任意の URL を取りに行く
（`web_fetch`）ので、外から叩ける場所に置くと踏み台になる。
"""
import argparse, importlib.util, json, os, sqlite3, sys, threading, time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

# `ollama-search.py` はハイフン入りで import 文が書けないので importlib で読む。
# 入口として README に載っている名前なので、import しやすさのために改名はしない。
_spec = importlib.util.spec_from_file_location(
    "ollama_search", os.path.join(HERE, "ollama-search.py"))
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)

DB_PATH = os.environ.get("WEBUI_DB", os.path.join(HERE, "chats.sqlite"))
HTML_PATH = os.path.join(HERE, "webui.html")
DEFAULT_PORT = int(os.environ.get("WEBUI_PORT", "4645"))

# モデルは1つしか動かせないので、生成は同時に1本まで。2本目は 409 を返す。
# 待たせると、待っている側のブラウザが黙って固まる。
GEN_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# 会話ログ（sqlite）
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL DEFAULT '',
    system     TEXT,                     -- NULL なら Modelfile の SYSTEM が効く
    model      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    turn       INTEGER NOT NULL,         -- ターン番号。履歴を削るときの単位
    seq        INTEGER NOT NULL,         -- ターン内の順番
    role       TEXT NOT NULL,            -- user / assistant / tool / error
    content    TEXT NOT NULL DEFAULT '',
    thinking   TEXT NOT NULL DEFAULT '',
    tool_name  TEXT,
    tool_calls TEXT,                     -- JSON
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, turn, seq);
"""


def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    with db() as con:
        con.executescript(SCHEMA)


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_conversation(chat):
    """保存済みのログから `Conversation` を組み直す。

    **sqlite が正で、メモリ上の状態は持たない。** サーバを再起動しても、
    別のタブから続きを書いても、同じ文脈になる。

    `thinking` は**戻さない**（表示用に保存してあるだけ）。`role='error'` も戻さない
    ——検索に失敗した記録であって、モデルの発言ではないため。
    """
    conv = agent.Conversation(system=chat["system"])
    with db() as con:
        rows = con.execute(
            "SELECT * FROM messages WHERE chat_id=? AND role<>'error' "
            "ORDER BY turn, seq", (chat["id"],)).fetchall()
    turns, cur, cur_no = [], [], None
    for r in rows:
        if r["turn"] != cur_no:
            if cur:
                turns.append(cur)
            cur, cur_no = [], r["turn"]
        m = {"role": r["role"], "content": r["content"]}
        if r["tool_name"]:
            m["tool_name"] = r["tool_name"]
        if r["tool_calls"]:
            m["tool_calls"] = json.loads(r["tool_calls"])
        cur.append(m)
    if cur:
        turns.append(cur)
    conv.turns = turns
    return conv


def next_turn(chat_id):
    with db() as con:
        n = con.execute("SELECT MAX(turn) FROM messages WHERE chat_id=?",
                        (chat_id,)).fetchone()[0]
    return 0 if n is None else n + 1


def save_message(chat_id, turn, seq, msg, thinking=""):
    with db() as con:
        con.execute(
            "INSERT INTO messages (chat_id,turn,seq,role,content,thinking,"
            "tool_name,tool_calls,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (chat_id, turn, seq, msg.get("role", ""), msg.get("content") or "",
             thinking, msg.get("tool_name"),
             json.dumps(msg["tool_calls"], ensure_ascii=False)
             if msg.get("tool_calls") else None, now()))
        con.execute("UPDATE chats SET updated_at=? WHERE id=?", (now(), chat_id))


# --------------------------------------------------------------------------
# Ollama の状態
# --------------------------------------------------------------------------
def list_models():
    try:
        with urllib.request.urlopen(f"{agent.OLLAMA}/api/tags", timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:                                       # noqa: BLE001
        return []
    return sorted(m.get("name", "") for m in data.get("models") or [])


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ollama-toolbox-webui"

    def log_message(self, fmt, *args):
        # 既定のアクセスログは SSE のせいで騒がしいだけなので黙らせる
        pass

    # -- 返す ------------------------------------------------------------
    def _send(self, code, body=b"", ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 画面は自前のものしか出さないが、念のため
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _error(self, code, msg):
        self._json({"error": msg}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    # -- 経路 ------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            try:
                with open(HTML_PATH, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                return self._error(500, f"webui.html が読めない: {e}")
        if path == "/api/state":
            return self._json({
                "model": agent.MODEL, "models": list_models(),
                "backend": agent.BACKEND, "safesearch": agent.SAFESEARCH,
                "num_ctx": agent.detect_num_ctx(),
            })
        if path == "/api/chats":
            with db() as con:
                rows = con.execute(
                    "SELECT id,title,updated_at FROM chats "
                    "ORDER BY updated_at DESC").fetchall()
            return self._json([dict(r) for r in rows])
        if path.startswith("/api/chats/"):
            chat = self._chat(path.split("/")[3])
            if chat is None:
                return
            with db() as con:
                rows = con.execute(
                    "SELECT turn,seq,role,content,thinking,tool_name,tool_calls "
                    "FROM messages WHERE chat_id=? ORDER BY turn,seq",
                    (chat["id"],)).fetchall()
            return self._json({**dict(chat), "messages": [dict(r) for r in rows]})
        return self._error(404, "no such path")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/chats":
            with db() as con:
                cur = con.execute(
                    "INSERT INTO chats (title,system,model,created_at,updated_at) "
                    "VALUES ('',NULL,?,?,?)", (agent.MODEL, now(), now()))
            return self._json({"id": cur.lastrowid})
        parts = path.split("/")
        if len(parts) == 5 and parts[1] == "api" and parts[2] == "chats" \
                and parts[4] == "ask":
            return self.do_ask(parts[3])
        return self._error(404, "no such path")

    def do_PATCH(self):
        parts = self.path.split("?")[0].split("/")
        if len(parts) != 4 or parts[1] != "api" or parts[2] != "chats":
            return self._error(404, "no such path")
        chat = self._chat(parts[3])
        if chat is None:
            return
        b = self._body()
        sets, vals = [], []
        for k in ("title", "system", "model"):
            if k in b:
                # system だけは None（未設定）と "" を区別する。**意味が違う**:
                # None は Modelfile の SYSTEM が効く、"" は空の SYSTEM で上書き。
                v = b[k]
                if k == "system" and (v is None or v == ""):
                    v = None
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            with db() as con:
                con.execute(f"UPDATE chats SET {','.join(sets)},updated_at=? "
                            f"WHERE id=?", (*vals, now(), chat["id"]))
        return self._json({"ok": True})

    def do_DELETE(self):
        parts = self.path.split("?")[0].split("/")
        if len(parts) != 4 or parts[1] != "api" or parts[2] != "chats":
            return self._error(404, "no such path")
        chat = self._chat(parts[3])
        if chat is None:
            return
        with db() as con:
            con.execute("DELETE FROM messages WHERE chat_id=?", (chat["id"],))
            con.execute("DELETE FROM chats WHERE id=?", (chat["id"],))
        return self._json({"ok": True})

    def _chat(self, raw_id):
        """チャットを引く。無ければ 404 を返して None（呼び出し側は即 return）。"""
        if not raw_id.isdigit():
            self._error(404, "no such chat")
            return None
        with db() as con:
            row = con.execute("SELECT * FROM chats WHERE id=?",
                              (int(raw_id),)).fetchone()
        if row is None:
            self._error(404, "no such chat")
        return row

    # -- 本番: 質問を投げて途中経過を流す --------------------------------
    def do_ask(self, raw_id):
        chat = self._chat(raw_id)
        if chat is None:
            return
        b = self._body()
        question = (b.get("question") or "").strip()
        if not question:
            return self._error(400, "question が空")
        if not GEN_LOCK.acquire(blocking=False):
            return self._error(409, "いま別の質問を処理中。終わるまで待つこと")
        try:
            self._ask(chat, question, bool(b.get("force")),
                      b.get("think", True) is not False, b.get("model"))
        finally:
            GEN_LOCK.release()

    def _ask(self, chat, question, force, think, model):
        # モデルの切り替えはプロセス全体に効く（agent.MODEL がグローバル）。
        # 生成は同時1本なので競合はしないが、**他のチャットの既定も変わる**。
        if model and model != agent.MODEL:
            agent.MODEL = model
        turn = next_turn(chat["id"])
        save_message(chat["id"], turn, 0, {"role": "user", "content": question})
        if not chat["title"]:
            with db() as con:
                con.execute("UPDATE chats SET title=? WHERE id=?",
                            (question[:60], chat["id"]))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        state = {"dead": False, "think": []}

        def sse(kind, payload):
            """1イベント送る。**送れなくなっても生成は止めない。**

            ブラウザを閉じられただけで、走らせた推論と検索を捨てるのは惜しい。
            答えは sqlite に入るので、開き直せば続きから読める。
            """
            if state["dead"]:
                return
            try:
                line = json.dumps({"kind": kind, "payload": payload},
                                  ensure_ascii=False)
                self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                    OSError):
                state["dead"] = True

        def emit(kind, payload):
            if kind == "think":
                state["think"].append(payload)
            sse(kind, payload)

        conv = load_conversation(chat)
        try:
            answer = agent.ask(question, conv, force, True, think, emit)
        except agent.SearchFailed as e:
            # 検索の失敗は握り潰さない（CLI と同じ）。記録は残すが、
            # role='error' なので次の文脈には入らない。
            save_message(chat["id"], turn, 1,
                         {"role": "error", "content": f"検索に失敗した: {e}"})
            return sse("failed", str(e))
        except Exception as e:                              # noqa: BLE001
            save_message(chat["id"], turn, 1,
                         {"role": "error",
                          "content": f"{type(e).__name__}: {e}"})
            return sse("failed", f"{type(e).__name__}: {e}")

        # ask() が積んだターンをそのまま保存する。先頭は今保存した user なので飛ばす。
        # thinking は表示用にターンごと1つにまとめて最後の assistant に付ける
        # （ラウンドごとに分けて持つほどの価値がない）。
        saved = conv.turns[-1] if conv.turns else []
        for i, m in enumerate(saved[1:], start=1):
            last = i == len(saved) - 1
            save_message(chat["id"], turn, i, m,
                         "".join(state["think"]) if last else "")
        sse("done", {"answer": answer, "turn": turn})


def serve(port):
    init_db()
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
        except OSError:
            continue                       # 埋まっている。上に空きを探す
        print(f"http://127.0.0.1:{p}  (model={agent.MODEL}, "
              f"backend={agent.BACKEND})", file=sys.stderr)
        print(f"会話ログ: {DB_PATH}", file=sys.stderr)
        print("Ctrl-C で止める", file=sys.stderr)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(file=sys.stderr)
        return
    sys.exit(f"{port}〜{port + 19} が全部埋まっている")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve(ap.parse_args().port)
