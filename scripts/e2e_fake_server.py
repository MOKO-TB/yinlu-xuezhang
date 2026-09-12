"""端到端冒烟:启动本地假 OpenAI SSE 服务器,完整跑通 mini-code 一回合。

验证链路:HTTP 请求 → SSE 流式解析 → 工具调用增量聚合 → 权限放行 →
工具执行写文件 → 结果回填 → 第二轮请求 → 最终文本回复。

用法: python scripts/e2e_fake_server.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REQ_COUNT = {"n": 0}


def chunk(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def sse_response_1() -> bytes:
    """回合 1:思考流 + 正文 + 工具调用(参数分三片传输,验证增量聚合)。"""
    parts = [
        chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}),
        chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"reasoning_content": "用户想要一个文件。"}, "finish_reason": None}]}),
        chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"content": "好的,我来创建文件。\n"}, "finish_reason": None}]}),
    ]
    for frag in [
        {"index": 0, "id": "call_e2e", "type": "function",
         "function": {"name": "write_file", "arguments": ""}},
        {"index": 0, "function": {"arguments": "{\"path\": \"e2e_demo.txt\", "}},
        {"index": 0, "function": {"arguments": "\"content\": \"hello e2e\"}"}},
    ]:
        parts.append(chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                            "choices": [{"index": 0, "delta": {"tool_calls": [frag]}, "finish_reason": None}]}))
    parts.append(chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}))
    parts.append(b"data: [DONE]\n\n")
    return b"".join(parts)


def sse_response_2() -> bytes:
    """回合 2:纯文本收尾 + usage 统计。"""
    parts = [
        chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"content": "文件已创建完成。"}, "finish_reason": None}]}),
        chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}}),
        b"data: [DONE]\n\n",
    ]
    return b"".join(parts)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        REQ_COUNT["n"] += 1
        payload = sse_response_1() if REQ_COUNT["n"] == 1 else sse_response_2()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def main() -> None:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    workspace = Path(tempfile.mkdtemp(prefix="minicode-e2e-"))
    config_path = PROJECT_ROOT / "config.json"
    original = config_path.read_bytes() if config_path.exists() else None
    config_path.write_text(json.dumps({
        "base_url": f"http://127.0.0.1:{port}/v1",
        "api_key": "test-key-e2e",
        "model": "fake-model",
        "permission_mode": "accept_all",
    }, ensure_ascii=False), encoding="utf-8")

    os.chdir(workspace)
    sys.argv = ["main.py", "创建 e2e_demo.txt"]
    try:
        import main as entry
        entry.main()
        assert (workspace / "e2e_demo.txt").read_text(encoding="utf-8") == "hello e2e", \
            "工具未真正写出文件"
        print(f"\n=== E2E PASS: 文件已写出,模型请求 {REQ_COUNT['n']} 次 ===")
    finally:
        if original is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_bytes(original)
        server.shutdown()


if __name__ == "__main__":
    main()
