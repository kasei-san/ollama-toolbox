"""Windows で SearXNG を動かすための Unix API シム。

SearXNG は Linux 前提なので、いくつか Unix 専用 API を無条件に呼ぶ。
リポジトリ本体を書き換えると `git pull` で壊れるので、venv 側にこれを置いて
インタプリタ起動時に補う（sitecustomize は site の初期化で自動 import される）。

現時点で必要なのは searx/valkeydb.py の1行だけ:

    _pw = pwd.getpwuid(os.getuid())

これは Valkey への接続に失敗したときのログ出力用。Valkey は使っていないので
値は何でもよく、例外さえ出なければいい。

新しい Unix API で落ちたら、ここに足すこと（grp / fcntl / os.geteuid など）。
"""

import os
import sys
import types

if os.name == "nt":
    for _name, _val in (("getuid", 0), ("geteuid", 0), ("getgid", 0), ("getegid", 0)):
        if not hasattr(os, _name):
            setattr(os, _name, (lambda v=_val: v))

    if "pwd" not in sys.modules:
        _pwd = types.ModuleType("pwd")

        class _PasswdEntry(tuple):
            pw_name = "windows"
            pw_passwd = "x"
            pw_uid = 0
            pw_gid = 0
            pw_gecos = ""
            pw_dir = os.path.expanduser("~")
            pw_shell = ""

            def __new__(cls):
                return super().__new__(cls, ("windows", "x", 0, 0, "", cls.pw_dir, ""))

        _pwd.struct_passwd = _PasswdEntry
        _pwd.getpwuid = lambda uid=0: _PasswdEntry()
        _pwd.getpwnam = lambda name="windows": _PasswdEntry()
        _pwd.getpwall = lambda: [_PasswdEntry()]
        sys.modules["pwd"] = _pwd
