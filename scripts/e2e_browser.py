"""端到端验收:用**真实浏览器**跑一遍浏览器能力(不用假连接)。

覆盖轻量版打包后真正要走的那条路:
  1. 本机起一个临时静态站(正文由 JS 延迟渲染,验证「渲染后再取」);
  2. browser_open → 必须拿到 JS 渲染后的内容(静态抓取拿不到的那部分);
  3. browser_eval → 从页面里精确取数;
  4. browser_screenshot → 落盘真 PNG + 给 GUI 推事件;
  5. 两个线程并发各开一页,互不串台(多会话并发时必须成立);
  6. 安全:file:// 必须被拒(取本机文件要走 read_file);
  7. browser_close / browser_status 收尾。

用法:
  python scripts/e2e_browser.py            # 自起独立实例(headless)
  python scripts/e2e_browser.py --attach   # 改用已开远程调试的浏览器(带登录态)
"""
from __future__ import annotations

import contextlib
import http.server
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 独立实例的 profile 放临时目录,别污染用户的真实浏览器配置
os.environ.setdefault("YINLUXS_BROWSER_PROFILE",
                      tempfile.mkdtemp(prefix="yinluxs-e2e-profile-"))

from minicode import browser as br                                   # noqa: E402
from minicode.config import Config                                   # noqa: E402
from minicode.tools import browser as bt                             # noqa: E402
from minicode.tools.base import ToolContext                          # noqa: E402

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>引路学长 E2E</title></head>
<body><h1>静态标题</h1><div id="app">loading</div>
<script>
setTimeout(function(){
  document.getElementById('app').innerHTML =
    '<table id="t"><tr><th>书名</th><th>索书号</th></tr>' +
    '<tr><td>三体</td><td>I247.5/1</td></tr>' +
    '<tr><td>活着</td><td>I247.5/2</td></tr></table>';
  document.title = '引路学长 E2E(已渲染)';
}, 400);
</script></body></html>"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = PAGE.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass          # 浏览器/子代理提前关页或收尾断连,不是验收失败

    def log_message(self, *a):        # 安静
        pass


class Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):   # 同上:不要把 traceback 刷到 stderr
        with contextlib.suppress(Exception):
            pass


def serve():
    srv = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/index.html"


class UI:
    def __init__(self):
        self.events: list[dict] = []

    def push(self, type_, **payload):
        self.events.append({"type": type_, **payload})


def run(attach: bool) -> list[str]:
    """跑完全链路,返回每一步的一行说明(失败就 assert 抛出来)。"""
    steps: list[str] = []
    workspace = Path(tempfile.mkdtemp(prefix="yinluxs-e2e-ws-"))
    srv, url = serve()
    ui = UI()
    ctx = ToolContext(workspace=workspace, config=Config(), ui=ui)
    mode = "attach" if attach else "auto"
    try:
        info = br.health()
        steps.append(f"[0] 自检 ok={info['ok']} mode={info['mode']} "
                     f"browser={info['browser']}")
        assert info["ok"], f"浏览器能力不可用:{info['detail']}"

        out = bt.browser_open(ctx, url, wait_ms=10000, wait_for="#t", mode=mode)
        assert "已渲染" in out, f"标题不是 JS 改过的值(没等到渲染):\n{out[:300]}"
        assert "三体" in out and "I247.5/1" in out, f"JS 生成的表格没进正文:\n{out[:300]}"
        assert "静态标题" in out, "原本就在 DOM 里的内容丢了"
        steps.append(f"[1] browser_open 拿到渲染后正文({len(out)} 字符, "
                     f"{out.splitlines()[2]})")

        js = ("JSON.stringify([...document.querySelectorAll('#t tr')].slice(1)"
              ".map(tr=>[...tr.cells].map(td=>td.textContent)))")
        got = bt.browser_eval(ctx, js)
        assert "三体" in got and "活着" in got, f"取数失败:{got[:200]}"
        steps.append(f"[2] browser_eval 取到表格({got.replace(chr(10), ' ')[:90]})")

        shot = bt.browser_screenshot(ctx, path="shots/e2e.png")
        png = workspace / "shots" / "e2e.png"
        assert png.exists(), "截图文件没落盘"
        raw = png.read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) > 800, f"不是真 PNG({len(raw)})"
        assert "![](shots/e2e.png)" in shot, "没教模型怎么把图写进回答"
        pushed = [e for e in ui.events if e["type"] == "browser"]
        assert pushed and pushed[-1]["op"] == "shot", "没给 GUI 推截图事件"
        steps.append(f"[3] 截图 {len(raw)} 字节 PNG 落盘,GUI 收到 {len(pushed)} 条事件")

        # 并发两线程:多会话/子代理同时用浏览器时不能串台
        gate = threading.Barrier(2)
        seen: dict[str, tuple] = {}
        srv2, url2 = serve()

        def worker(name: str, target: str):
            o = bt.browser_open(ctx, target, wait_ms=10000, wait_for="#t", mode=mode)
            sess = br.current_info()
            gate.wait(timeout=20)
            seen[name] = (sess["target_id"], sess["url"], "三体" in o)

        try:
            threads = [threading.Thread(target=worker, args=("a", url)),
                       threading.Thread(target=worker, args=("b", url2))]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=90)
        finally:
            srv2.shutdown()
        assert len(seen) == 2, "并发线程没跑完"
        assert all(v[2] for v in seen.values()), f"并发取页面内容丢了:{seen}"
        assert seen["a"][0] != seen["b"][0], "两个会话抢了同一个标签页"
        steps.append(f"[4] 两线程并发各自独立标签页({seen['a'][0]} / {seen['b'][0]})")

        denied = bt.browser_open(ctx, "file:///C:/Windows/win.ini")
        assert "不允许" in denied or "不合法" in denied, f"file:// 没被拒:{denied[:160]}"
        steps.append(f"[5] file:// 被拒({denied.splitlines()[0][:60]})")

        assert bt.browser_close(ctx).startswith("已关闭"), "关页面没确认"
        assert "没有打开的页面" in bt.browser_close(ctx), "重复关闭应报告没页面"
        status = bt.browser_status(ctx)
        assert "可用" in status, f"状态自检不对:{status[:120]}"
        steps.append(f"[6] 关页面 + 收尾自检({status[:60]})")
        return steps
    finally:
        with contextlib.suppress(Exception):
            br.shutdown()
        srv.shutdown()
        shutil.rmtree(workspace, ignore_errors=True)


def main() -> int:
    attach = "--attach" in sys.argv
    try:
        steps = run(attach)
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception as exc:
        print(f"FAIL({type(exc).__name__}): {exc}")
        return 1
    finally:
        with contextlib.suppress(Exception):
            shutil.rmtree(os.environ["YINLUXS_BROWSER_PROFILE"], ignore_errors=True)
    for s in steps:
        print(s)
    print("\n=== BROWSER E2E PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
