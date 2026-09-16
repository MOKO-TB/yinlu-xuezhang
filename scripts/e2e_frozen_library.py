"""冻结态端到端:图书馆查书(用打包内那份 1.3 GB 馆藏库真实检索)。

对应报障:「图书馆的查询书本还是无法正常查询显示没有这一本书」。
本脚本用**打包后的 CLI exe** + 假 LLM,让 exe 真去调 library_search,验证:

1. exe 能在自己目录下找到内置的 `crawled/mfindgzyykj.libsp.cn/library.db`
   (CLI 的 workspace 是 cwd,所以这里故意把 cwd 设成临时目录,
   只有当 app_path 回退生效时才能查到——这正是便携版/装完即用的真实场景);
2. 带书名号、版次标注、副标题的查询都能命中,而不是回"没有这一本书"。

断言不依赖 CLI 的终端渲染(成功时 CLI 只打印耗时),而是直接读会话留档
`<exe目录>/sessions/session_*.jsonl` 里的 tool 结果,保证验的是真数据。

用法: python scripts/e2e_frozen_library.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # 同目录的 e2e_probe

from e2e_probe import is_probe_request, refuse_probe  # noqa: E402

APP_DIR = ROOT / "dist" / "引路学长"
CLI_EXE = APP_DIR / "引路学长-CLI.exe"
DB_REL = Path("crawled") / "mfindgzyykj.libsp.cn" / "library.db"

# (用户会怎么问, 结果里必须出现什么)
CASES: list[tuple[str, str]] = [
    ("《C程序设计》", "C程序设计"),
    ("C语言程序设计（第5版）", "C语言程序设计"),
    ("高等数学（上）", "高等数学"),
    ("《三体》", "三体"),
]

STATE = {"query": ""}


def _chunk(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _tool_call_sse(query: str) -> bytes:
    """第一回合:让模型调 library_search。"""
    frag = {"index": 0, "id": "call_lib", "type": "function",
            "function": {"name": "library_search",
                         "arguments": json.dumps({"query": query},
                                                 ensure_ascii=False)}}
    parts = [
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1,
                "model": "fake",
                "choices": [{"index": 0, "delta": {"role": "assistant"},
                             "finish_reason": None}]}),
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1,
                "model": "fake",
                "choices": [{"index": 0,
                             "delta": {"tool_calls": [frag]},
                             "finish_reason": None}]}),
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1,
                "model": "fake",
                "choices": [{"index": 0, "delta": {},
                             "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]
    return b"".join(parts)


def _final_sse() -> bytes:
    parts = [
        _chunk({"id": "2", "object": "chat.completion.chunk", "created": 1,
                "model": "fake",
                "choices": [{"index": 0, "delta": {"content": "已查询。"},
                             "finish_reason": None}]}),
        _chunk({"id": "2", "object": "chat.completion.chunk", "created": 1,
                "model": "fake",
                "choices": [{"index": 0, "delta": {},
                             "finish_reason": "stop"}]}),
        b"data: [DONE]\n\n",
    ]
    return b"".join(parts)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if is_probe_request(raw):
            refuse_probe(self)          # 能力探测不占对话剧本
            return
        try:
            req = json.loads(raw.decode("utf-8"))
            seen_tool_result = any(m.get("role") == "tool"
                                   for m in req.get("messages", []))
        except Exception:                                  # noqa: BLE001
            seen_tool_result = True                        # 解析不了就收尾,别死循环
        payload = _final_sse() if seen_tool_result else _tool_call_sse(STATE["query"])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def _tool_result_from_sessions() -> str:
    """读最新一条会话留档里的 tool 结果。"""
    sdir = APP_DIR / "sessions"
    files = sorted(sdir.glob("session_*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("role") == "tool":
                return str(msg.get("content") or "")
    return ""


def main() -> int:
    if not CLI_EXE.exists():
        print(f"!! 找不到 {CLI_EXE};请先 python scripts/build_exe.py")
        return 1
    db = APP_DIR / DB_REL
    if not db.is_file():
        print(f"!! 打包内没有馆藏库 {db};图书馆查询不可用")
        return 1
    print(f"打包内馆藏库: {db} ({db.stat().st_size/1e9:.2f} GB)")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    config_path = APP_DIR / "config.json"
    config_path.write_text(json.dumps({"permission_mode": "accept_all"},
                                      ensure_ascii=False), encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "YINLUXS_LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "YINLUXS_LLM_API_KEY": "test-key-frozen",
        "YINLUXS_LLM_MODEL": "fake-model",
    }

    failures: list[str] = []
    try:
        for query, expect in CASES:
            STATE["query"] = query
            sdir = APP_DIR / "sessions"
            sdir.mkdir(parents=True, exist_ok=True)
            for old in sdir.glob("session_*.jsonl"):
                old.unlink()

            workspace = Path(tempfile.mkdtemp(prefix="gcode-lib-"))
            subprocess.run([str(CLI_EXE), f"图书馆有没有{query}这本书"],
                           cwd=workspace, capture_output=True, timeout=180,
                           env=env)
            result = _tool_result_from_sessions()
            if expect in result:
                print(f"  ✓ {query:26} → 命中(结果含「{expect}」)")
            else:
                failures.append(query)
                print(f"  ✗ {query:26} → 未命中;结果前 160 字:"
                      f"{result[:160]!r}")
    finally:
        server.shutdown()
        config_path.unlink(missing_ok=True)
        for old in (APP_DIR / "sessions").glob("session_*.jsonl"):
            old.unlink()

    if failures:
        print(f"\n冻结态图书馆查询 FAIL:{len(failures)}/{len(CASES)} 未命中")
        return 1
    print(f"\n=== 冻结态图书馆查询 PASS:{len(CASES)}/{len(CASES)} 全部命中 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
