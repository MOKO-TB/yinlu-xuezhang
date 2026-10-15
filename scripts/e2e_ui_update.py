# -*- coding: utf-8 -*-
"""v0.9.176「发现新版本」这张卡的真浏览器验证(用户八张截图点来的那一件)。

跑法(项目根目录,**要带 utf-8**,否则脚本自己那句 ✓ 在 GBK 控制台炸):

    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_update.py

钉的是"看得见"这一半 —— 后端那半有 `tests/test_update_flow.py` 管,但用户报的原话是
**"有更新应该自己推送出来,而不是用户自己去点查询更新"**,那是一句界面主张,
只有真渲染能证。六条断言:

1. 一条 `update_prompt` 事件进来 ⇒ 卡**自己弹开**、版本号旁那枚「更新」徽章显形
   (不是只躺在聊天栏顶部那条会被挤掉的提示栏里);
2. 有适配本机的发布件 ⇒ 「下载更新」是**亮**的;没有 ⇒ 它是灰的并给得出为什么;
3. 点下载 ⇒ 进度区显形,`update_state` 报 40% ⇒ 条子与文字都跟着动;
4. 下完且 **sha256 核对通过** ⇒ 按钮变「运行安装程序」;
5. 下完但**没核对上**(verified=none/mismatch) ⇒ 按钮**是灰的** —— 这条是这一路
   真正的红线,它不许只在 Python 层成立而界面上照样能点;
6. 「跳过此版本」与「查看历史更新内容」真的敲到后端那两只口子上,历史一版一行、
   正在用的那一版有标记。

浏览器那一侧的启动一律走 `scripts/e2e_launch.py`(那份"自己抄一条退路"的账有闸管着)。
"""
from __future__ import annotations

import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

STUB_JS = r"""
window.__E2E_CALLS__ = [];
function __spy(name, out) {
  return function () {
    window.__E2E_CALLS__.push(name);
    return Promise.resolve(typeof out === "function" ? out() : out);
  };
}
window.__API__ = {
  get_info: () => Promise.resolve({
    version: "0.9.175", app_name: "引路学长",
    permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    session_key: "s1", model: "deepseek-v4-flash", active_provider: "gzasc",
    providers: [{id: "gzasc", name: "广州应用科技学院",
                 model: "deepseek-v4-flash", has_key: true}],
    api_key_configured: true, workspace: "D:/demo", workspace_name: "demo",
    workspace_is_default: false,
  }),
  check_update_now: () => Promise.resolve({ok: true, current: "0.9.175", why: "",
    update: {version: "0.9.176", date: "2026-10-16", notes: "## 本次更新",
             truncated: false, url: "https://github.com/x/releases/tag/v0.9.176",
             asset: {name: "yinlu-xuezhang-0.9.176-setup.exe",
                     url: "https://github.com/x/download/a.exe", size: 900000000}}}),
  update_download: __spy("update_download", {ok: true, started: true,
    version: "0.9.176", name: "yinlu-xuezhang-0.9.176-setup.exe", total: 900000000}),
  update_download_state: __spy("update_download_state", {state: "idle"}),
  update_download_cancel: __spy("update_download_cancel", {ok: true, why: "已取消"}),
  update_install: __spy("update_install", {ok: true, why: "安装程序已打开"}),
  update_skip: __spy("update_skip", {ok: true, skipped: "0.9.176", why: "已跳过这一版"}),
  update_history: __spy("update_history", {ok: true, why: "", releases: [
    {version: "0.9.176", date: "2026-10-16", notes: "就地更新", truncated: false},
    {version: "0.9.175", date: "2026-10-15", notes: "冷启动与 MCP", truncated: true},
    {version: "0.9.174", date: "2026-10-14", notes: "浅色主题", truncated: false}]}),
  open_url: __spy("open_url", {ok: true}),
};
window.pywebview = { api: new Proxy({}, {
  get: function (_t, k) {
    if (window.__API__[k]) return window.__API__[k];
    return function () {
      window.__E2E_CALLS__.push(String(k));
      return Promise.resolve({});
    };
  }})};
window.__stubReady = true;
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = None
    for candidate in range(18620, 18700):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            break
        except OSError:
            continue
    assert httpd is not None, "18620-18699 里找不到可绑定的端口"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


def _push(page, event: dict) -> None:
    page.evaluate(f"window.__gcodeEvent({json.dumps(event, ensure_ascii=False)})")


def _calls(page) -> list:
    return page.evaluate("() => window.__E2E_CALLS__")


def main() -> int:
    from playwright.sync_api import sync_playwright
    from e2e_launch import launch

    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []
    tally = {"n": 0}

    def check(cond, message):
        tally["n"] += 1
        if not cond:
            failures.append(message)
        print(("  \u2713 " if cond else "  \u2717 ") + message)

    try:
        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(STUB_JS)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => document.getElementById('updateOverlay') !== null")

            # ---- 1. 一条事件进来:卡自己弹开 + 徽章显形 ----
            _push(page, {"type": "update_prompt", "version": "0.9.176",
                         "date": "2026-10-16", "notes": "## 本次更新\n- 就地装",
                         "truncated": False, "url": "https://github.com/x",
                         "asset": {"name": "yinlu-xuezhang-0.9.176-setup.exe",
                                   "url": "https://github.com/x/a.exe", "size": 9000}})
            page.wait_for_function(
                "() => document.getElementById('updateOverlay').classList.contains('show')")
            check("0.9.176" in page.locator("#updVer").inner_text(),
                  "卡上写的是新版本号,不是当前版本")
            check(page.locator("#verBadge").is_visible(),
                  "版本号旁那枚「更新」徽章显形(关掉卡之后还得能再唤出来)")
            check(page.locator("#updGo").inner_text().strip() == "下载更新"
                  and page.locator("#updGo").is_enabled(),
                  "有适配本机的发布件 ⇒ 「下载更新」是亮的")

            # ---- 2. 进度跟着 update_state 走 ----
            page.locator("#updGo").click()
            _push(page, {"type": "update_state", "state": "downloading",
                         "version": "0.9.176", "pct": 40, "got": 3600, "total": 9000,
                         "name": "yinlu-xuezhang-0.9.176-setup.exe"})
            page.wait_for_function(
                "() => !document.getElementById('updDl').hidden")
            check("40%" in page.locator("#updLine").inner_text(),
                  "进度条那一行报得出百分比")
            check(page.evaluate("() => document.getElementById('updBar').style.width")
                  == "40%", "条子的宽度与数字是同一个来源")
            check(page.locator("#updGo").inner_text().strip() == "取消下载",
                  "下载中那颗按钮改口成取消,不让人以为要点第二次")

            # ---- 3. 下完且核对通过 → 才给「运行安装程序」 ----
            _push(page, {"type": "update_state", "state": "done", "pct": 100,
                         "version": "0.9.176", "verified": "ok", "size": 9000,
                         "name": "yinlu-xuezhang-0.9.176-setup.exe",
                         "path": "C:/x/yinlu-xuezhang-0.9.176-setup.exe", "why": ""})
            page.wait_for_function(
                "() => document.getElementById('updGo').textContent.indexOf('安装') >= 0")
            check("sha256" in page.locator("#updLine").inner_text(),
                  "写清这份是**核对过**的")

            # ---- 4. 没核对上 → 按钮必须是灰的(这一路的红线) ----
            _push(page, {"type": "update_state", "state": "done", "pct": 100,
                         "version": "0.9.176", "verified": "none", "size": 9000,
                         "name": "yinlu-xuezhang-0.9.176-setup.exe",
                         "path": "C:/x/a.exe", "why": "校验文件 HTTP 404"})
            check(not page.locator("#updGo").is_enabled(),
                  "verified=none ⇒ 按钮灰掉:没核对过的包不给双击")
            _push(page, {"type": "update_state", "state": "error", "pct": 12,
                         "version": "0.9.176", "why": "下载中断(ConnectionReset)"})
            check("ConnectionReset" in page.locator("#updLine").inner_text(),
                  "下载失败把原因摊在卡上,不静默退回 idle")

            # ---- 5. 没有适配本机的发布件 ⇒ 按钮灰 + 说得清为什么 ----
            _push(page, {"type": "update_prompt", "version": "0.9.177", "date": "",
                         "notes": "x", "truncated": False, "url": "https://github.com/y",
                         "asset": None})
            check(not page.locator("#updGo").is_enabled(),
                  "挑不到发布件时按钮是灰的(而不是点了报一个后端错)")

            # ---- 6. 跳过此版本 / 历史更新内容 ----
            page.locator("#updSkipVer").click()
            page.wait_for_function("() => window.__E2E_CALLS__.includes('update_skip')")
            check(not page.evaluate(
                "() => document.getElementById('updateOverlay').classList.contains('show')"),
                  "跳过后卡收起")
            check(not page.locator("#verBadge").is_visible(),
                  "跳过之后连徽章也收掉 —— 只藏卡不藏徽章,用户会觉得'还在提醒我'")
            # 跳过只对那一版有效:后端下一轮推的是**另一版**,卡该重新出现
            _push(page, {"type": "update_prompt", "version": "0.9.178",
                         "date": "2026-10-17", "notes": "再下一版", "truncated": False,
                         "url": "https://github.com/z",
                         "asset": {"name": "yinlu-xuezhang-0.9.178-setup.exe",
                                   "url": "https://github.com/z/a.exe", "size": 9000}})
            page.wait_for_function(
                "() => document.getElementById('updateOverlay').classList.contains('show')")
            check("0.9.178" in page.locator("#updVer").inner_text(),
                  "「跳过此版本」跳的是那一版,不是以后都安静")
            page.locator("#updHist").click()
            page.wait_for_function("() => document.querySelectorAll('.hist-item').length >= 3")
            check(page.locator(".hist-item").count() == 3, "历史一版一行")
            check(page.locator(".hist-cur").count() == 1,
                  "只有正在用的那一版被标出来(拿侧栏那串版本号比,不是猜)")
            page.locator(".hist-item").first.locator(".hist-head").click()
            check(page.evaluate(
                "() => getComputedStyle(document.querySelector('.hist-item .hist-body'))"
                ".display !== 'none'"), "点一行能展开正文")
            page.locator("#histClose").click()
            browser.close()

        print()
        if failures:
            print(f"=== E2E UI 更新卡 FAIL:{len(failures)}/{tally['n']} 项 ===")
            for f in failures:
                print("  - " + f)
            return 1
        print(f"=== E2E UI 更新卡 PASS:{tally['n']} 项断言全过 ===")
        return 0
    finally:
        shutdown()


if __name__ == "__main__":
    sys.exit(main())
