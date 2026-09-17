# -*- coding: utf-8 -*-
"""给 v0.9.13 的模型管理弹窗截图(开发自检用,不参与测试)。

复用 e2e_ui_v0913 的假前端桩:同一份假数据,开弹窗、截两张图
(浅色 / 深色各一张),输出到 _tmp_dev_check/ 下,便于人工核对版式。

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\shot_model_dialog.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.e2e_ui_v0913 import FRONTEND, STUB_JS, _serve   # noqa: E402

OUT = ROOT / "_tmp_dev_check"


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(exist_ok=True)
    url, shutdown = _serve(FRONTEND)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(STUB_JS)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => window.__stubReady === true")

            page.click("#modelPill")
            page.wait_for_selector("#provOverlay.show")
            # 选到自定义供应商:右栏是完整的表单 + 模型清单
            page.click("#provList .prov-item:nth-child(2)")
            page.wait_for_selector("#pvName")
            page.wait_for_timeout(250)
            light = OUT / "model_dialog_light.png"
            page.screenshot(path=str(light))
            print("浅色:", light)

            page.evaluate("document.documentElement.classList.add('dark')")
            page.wait_for_timeout(350)
            dark = OUT / "model_dialog_dark.png"
            page.screenshot(path=str(dark))
            print("深色:", dark)

            # 顺手截一张侧栏搜索(有命中片段)的样子
            page.keyboard.press("Escape")
            page.click("#btnProvClose")
            page.fill("#sessSearch", "三体")
            page.press("#sessSearch", "Enter")
            page.wait_for_function(
                "() => document.querySelectorAll('#sessions .sess').length === 1")
            page.wait_for_timeout(250)
            shot = OUT / "session_search_dark.png"
            page.screenshot(path=str(shot))
            print("搜索:", shot)
            browser.close()
    finally:
        shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
