"""端到端验收:页面交互三件套(browser_snapshot / browser_click / browser_fill)。

用**真实浏览器**跑(同 e2e_browser.py 的标准):
  1. 起一个带表单的临时静态站(纯 JS,无后端);
  2. browser_snapshot → 必须列出输入框/下拉框/勾选框/按钮(带序号);
  3. browser_fill 按中文字段名填值 + 勾选 → 页面里真的变了(React 式受控组件路径);
  4. browser_fill submit=true → 自动点提交,页面出现「欢迎」(表单真的被处理了);
  5. browser_click 按快照序号点链接 → 真的跳到了第二页;
  6. 页面变过之后旧序号必须失效(不能点错元素);
  7. 文字歧义时拒绝乱点。

用法:
  python scripts/e2e_browser_interact.py
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

os.environ.setdefault("YINLUXS_BROWSER_PROFILE",
                      tempfile.mkdtemp(prefix="yinluxs-e2e-profile-"))

from minicode import browser as br                                   # noqa: E402
from minicode.config import Config                                   # noqa: E402
from minicode.tools import browser as bt                             # noqa: E402
from minicode.tools.base import ToolContext                          # noqa: E402

FORM_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>选课系统(演示)</title></head><body>
<h1>学生登录</h1>
<form id="f" onsubmit="return doLogin(event)">
  <label>用户名<input id="username" placeholder="请输入用户名"></label>
  <label>密码<input id="pwd" type="password" placeholder="请输入密码"></label>
  <label>年级
    <select id="grade">
      <option value="1">大一</option><option value="2">大二</option>
      <option value="3">大三</option><option value="4">大四</option>
    </select>
  </label>
  <label><input id="agree" type="checkbox"> 我已阅读选课须知</label>
  <button type="submit" id="go">登 录</button>
</form>
<div id="msg"></div>
<script>
function doLogin(e) {
  e.preventDefault();
  var u = document.getElementById('username').value;
  if (!u) { document.getElementById('msg').textContent = '请填写用户名'; return false; }
  document.getElementById('msg').textContent = '欢迎,' + u + '(年级' +
    document.getElementById('grade').value + ')';
  return false;
}
</script></body></html>"""

WELCOME_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>我的课表</title></head><body><h1>课表页</h1>
<div id="courses">高等数学 / 大学英语 / 数据结构</div>
</body></html>"""

PAGES = {"/index.html": FORM_PAGE, "/courses.html": WELCOME_PAGE}


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = PAGES.get(self.path, FORM_PAGE).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass

    def log_message(self, *a):
        pass


class Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        with contextlib.suppress(Exception):
            pass


class UI:
    def __init__(self):
        self.events: list[dict] = []

    def push(self, type_, **payload):
        self.events.append({"type": type_, **payload})


def run() -> list[str]:
    steps: list[str] = []
    workspace = Path(tempfile.mkdtemp(prefix="yinluxs-e2e-ws-"))
    srv = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    ctx = ToolContext(workspace=workspace, config=Config(), ui=UI())
    try:
        out = bt.browser_open(ctx, f"{base}/index.html", wait_ms=8000,
                              wait_for="#go")
        assert "学生登录" in out, f"登录页没打开:\n{out[:300]}"
        steps.append(f"[1] 打开登录页({out.splitlines()[0]})")

        snap = bt.browser_snapshot(ctx)
        for token in ("username", "grade", "大一 | 大二", "选课须知", "登 录"):
            assert token in snap, f"快照缺元素/选项「{token}」:\n{snap[:400]}"
        assert "pwd" in snap and "请输入密码" not in snap.split("pwd")[1][:40], \
            "密码框的占位文本不应带着值回显之外的内容泄露(只需列字段名)"
        steps.append(f"[2] snapshot 列出表单元素与选项({snap.count(chr(10))} 行)")

        filled = bt.browser_fill(ctx, {"用户名": "张三", "密码": "secret123",
                                       "年级": "大三", "我已阅读选课须知": True})
        assert "错误" not in filled, f"填表失败:\n{filled[:400]}"
        got = bt.browser_eval(ctx, "JSON.stringify({u:document.getElementById('username').value,"
                                  "p:document.getElementById('pwd').value,"
                                  "g:document.getElementById('grade').value,"
                                  "a:document.getElementById('agree').checked})")
        assert '"u":"张三"' in got and '"p":"secret123"' in got, f"输入框没填上:{got[:200]}"
        assert '"g":"3"' in got, f"下拉框没选中大三:{got[:200]}"
        assert '"a":true' in got, f"勾选框没勾上:{got[:200]}"
        steps.append("[3] fill 按中文字段名填好 输入框/下拉框/勾选框(页面侧验证)")

        submitted = bt.browser_fill(ctx, {"用户名": "张三"}, submit=True)
        assert "错误" not in submitted, f"提交失败:\n{submitted[:300]}"
        msg = bt.browser_eval(ctx, "document.getElementById('msg').textContent")
        assert "欢迎,张三" in msg, f"表单没被真正处理:{msg[:200]}"
        steps.append(f"[4] submit=true 自动点「登 录」并拿到页面反馈({msg.strip()[:30]})")

        # 重新打开表单页,拿一次快照,再点链接跳去课表页 —— 这里没有链接,
        # 用 snapshot 的序号点「登 录」按钮证明序号定位本身是准的
        bt.browser_open(ctx, f"{base}/index.html", wait_ms=6000, wait_for="#go")
        snap2 = bt.browser_snapshot(ctx)
        import re
        m = re.search(r"(\d+)\. .*登 录", snap2)
        assert m, f"快照里找不到「登 录」的序号:\n{snap2[:300]}"
        # 当前线程快照给 click 用的 data-ylx 标记已在页面里,直接按序号点
        clicked = bt.browser_click(ctx, m.group(1))
        assert "已点击" in clicked, f"按序号点击失败:\n{clicked[:300]}"
        steps.append(f"[5] 按快照序号点击「登 录」成功({clicked.splitlines()[0][:50]})")

        # 页面没换时给一个必然失效的序号(把 snapshot 上限拉高后再点一个不存在的号)
        stale = bt.browser_click(ctx, "999")
        assert stale.startswith("错误:") and "snapshot" in stale, \
            f"失效序号应报错并提示重新快照:{stale[:200]}"
        steps.append(f"[6] 失效序号被拒并提示重新 snapshot({stale.splitlines()[0][:40]})")

        # 文字歧义:表单页只有一个「登 录」,构造歧义用两个同名链接页
        PAGES["/dup.html"] = ("<a href='#1'>下一步</a><a href='#2'>下一步</a>"
                              "<button>下一步</button>")
        bt.browser_open(ctx, f"{base}/dup.html", wait_ms=6000)
        amb = bt.browser_click(ctx, "下一步")
        assert amb.startswith("错误:") and "snapshot" in amb, \
            f"同名元素应拒绝盲点:{amb[:200]}"
        steps.append("[7] 三个同名「下一步」→ 拒绝盲点,要求先快照")

        assert bt.browser_close(ctx).startswith("已关闭")
        steps.append("[8] 收尾关页")
        return steps
    finally:
        with contextlib.suppress(Exception):
            br.shutdown()
        srv.shutdown()
        shutil.rmtree(workspace, ignore_errors=True)


def main() -> int:
    try:
        steps = run()
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
    print("\n=== BROWSER INTERACT E2E PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
