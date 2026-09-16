"""端到端冒烟:启动本地假 OpenAI SSE 服务器,完整跑通 mini-code 一回合。

验证链路:HTTP 请求 → SSE 流式解析 → 工具调用增量聚合 → 权限放行 →
工具执行写文件 → 结果回填 → 第二轮请求 → 最终文本回复。

用法:
  python scripts/e2e_fake_server.py                 # 默认剧本:模型调 write_file 真写文件 + token 记账
  $env:E2E_SCRIPT='browser'; python scripts/e2e_fake_server.py
      # 浏览器剧本:模型真调 browser_open 开本服务器自带的 JS 渲染页。
      # 验收靠"第二轮请求体里必须有只由 JS 生成的哨兵文本"——静态抓取拿不到它,
      # 所以这条能证明"真浏览器渲染 → 工具结果回填模型"整条链是通的(不花 token)。
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
sys.path.insert(0, str(Path(__file__).resolve().parent))   # 同目录的 e2e_probe

from e2e_probe import is_probe_request, refuse_probe  # noqa: E402

REQ_COUNT = {"n": 0}
# 剧本:默认 write_file 两步(历史行为);设成 browser 则跑"模型真调浏览器工具"那条链路。
# 读环境变量要在**请求时**(_script),不能只在导入时读一次:
# e2e_frozen 先 import 本模块、后设 E2E_SCRIPT,导入时取值会拿到默认剧本。
SCRIPT = os.environ.get("E2E_SCRIPT", "write_file")


def _script() -> str:
    return os.environ.get("E2E_SCRIPT", "write_file")
# 假服务器收到的每个请求体(解析后重新序列化,免中文明文被 \uXXXX 逃掉)
SEEN_REQUESTS: list[str] = []

# 只由 JS 生成的哨兵:静态抓取拿不到,能取到就说明真走了浏览器渲染
E2E_PAGE = """<!doctype html><meta charset="utf-8"><title>E2E 浏览器页</title>
<h1>静态可见</h1><div id="app">loading</div>
<script>setTimeout(function(){
  document.getElementById('app').innerHTML =
    '<p id="e2e-done">E2E_BROWSER_SENTINEL \u00b7 \u53ea\u6709 JS \u6e32\u67d3\u540e\u624d\u5b58\u5728</p>';
}, 400);</script>"""


def chunk(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _tool_call_round(name: str, args_frags: list[str], *, first: bool) -> bytes:
    """通用:思考流 + 正文 + 一个工具调用(参数分片传输,验证增量聚合)。"""
    rid = "1" if first else "3"
    parts = [
        chunk({"id": rid, "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}),
        chunk({"id": rid, "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"content": "好的,我来执行工具。\n"},
                            "finish_reason": None}]}),
    ]
    frags = [dict(f, index=0) for f in args_frags]
    frags[0].update({"id": f"call_{rid}", "type": "function"})
    frags[0]["function"] = dict(frags[0].get("function", {}), name=name)
    for frag in frags:
        parts.append(chunk({"id": rid, "object": "chat.completion.chunk", "created": 1, "model": "fake",
                            "choices": [{"index": 0, "delta": {"tool_calls": [frag]},
                                         "finish_reason": None}]}))
    parts.append(chunk({"id": rid, "object": "chat.completion.chunk", "created": 1, "model": "fake",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}))
    parts.append(b"data: [DONE]\n\n")
    return b"".join(parts)


def sse_response_browser() -> bytes:
    """浏览器回合:让模型真的调 browser_open 打开本地那个 JS 渲染页。

    地址由 main 里根据本地服务端口注入(BROWSER_TARGET)。
    """
    args = json.dumps({"url": os.environ["BROWSER_TARGET"], "wait_ms": 10000,
                       "wait_for": "#e2e-done",
                       "reason": "端到端验收:打开本地页面看 JS 渲染"},
                      ensure_ascii=False)
    return _tool_call_round("browser_open", [{"function": {"arguments": args}}], first=True)


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
    def do_GET(self):
        """同一个服务兼作本地被测页(浏览器剧本用)。"""
        body = E2E_PAGE.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass          # 页面/浏览器收尾断连是常态,不是验收失败

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if is_probe_request(raw):
            refuse_probe(self)          # 探测不占剧本、不进 SEEN_REQUESTS
            return
        script = _script()
        if script == "browser":
            try:                       # 记下请求体,后面断言工具结果真回了模型
                SEEN_REQUESTS.append(
                    json.dumps(json.loads(raw.decode("utf-8", "replace")), ensure_ascii=False))
            except Exception:
                SEEN_REQUESTS.append(raw.decode("utf-8", "replace"))
        REQ_COUNT["n"] += 1
        if script == "browser":
            # 浏览器剧本:第一回合开页面,第二回合收尾(不记 usage 断言)
            payload = (sse_response_browser() if REQ_COUNT["n"] == 1
                       else sse_response_2())
        else:
            payload = sse_response_1() if REQ_COUNT["n"] == 1 else sse_response_2()
        self._send_bytes(200, payload, "text/event-stream")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass          # 客户端提前断开是常态,不是验收失败

    def log_message(self, *args):
        pass


def _assert_browser() -> None:
    """浏览器剧本的验收:假服务器在**第二轮收到的请求体**里必须看到
    工具返回的渲染哨兵——这才能证明真浏览器把 JS 渲染后的内容回了模型。"""
    blob = SEEN_REQUESTS[-1] if len(SEEN_REQUESTS) > 1 else ""
    assert len(SEEN_REQUESTS) > 1, \
        f"模型只被请求了 {len(SEEN_REQUESTS)} 次(工具调用没走完)"
    assert "E2E_BROWSER_SENTINEL" in blob, \
        "第二轮请求里没有哨兵文本(浏览器没真渲染,或工具结果没回填给模型)"
    assert "已打开" in blob, "browser_open 的文案没回填给模型"
    assert "独立实例" in blob or "登录态" in blob, "没告诉模型走的哪条接入路线"
    print(f"\n=== E2E PASS(browser):模型真调 browser_open、拿到 JS 渲染后的正文并回填,"
          f"请求 {REQ_COUNT['n']} 次 ===")


def main() -> None:
    server = HTTPServer(("127.0.0.1", 0), Handler)   # 同一个服务兼作被测页(浏览器剧本)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    workspace = Path(tempfile.mkdtemp(prefix="minicode-e2e-"))
    config_path = PROJECT_ROOT / "config.json"
    original = config_path.read_bytes() if config_path.exists() else None
    # v0.2 扁平格式:顺带锁住"旧配置不被静默换成内置模型"这条回归
    config_path.write_text(json.dumps({
        "base_url": f"http://127.0.0.1:{port}/v1",
        "api_key": "test-key-e2e",
        "model": "fake-model",
        "permission_mode": "accept_all",
    }, ensure_ascii=False), encoding="utf-8")

    # 隔离:凭据文件与记账库都必须落在临时目录。
    # 否则 load_config 的凭据迁移会把测试 key 写进真实
    # ~/.yinluxs/credentials.json(曾经真的污染过用户主目录)。
    from minicode import config as config_mod
    from minicode import usage as usage_mod
    original_cred_path = config_mod.CREDENTIALS_PATH
    config_mod.CREDENTIALS_PATH = workspace / "credentials.json"
    usage_mod.set_db_path(workspace / "token-usage.db")

    os.chdir(workspace)
    script = _script()
    sys.argv = ["main.py", "创建 e2e_demo.txt" if script != "browser" else "用浏览器看看这个页面"]
    if script == "browser":
        os.environ["BROWSER_TARGET"] = f"http://127.0.0.1:{port}/e2e"
    try:
        import main as entry
        entry.main()
        if script == "browser":
            _assert_browser()
        else:
            assert (workspace / "e2e_demo.txt").read_text(encoding="utf-8") == "hello e2e", \
                "工具未真正写出文件"
            # v0.9.03:真 usage(12+6)必须被记进本地统计
            stats = usage_mod.statistics(1)
            assert stats["ok"] and stats["summary"]["total"] == 18, stats["summary"]
            print(f"\n=== E2E PASS: 文件已写出,模型请求 {REQ_COUNT['n']} 次,"
                  f"token 记账 {stats['summary']['total']} ===")
    finally:
        config_mod.CREDENTIALS_PATH = original_cred_path
        usage_mod.set_db_path(None)
        if original is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_bytes(original)
        server.shutdown()


if __name__ == "__main__":
    main()
