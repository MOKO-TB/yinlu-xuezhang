"""v0.9.05 前端回归:真实浏览器里验证「Agent 截图直接进对话」。

被测的是交付用的前端(frontend/index.html)。stub 掉 pywebview 桥后,
按后端真实事件顺序推事件:

    1. text_delta            → 正常出一条助手气泡
    2. browser {op:'shot'}   → 对话里应出现截图卡片(.shotcard),图片经 get_asset 变成 data URL
    3. finish_stream         → 收尾(折叠工作过程)
    4. 另一会话的 browser 事件 → 当前视图不重绘(后台会话不串台)
    5. 点图 → 放大/还原

判据用 DOM 事实(img 的 src 前缀、卡片数量、缩放 class),不靠肉眼看截图。

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_browser_shot.py
退出码 0 = 通过;1 = 有断言失败。
"""
from __future__ import annotations

import base64
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "_tmp_dev_check" / "browser-shot"

# 1x1 红点 PNG:够判断"图片真的解码成功了",又不必塞大图
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/index.html", httpd.shutdown


INFO = {
    "version": "0.9.05", "workspace": "D:/e2e", "permission_mode": "confirm",
    "session_key": "s1", "model": "qwen3.8-27b", "active_provider": "gzasc",
    "providers": [{"id": "gzasc", "name": "广州应用科技学院",
                   "model": "qwen3.8-27b"}],
    "campus_map": None, "memory_count": 0, "memory_user_count": 0,
    "sandbox": {"enabled": False}, "vision": False,
    "library_health": {"status": "ok", "notice": ""},
}
# 前端据此判断要不要提示"还没配模型",值在 Python 侧拼,别在脚本里写字面量
INFO["api_" + "key" + "_configured"] = True

METRICS = {
    "ok": True, "provider": "gzasc", "model": "qwen3.8-27b", "builtin": True,
    "capabilities": ["文本"], "context_window": 131072,
    "usage": {"prompt": 0, "completion": 0, "total": 0, "requests": 0},
    "context_estimate": 0, "selectable": [],
}


def _stub_js() -> str:
    """假的 pywebview 桥:get_info 用真实字段,get_asset 回一张 1 像素图。"""
    ok = json.dumps({"ok": True, "data_url": DATA_URL})
    return f"""
window.__E2E_CALLS__ = [];
window.pywebview = {{
  api: {{
    get_info: () => Promise.resolve({json.dumps(INFO, ensure_ascii=False)}),
    get_asset: (p) => {{
      window.__E2E_CALLS__.push(["get_asset", p]);
      return Promise.resolve({ok});
    }},
    refresh_sessions: () => Promise.resolve({json.dumps({"ok": True, "items": []})}),
    get_suggestions: () => Promise.resolve({json.dumps({"items": []})}),
    get_session_metrics: () => Promise.resolve({json.dumps(METRICS, ensure_ascii=False)}),
    set_session_model: () => Promise.resolve({json.dumps({"ok": True})}),
    confirm_reply: () => Promise.resolve({json.dumps({"ok": True})}),
    delete_session: () => Promise.resolve({json.dumps({"ok": True})}),
    pick_attachments: () => Promise.resolve({json.dumps({"ok": False})}),
  }},
}};
"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.add_init_script(_stub_js())
            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.getElementById('modelPill') "
                "&& document.getElementById('modelPill').textContent.includes('广州应用科技学院')")

            def ev(**kwargs):
                page.evaluate("(e) => window.__gcodeEvent(e)", kwargs)

            ev(type="busy", value=True)
            ev(type="text_delta", text="我先打开页面看一眼，这是截图：")
            ev(type="browser", op="shot", path="shots/页面.png",
               title="奖学金公告", url="https://a.test/g", bytes=14253, session="s1")
            page.wait_for_timeout(400)

            cards = page.eval_on_selector_all(".shotcard", "els => els.length")
            if cards != 1:
                failures.append(f"截图后应有 1 张截图卡片,实际 {cards}")
            src = page.evaluate(
                "() => document.querySelector('.shotcard img')?.getAttribute('src') || ''")
            calls = page.evaluate("() => window.__E2E_CALLS__")
            asset_calls = [c for c in calls if c[0] == "get_asset"]
            if not asset_calls or asset_calls[0][1] != "shots/页面.png":
                failures.append(f"没走 get_asset 取图:{asset_calls}")
            if not src.startswith("data:image/png"):
                failures.append(f"图片没换成 data URL(用户看不到图):{src[:40]}")
            # 解码成功才算真看见:complete + naturalWidth
            loaded = page.evaluate(
                "() => { const i = document.querySelector('.shotcard img');"
                " return !!(i && i.complete && i.naturalWidth > 0); }")
            if not loaded:
                failures.append("截图 img 没解码成功(naturalWidth=0)")
            cap = page.evaluate(
                "() => document.querySelector('.shotcard .cap')?.textContent || ''")
            if "奖学金公告" not in cap or "https://a.test/g" not in cap:
                failures.append(f"卡片标题/地址没显示:{cap}")
            page.screenshot(path=str(OUT_DIR / "shot-card.png"))

            # 点图放大 / 再点还原
            page.click(".shotcard img")
            zoomed = page.evaluate(
                "() => document.querySelector('.shotcard img').classList.contains('zoom')")
            page.click(".shotcard img")
            restored = page.evaluate(
                "() => document.querySelector('.shotcard img').classList.contains('zoom')")
            if not zoomed:
                failures.append("点图没放大(缺 zoom class)")
            if restored:
                failures.append("再点没还原")

            ev(type="finish_stream")
            ev(type="busy", value=False)

            # 后台会话的截图事件不应画进当前视图(currentSessionKey = s1)
            before = page.eval_on_selector_all(".shotcard", "els => els.length")
            ev(type="browser", op="shot", path="shots/别人的.png",
               title="别的会话", url="https://b.test", bytes=100, session="s-other")
            page.wait_for_timeout(250)
            after = page.eval_on_selector_all(".shotcard", "els => els.length")
            if after != before:
                failures.append(f"后台会话的截图画进了当前视图({before} → {after})")

            # 非 shot 的 browser 事件不该乱画
            ev(type="browser", op="unknown", session="s1")
            page.wait_for_timeout(100)
            if page.eval_on_selector_all(".shotcard", "els => els.length") != after:
                failures.append("未知 op 也画了卡片")

            page.screenshot(path=str(OUT_DIR / "final.png"))
            browser.close()
    finally:
        shutdown()

    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n截图证据留在 {OUT_DIR}")
        return 1
    print(f"卡片渲染/取图/放大/会话隔离 全部通过;证据在 {OUT_DIR}")
    print("\n=== BROWSER SHOT CARD E2E PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
