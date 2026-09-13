"""网页版端到端验收:假 OpenAI SSE 服务器 + 真实 HTTP 客户端。

验证链路:
    浏览器语义的 HTTP → POST /api/send_task → 引擎回合(流式 + 工具调用)
    → 只读工具真跑(search_knowledge 命知识库)→ 结果回填 → 模型收尾
    → SSE 把 text_delta / tool_start / tool_done / finish_stream / busy 推给客户端

同时验收安全边界(这两条比"能跑通"更重要):
    ① 越权调用 read_file / bash 必须被拒;
    ② 对外门面不得吐出工作区路径、内网模型端点、Key。

用法:
    py -3.12 scripts/e2e_web.py
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REQ_COUNT = {"n": 0}


def chunk(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def sse_tool_turn() -> bytes:
    """回合 1:先说一句,再调 search_knowledge 查知识库。"""
    parts = [
        chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"role": "assistant",
                                                  "content": "我先查一下知识库。\n"},
                            "finish_reason": None}]}),
        chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"tool_calls": [
                   {"index": 0, "id": "call_1", "type": "function",
                    "function": {"name": "search_knowledge", "arguments": ""}}]},
                   "finish_reason": None}]}),
        chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"tool_calls": [
                   {"index": 0, "function": {"arguments": "{\"query\": \"图书馆\"}"}}]},
                   "finish_reason": None}]}),
        chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]
    return b"".join(parts)


def sse_text_turn() -> bytes:
    """回合 2:纯文本收尾。"""
    return b"".join([
        chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"content": "图书馆在旧图那栋,学弟。"},
                            "finish_reason": None}]}),
        chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        b"data: [DONE]\n\n",
    ])


class FakeLLM(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        REQ_COUNT["n"] += 1
        payload = sse_tool_turn() if REQ_COUNT["n"] == 1 else sse_text_turn()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *args):
        pass


class Client:
    def __init__(self, port: int):
        self.port = port
        self.cookie = ""

    def post(self, name: str, args: dict | None = None, raw: str | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        body = raw if raw is not None else json.dumps(args or {}, ensure_ascii=False)
        conn.request("POST", "/api/" + name, body=body.encode("utf-8"), headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        set_cookie = resp.getheader("Set-Cookie")
        status = resp.status
        conn.close()
        if set_cookie and not self.cookie:
            self.cookie = set_cookie.split(";")[0]
        return status, (json.loads(data.decode("utf-8")) if data else {})

    def get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Cookie": self.cookie} if self.cookie else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        status, ctype = resp.status, resp.getheader("Content-Type")
        conn.close()
        return status, ctype, data.decode("utf-8", "replace")


def collect_events(port: int, cookie: str, holder: dict) -> None:
    """后台读 SSE,直到收到 busy=False(回合真正收尾)。"""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        conn.request("GET", "/api/events", headers={"Cookie": cookie})
        resp = conn.getresponse()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            line = resp.fp.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text.startswith("data: "):
                continue
            event = json.loads(text[len("data: "):])
            holder["events"].append(event)
            if event.get("type") == "busy" and event.get("value") is False:
                break
        conn.close()
    except Exception as e:
        holder["error"] = e
    finally:
        holder["done"].set()


def main() -> int:
    llm = HTTPServer(("127.0.0.1", 0), FakeLLM)
    llm_port = llm.server_address[1]
    threading.Thread(target=llm.serve_forever, daemon=True).start()

    # 用环境变量把模型端点临时指向假服务器(不落盘,不改 config.json)
    os.environ["YINLUXS_LLM_BASE_URL"] = f"http://127.0.0.1:{llm_port}/v1"
    os.environ["YINLUXS_LLM_MODEL"] = "fake-model"
    os.environ["YINLUXS_LLM_API_KEY"] = "test-key-e2e"

    from minicode.config import load_config
    from minicode.paths import resource_path
    from minicode.web_server import create_server

    config = load_config()
    workspace = PROJECT_ROOT                      # 真工作区:kb/ 有真语料
    tmp = Path(tempfile.mkdtemp(prefix="yinluxs-web-e2e-"))
    httpd = create_server(config, workspace, host="127.0.0.1", port=0,
                          frontend_dir=resource_path("frontend"),
                          sessions_root=tmp / "web-sessions",
                          config_path=tmp / "config.json")
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(("  [OK]   " if ok else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    try:
        client = Client(port)
        print(f"服务已起:http://127.0.0.1:{port}(假模型 :{llm_port})\n")

        print("1) 静态页面与 shim 注入")
        status, ctype, body = client.get("/")
        check("首页 200", status == 200, f"status={status}")
        check("注入 bridge-web.js", "/bridge-web.js" in body)
        check("页面未依赖 pywebview", "pywebview.min.js" not in body)

        print("2) 访客握手与初始化信息")
        status, hello = client.post("hello")
        check("hello ok", status == 200 and hello.get("ok"), str(hello))
        check("下发访客 cookie", bool(client.cookie), client.cookie)
        status, info = client.post("get_info")
        check("get_info ok", info.get("ok", True) is not False)
        check("session_key 存在", bool(info.get("session_key")))
        check("不泄露工作区路径", info.get("workspace") == "", repr(info.get("workspace")))
        check("不泄露内网模型端点", info.get("base_url") == "", repr(info.get("base_url")))
        check("不泄露密钥", "test-key-e2e" not in json.dumps(info, ensure_ascii=False))

        print("3) 只读白名单与越权拒绝")
        status, payload = client.post("bash", {"command": "echo hi"})
        check("bash 不可用", payload.get("ok") is False, str(payload))
        status, payload = client.post("read_file", {"path": "config.local.json"})
        check("read_file 不可用", payload.get("ok") is False, str(payload))
        status, payload = client.post(
            "get_asset", {"rel_path": "config.local.json"})
        check("get_asset 不是文件读取原语", payload.get("ok") is False, str(payload))
        status, payload = client.post("get_providers")
        check("密钥管理不可用", payload.get("ok") is False)
        status, payload = client.post("get_info", raw='{"x":1}')
        check("未知参数不致命", status in (200, 400), f"status={status}")

        print("4) 跨站请求拦截")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        conn.request("POST", "/api/get_info", body=b"{}",
                     headers={"Content-Type": "application/json",
                              "Origin": "http://evil.example"})
        evil = conn.getresponse()
        check("外站 Origin 被拒", evil.status == 403, f"status={evil.status}")
        evil.read()
        conn.close()

        print("5) 完整回合:流式 + 工具调用 + SSE 推送")
        holder = {"events": [], "done": threading.Event(), "error": None}
        threading.Thread(target=collect_events, args=(port, client.cookie, holder),
                         daemon=True).start()
        time.sleep(0.3)
        status, sent = client.post("send_task", {"text": "图书馆在哪?"})
        check("send_task ok", status == 200 and sent.get("ok"), str(sent))
        finished = holder["done"].wait(timeout=25)
        check("回合结束(收到 busy=False)", finished, "" if finished else "25 秒内没收到")
        check("SSE 无异常", holder["error"] is None,
              "" if holder["error"] is None else str(holder["error"]))

        events = holder["events"]
        types = [e.get("type") for e in events]
        tool_starts = [e for e in events if e.get("type") == "tool_start"]
        tool_dones = [e for e in events if e.get("type") == "tool_done"]
        text = "".join(e.get("text", "") for e in events if e.get("type") == "text_delta")

        check("有流式正文", bool(text), repr(text[:40]))
        check("模型收尾文本到了", "旧图" in text, repr(text[-40:]))
        check("工具卡开始", any(e.get("name") == "search_knowledge" for e in tool_starts),
              str([e.get("name") for e in tool_starts]))
        check("工具执行成功", bool(tool_dones) and all(e.get("ok") for e in tool_dones),
              str(tool_dones))
        check("busy 事件成对", types.count("busy") >= 2, str(types))
        check("回合结束事件", "finish_stream" in types)

        print("6) 会话留档只落在访客自己的目录")
        vid = client.cookie.split("=", 1)[1]
        visitor_dir = tmp / "web-sessions" / vid
        logs = list(visitor_dir.glob("session_*.jsonl")) if visitor_dir.is_dir() else []
        check("留档写在访客私有目录", len(logs) == 1, str(visitor_dir))
        check("留档目录取自 --sessions-dir",
              str(visitor_dir.resolve()).startswith(str(tmp.resolve())))
        print(f"\n模型请求次数:{REQ_COUNT['n']}")
    finally:
        httpd.shutdown()
        httpd.server_close()
        llm.shutdown()

    if failures:
        print(f"\n=== E2E FAIL:{len(failures)} 项未通过 -> {failures} ===")
        return 1
    print("\n=== E2E PASS:网页版只读问答链路与安全边界均通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
