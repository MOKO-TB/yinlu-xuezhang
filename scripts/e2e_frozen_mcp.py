"""冻结版 MCP 端到端:验证打包后的 exe 能连上 MCP 服务器并真的调用外部工具。

链路:
  假 OpenAI SSE(第 1 轮脚本化调用 mcp_demo_echo,第 2 轮文本收尾)
  → 打包 exe 启动 → 读 config.json 的 mcp_servers → 拉起假 MCP 服务器
  → 外部工具进入注册表 → 权限放行(read_only) → tools/call
  → 结果回填 → 收尾回复。

用法:
  python scripts/e2e_frozen_mcp.py
  python scripts/e2e_frozen_mcp.py --exe dist/引路学长-crawl4ai/引路学长-CLI.exe

注:打包版把 config.json 放在 **exe 所在目录**(便携设计),所以这里跑 onedir 的
CLI exe,临时配置写在它旁边、跑完清理;单文件版是 GUI 形态(无控制台),不能这样用。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAKE_MCP = ROOT / "tests" / "fake_mcp_server.py"
DEFAULT_EXE = ROOT / "dist" / "引路学长" / "引路学长-CLI.exe"
REQ = {"n": 0}
# 第 2 轮请求里模型看到的 tool 消息(用来证明 MCP 的返回值真的回填给了模型)
TOOL_FEEDBACK = {"text": ""}


def _chunk(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _turn_tool_call() -> bytes:
    """第 1 轮:调用外部 MCP 工具 mcp_demo_echo。"""
    parts = [
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}),
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {"content": "我用 MCP 工具试一下。\n"},
                             "finish_reason": None}]}),
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {"tool_calls": [
                    {"index": 0, "id": "call_mcp", "type": "function",
                     "function": {"name": "mcp_demo_echo",
                                  "arguments": "{\"message\": \"hi from mcp\"}"}}]},
                    "finish_reason": None}]}),
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]
    return b"".join(parts)


def _turn_text() -> bytes:
    parts = [
        _chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {"content": "MCP 调用完成。"}, "finish_reason": None}]}),
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
        body = _turn_tool_call() if REQ["n"] == 1 else _turn_text()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _write_config(workspace: Path) -> Path:
    """只写 MCP 与权限配置;模型端点靠 YINLUXS_LLM_* 环境变量临时指向假服务器。"""
    cfg = {
        "permission_mode": "accept_all",
        "mcp_servers": [{
            "id": "demo",
            "command": sys.executable,          # 开发环境 python(脚本由它运行)
            "args": [str(FAKE_MCP)],
            "read_only": True,                  # 免确认,便于无人值守验证
            "description": "假 MCP 服务器",
        }],
    }
    path = workspace / "config.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=str(DEFAULT_EXE))
    args = ap.parse_args()
    exe = Path(args.exe)
    if not exe.exists():
        print(f"!! 找不到 exe: {exe}(先运行 scripts/build_exe.py)")
        return 1

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}/v1"

    with tempfile.TemporaryDirectory(prefix="mcp-frozen-") as tmp:
        workspace = Path(tmp)
        target_exe = exe
        cleanup_config = None
        if (exe.parent / "_internal").is_dir():
            # onedir:不能整份搬(太大),就地跑,配置写在 exe 旁,跑完清理
            target_exe = exe
            workspace = exe.parent
            cleanup_config = workspace / "config.json"
            cleanup_config.unlink(missing_ok=True)
        else:
            # onefile:整份拷进临时目录,源目录不受污染
            target_exe = workspace / exe.name
            shutil.copy2(exe, target_exe)
        _write_config(workspace)
        env = {
            **os.environ,
            "YINLUXS_LLM_BASE_URL": base_url,
            "YINLUXS_LLM_API_KEY": "test-key",
            "YINLUXS_LLM_MODEL": "fake-model",
        }
        try:
            proc = subprocess.run(
                [str(target_exe), "用 MCP 工具回显 hi from mcp"],
                cwd=str(workspace), capture_output=True, text=True, env=env,
                encoding="utf-8", errors="replace", timeout=300,
            )
        finally:
            if cleanup_config is not None:
                cleanup_config.unlink(missing_ok=True)
                for extra in ("sessions", "memory"):
                    shutil.rmtree(workspace / extra, ignore_errors=True)
    server.shutdown()
    out = (proc.stdout or "") + (proc.stderr or "")

    checks = {
        "退出码为 0": proc.returncode == 0,
        "发现并使用 MCP 工具": "mcp_demo_echo" in out,
        "MCP 返回值回填给模型": "echo: hi from mcp" in TOOL_FEEDBACK["text"],
    }
    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if not all(checks.values()):
        print("---- 输出 ----")
        print(out[-2000:])
        return 1
    print("=== FROZEN MCP E2E PASS ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
