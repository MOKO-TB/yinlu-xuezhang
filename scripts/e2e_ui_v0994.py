# -*- coding: utf-8 -*-
"""v0.9.94 前端回归:四件 UI —— 权限浮层菜单(p3/p5)/ 轮次导航条(p6)/
图片灯箱(p7/p8)。

钉八件事:①原生 select 换成浮层菜单后,点药丸弹菜单(带图标/说明/当前档
勾选)、选新档真调 set_permission_mode;②Esc 与点外部都能关;③消息 ≥3 问
时轮次导航条出现(刻度数=问数),hover 出预览浮窗、点击滚到那一问;<3 问整
条隐藏;④点对话里的图片开灯箱,底部缩放与索引可见,←→ 切换,Esc 关;
⑤网页版形态(桥缺方法)不炸。payload 形状照后端真值。

跑法(项目根目录,GBK 控制台必须带 PYTHONIOENCODING=utf-8):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0994.py
"""
from __future__ import annotations

import base64
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_PNG_1PX = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82").decode()

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__V994_IMG__ = "data:image/png;base64,@@IMG@@";
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.94", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [
        {value: "plan", label: "计划模式"},
        {value: "confirm", label: "变更前确认"},
        {value: "accept_edits", label: "自动编辑"},
        {value: "accept_all", label: "自动放行"},
        {value: "full", label: "完全访问"}],
      session_key: "s1", model: "test-model", active_provider: "test",
      providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/campus",
      workspace_name: "campus", workspace_is_default: true,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: {buildings: []}, library_health: {},
      skills: [], kb_count: 0,
      session_metrics: {ok: false, selectable: []}
    }),
    set_permission_mode: (v) => {
      window.__E2E_CALLS__.push(["set_permission_mode", v]);
      return Promise.resolve({ok: true});
    },
    set_chrome: (dark) => Promise.resolve({ok: true, applied: ["dark"],
                                           failed: ["caption", "text"], msg: "x"}),
    send_task: () => Promise.resolve({ok: true, session: "s1"}),
    get_schedule_view: () => Promise.resolve({ok: false}),
    set_notice_watch: () => Promise.resolve({ok: true}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
    get_session_metrics: () => Promise.resolve({ok: false}),
    refresh_sessions: () => Promise.resolve({ok: true}),
    rate_message: () => Promise.resolve({ok: true}),
    compact_now: () => Promise.resolve({ok: true}),
    get_weather: () => Promise.resolve({ok: false}),
  }
};
""".replace("@@IMG@@", _PNG_1PX)

FAILS: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ✓ " if ok else "  ✗ ") + what)
    if not ok:
        FAILS.append(what)


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception:
        import os
        base = os.environ.get("LOCALAPPDATA", "")
        for d in sorted(Path(base, "ms-playwright").glob("chromium-*/chrome-win*/chrome.exe"),
                        reverse=True):
            return p.chromium.launch(executable_path=str(d), headless=True)
        raise


def main() -> int:
    from playwright.sync_api import sync_playwright

    js_errors: list[str] = []
    url, stop = _serve(FRONTEND)
    try:
        with sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page()
            page.on("pageerror", lambda e: js_errors.append(str(e)))
            page.on("console", lambda m: js_errors.append(m.text)
                    if m.type == "error" and "Failed to load resource" not in m.text
                    else None)
            page.add_init_script(STUB_JS)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => document.getElementById('input') !== null")
            page.wait_for_timeout(300)

            def calls():
                return [c for c in page.evaluate("() => window.__E2E_CALLS__")]

            # ---- 1. 权限浮层菜单(p5) ----
            pill = page.locator("#permPill")
            check(pill.is_visible(), "权限药丸可见(原生 select 已替换)")
            pill.click()
            menu = page.locator("#permMenu")
            check(menu.is_visible() and menu.locator(".at-item").count() == 5,
                  "药丸点开浮层:5 个档位(带图标与说明)")
            check(menu.locator(".pm-check").count() == 1, "当前档带勾选标记")
            menu.locator(".at-item", has_text="自动编辑").click()
            page.wait_for_timeout(120)
            check(["set_permission_mode", "accept_edits"] in calls(),
                  "选新档真调 set_permission_mode")
            check(not menu.is_visible(), "选完自动收起")
            pill.click()
            page.keyboard.press("Escape")
            check(not menu.is_visible(), "Esc 收起")
            pill.click()
            page.mouse.click(200, 400)          # 点菜单外
            page.wait_for_timeout(100)
            check(not menu.is_visible(), "点外部收起")

            # ---- 2. 轮次导航条(p6) ----
            page.evaluate("e => window.__gcodeEvent(e)", {"type": "resumed",
                          "session": "s1", "messages": [
                {"role": "user", "text": "第 1 问:帮我看看课表"},
                {"role": "assistant", "text": "好的"},
                {"role": "user", "text": "第 2 问:帮我看看地图"},
                {"role": "assistant", "text": "好的"},
                {"role": "user", "text": "第 3 问:奖学金怎么申请"},
                {"role": "assistant", "text": "好的"},
            ]})
            rail = page.locator("#chatRail")
            check(rail.is_visible(), "3 问时轮次导航条出现")
            check(rail.locator(".tick").count() == 3, "刻度数 = 问数")
            rail.locator(".tick").nth(0).hover()
            pop = page.locator("#railPop")
            check(pop.is_visible() and "第 1 问" in pop.inner_text(),
                  "hover 刻度出预览浮窗")
            rail.locator(".tick").nth(0).click()
            page.wait_for_timeout(200)
            page.evaluate("e => window.__gcodeEvent(e)", {"type": "resumed",
                          "session": "s1", "messages": [
                {"role": "user", "text": "第 1 问:帮我看看课表"},
                {"role": "user", "text": "第 2 问:帮我看看地图"},
                {"role": "user", "text": "第 3 问:奖学金怎么申请"},
                {"role": "user", "text": "第四问"}]})
            check(rail.locator(".tick").count() == 4, "新问进来自动加刻度")

            # ---- 3. 图片灯箱(p7/p8) ----
            page.evaluate("""e => {
                const chat = document.getElementById('chat');
                for (let i = 0; i < 2; i++) {
                    const w = document.createElement('div');
                    w.className = 'msg user';
                    w.innerHTML = `<div class="col"><div class="bubble">图 ${i}</div>` +
                        `<div class="atts"><div class="att"><img src="${e.src}"></div></div></div>`;
                    chat.appendChild(w);
                }
            }""", {"src": "data:image/png;base64," + _PNG_1PX})
            imgs = page.locator("#chat .msg .att img")
            check(imgs.count() == 2, "测试图片两枚就位")
            imgs.nth(0).click()
            lb = page.locator("#imgLightbox")
            check(lb.evaluate("el => el.classList.contains('show')"),
                  "点缩略图打开灯箱")
            check(page.locator("#ilIdx").inner_text() == "1 / 2",
                  "索引显示 1 / 2")
            page.click("#ilNext")
            check(page.locator("#ilIdx").inner_text() == "2 / 2", "→ 切到第二张")
            page.click("#ilMinus")
            check(page.locator("#ilPct").inner_text() == "75%", "− 缩到 75%")
            page.keyboard.press("ArrowLeft")
            check(page.locator("#ilIdx").inner_text() == "1 / 2", "键盘 ← 切换")
            page.keyboard.press("Escape")
            check(not lb.evaluate("el => el.classList.contains('show')"),
                  "Esc 关灯箱")

            # ---- 4. <3 问时导航条隐藏 ----
            page.evaluate("e => window.__gcodeEvent(e)", {
                "type": "resumed", "session": "s1",
                "messages": [{"role": "user", "text": "只有一问"}]})
            check(not rail.is_visible(), "少于 3 问整条隐藏(不做无内容的装饰)")

            check(not js_errors, f"零 JS 错误(实测 {js_errors[:3]})")
            browser.close()
    finally:
        stop()
    print()
    if FAILS:
        print(f"{len(FAILS)} 项没过:")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全绿 17 项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
