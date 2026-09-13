"""冻结版沙箱端到端:验证打包后的 exe 真的会拦工作区外访问。

两个场景(都靠假 SSE 服务器脚本化调用 read_file):
  A. sandbox.allow_outside_workspace=false → 工作区外路径必须被拒(工具结果里出现越界错误);
  B. allow_outside_workspace=true          → 同一个路径能读到内容。

用法:
  python scripts/e2e_frozen_sandbox.py
  python scripts/e2e_frozen_sandbox.py --exe dist/引路学长-crawl4ai/引路学长-CLI.exe
"""
from __future__ import annotations

import argparse
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

DEFAULT_EXE = ROOT / "dist" / "引路学长" / "引路学长-CLI.exe"
REQ = {"n": 0}
TOOL_FEEDBACK = {"text": ""}


def _chunk(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _turn_tool_call(path: str) -> bytes:
    args = json.dumps({"path": path}, ensure_ascii=False)
    parts = [
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}),
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {"tool_calls": [
                    {"index": 0, "id": "call_read", "type": "function",
                     "function": {"name": "read_file", "arguments": args}}]},
                    "finish_reason": None}]}),
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]
    return b"".join(parts)


def _turn_text() -> bytes:
    parts = [
        _chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {"content": "看完了。"}, "finish_reason": None}]}),
        _chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        b"data: [DONE]\n\n",
    ]
    return b"".join(parts)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        REQ["n"] += 1
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if REQ["n"] > 1 and raw:
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
                for m in payload.get("messages") or []:
                    if isinstance(m, dict) and m.get("role") == "tool":
                        TOOL_FEEDBACK["text"] += str(m.get("content") or "")
            except json.JSONDecodeError:
                pass
        body = _turn_tool_call(TARGET["path"]) if REQ["n"] == 1 else _turn_text()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


TARGET = {"path": ""}


def _run(exe: Path, workspace: Path, app_dir: Path, port: int, sandbox: dict,
         answer: str = "n") -> str:
    """跑一次 exe,返回模型看到的工具结果文本。

    answer 是喂给权限确认框的输入:沙箱边界即使 accept_all 也会问一次,
    "n" 表示拒绝(被拦下),"y" 表示放行。
    """
    REQ["n"] = 0
    TOOL_FEEDBACK["text"] = ""
    config_path = app_dir / "config.json"
    original = config_path.read_bytes() if config_path.exists() else None
    config_path.write_text(json.dumps({
        "permission_mode": "accept_all",
        "sandbox": sandbox,
    }, ensure_ascii=False), encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "YINLUXS_LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "YINLUXS_LLM_API_KEY": "test-key",
        "YINLUXS_LLM_MODEL": "fake-model",
    }
    try:
        proc = subprocess.run([str(exe), "读一下那个文件"], cwd=str(workspace),
                              capture_output=True, text=True, env=env, input=answer + "\n",
                              encoding="utf-8", errors="replace", timeout=240)
    finally:
        if original is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_bytes(original)
    assert proc.returncode == 0, f"exe 异常退出({proc.returncode}): {proc.stderr[-400:]}"
    return TOOL_FEEDBACK["text"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=str(DEFAULT_EXE))
    args = ap.parse_args()
    exe = Path(args.exe)
    if not exe.exists():
        print(f"!! 找不到 exe: {exe}(先运行 scripts/build_exe.py)")
        return 1
    app_dir = exe.parent

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with tempfile.TemporaryDirectory(prefix="sandbox-frozen-") as tmp:
        workspace = Path(tmp) / "ws"
        workspace.mkdir()
        secret = Path(tmp) / "outside.txt"
        secret.write_text("SENSITIVE-CONTENT", encoding="utf-8")
        TARGET["path"] = str(secret)

        blocked = _run(exe, workspace, app_dir, port,
                       {"enabled": True, "allow_outside_workspace": False}, answer="n")
        allowed = _run(exe, workspace, app_dir, port,
                       {"enabled": True, "allow_outside_workspace": True}, answer="y")

    server.shutdown()
    checks = {
        "工作区外触发确认(accept_all 也拦)": ("拒绝" in blocked),
        "被拒后没有泄露文件内容": "SENSITIVE-CONTENT" not in blocked,
        "放开限制后能读到内容": "SENSITIVE-CONTENT" in allowed,
    }
    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if not all(checks.values()):
        print("---- blocked ----")
        print(blocked[:800])
        print("---- allowed ----")
        print(allowed[:800])
        return 1
    print("=== FROZEN SANDBOX E2E PASS ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
