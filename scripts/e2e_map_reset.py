"""B12 回归验证:真实浏览器里跑一遍「切会话后地图是否清零」。

被测的是交付用的前端(`frontend/index.html` + `frontend/map.js`),
用 Playwright 起真实 Chromium 加载页面,stub 掉 pywebview 桥,
然后按后端真实事件顺序推事件:

    1. map {op:'focus', items:[真楼]}  → 地图应弹开并画出标注框
    2. cleared {session:'s2'}         → 地图应清零并收起(B12 的修复点)

判据用 DOM 事实,不看截图:`.cm-box` 数量、面板 open class、搜索框内容。

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_map_reset.py

退出码 0 = 通过;1 = 有断言失败(会打印明细)。
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
if str(ROOT) not in sys.path:                 # 允许直接 python scripts/xxx.py 运行
    sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "_tmp_map_check"
SHOTS = {
    "before": OUT_DIR / "b12-before.png",
    "after": OUT_DIR / "b12-after.png",
}

DEFAULT_SUB = "手绘地图 · 滚轮缩放 · 拖动平移 · 点框看大图"


def _serve(directory: Path):
    """起一个只服务 frontend/ 的静态服务,返回 (url, shutdown)。"""
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):          # 静音访问日志(不污染 stdout)
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}/index.html", httpd.shutdown


def _stub_bridge_js(info_json: str) -> str:
    """在页面脚本之前注入假的 pywebview 桥(get_info 返回真实 campus_map)。"""
    return f"""
window.__E2E_INFO__ = {info_json};
window.pywebview = {{
  api: new Proxy({{
    get_info: () => Promise.resolve(window.__E2E_INFO__),
    refresh_sessions: () => Promise.resolve({{ ok: true, items: [] }}),
    get_suggestions: () => Promise.resolve({{ items: [] }}),
    get_asset: () => Promise.resolve({{ ok: false }}),
  }}, {{
    get(t, k) {{ return k in t ? t[k] : () => Promise.resolve({{ ok: true }}); }},
  }}),
}};
"""


def main() -> int:
    from minicode import campus

    payload = campus.frontend_payload()
    items = [it for it in payload.get("items", []) if it.get("bbox")]
    if not items:
        print("无法验证:交付索引里没有带坐标的建筑条目。")
        return 1
    # 挑一栋有标注图的楼,和 map_find_place 的 focus 事件结构一致
    target = next((it for it in items if it.get("thumb") or it.get("image")), items[0])

    from playwright.sync_api import sync_playwright

    url, shutdown = _serve(FRONTEND)
    info = {
        "version": "0.9.01", "workspace": str(ROOT), "permission_mode": "confirm",
        "session_key": "s1", "campus_map": payload, "memory_count": 0,
        "memory_user_count": 0, "vision": False, "api_key_configured": True,
        "sandbox": {"enabled": False},
    }
    failures: list[str] = []
    OUT_DIR.mkdir(exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.add_init_script(_stub_bridge_js(json.dumps(info, ensure_ascii=False)))
            page.goto(url, wait_until="load")
            page.wait_for_function("() => window.CampusMap && !!window.__gcodeEvent")

            def boxes() -> int:
                return page.eval_on_selector_all(".cm-box", "els => els.length")

            def panel_open() -> bool:
                return page.evaluate(
                    "() => document.getElementById('mapPanel').classList.contains('open')")

            # --- 1. 先制造"上一会话留下的地图状态":focus 事件会弹面板并画框 ---
            page.evaluate(
                "ev => window.__gcodeEvent(ev)",
                {"type": "map", "op": "focus", "session": "s1",
                 "items": [target], "color": "orange"})
            page.wait_for_timeout(400)          # 等滑入动画与 drawBoxes
            drawn = boxes()
            opened = panel_open()
            page.screenshot(path=str(SHOTS["before"]))
            if not opened:
                failures.append("前置条件失败:focus 事件没把地图面板弹开")
            if drawn < 1:
                failures.append(f"前置条件失败:focus 事件没画出标注框(实际 {drawn} 个)")

            # 手动搞点残留:图钉 + 搜索词 + 筛选,逼近真实使用后的状态
            page.evaluate("""() => {
              window.__gcodeEvent({type:'map', op:'pin', session:'s1',
                                   x:1200, y:600, label:'集合点', color:'red'});
              document.getElementById('mapSearch').value = '图书馆';
            }""")
            page.wait_for_timeout(200)
            pins_before = page.eval_on_selector_all(".cm-pin", "els => els.length")
            if pins_before < 1:
                failures.append("前置条件失败:图钉没画上")

            # --- 2. 后端新建会话 → 推 cleared;地图必须清零 ---
            page.evaluate(
                "ev => window.__gcodeEvent(ev)",
                {"type": "cleared", "session": "s2"})
            page.wait_for_timeout(400)
            after = {
                "boxes": boxes(),
                "pins": page.eval_on_selector_all(".cm-pin", "els => els.length"),
                "panel": panel_open(),
                "search": page.evaluate(
                    "() => document.getElementById('mapSearch').value"),
                "sub": page.evaluate(
                    "() => document.getElementById('mapSub').textContent"),
                "lightbox": page.evaluate(
                    "() => document.getElementById('mapLightbox')"
                    ".classList.contains('show')"),
            }
            page.screenshot(path=str(SHOTS["after"]))

            if after["boxes"]:
                failures.append(f"B12 未修复:切会话后仍留着 {after['boxes']} 个标注框")
            if after["pins"]:
                failures.append(f"B12 未修复:切会话后仍留着 {after['pins']} 个图钉")
            if after["panel"]:
                failures.append("B12 未修复:切会话后地图面板仍处于打开状态")
            if after["search"]:
                failures.append(f"B12 未修复:切会话后搜索框仍残留 {after['search']!r}")
            if after["sub"] != DEFAULT_SUB:
                failures.append(f"B12 未修复:切会话后副标题未复位({after['sub']!r})")
            if after["lightbox"]:
                failures.append("B12 未修复:切会话后大图预览仍开着")

            # --- 3. 回归:清零之后地图还得能正常用 ---
            page.evaluate(
                "ev => window.__gcodeEvent(ev)",
                {"type": "map", "op": "focus", "session": "s2",
                 "items": [target], "color": "teal"})
            page.wait_for_timeout(400)
            if boxes() < 1 or not panel_open():
                failures.append("回归失败:清零后地图无法再正常标注(功能被改坏)")

            print(f"目标建筑:{target.get('name')}({target.get('no')})")
            print(f"[before] 框 {drawn} 个 / 图钉 {pins_before} 个 / 面板开 {opened}")
            print(f"[after ] {after}")
            print(f"截图:{SHOTS['before']} , {SHOTS['after']}")
            browser.close()
    finally:
        shutdown()

    if failures:
        print("\n失败项:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nB12 验证通过:切会话后地图状态已清零,且清零后地图仍可正常使用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
