# -*- coding: utf-8 -*-
"""前端回归:工作区弹窗里那三个"只进不出"的目录 —— 看得见大小、删得掉、两步确认。

后端 `workspace_files_list` / `workspace_files_delete` 有一堆测试盯路径安全(带路径的、
绝对的、目录、软链到别处的一律拒),这份文件盯的是**界面上那几件事**:
数字来自后端而不是前端自己数、三个目录各算各的、没勾选不误删、第一次点只 arm 不删、
删完清单自己刷新、后端拒了要让用户看到有几个没删而不是假装成功。

钉这些:
1. 打开弹窗列出 `uploads/` 的两个附件,标签上带真条数;
2. 大小与日期是后端给的(`total_bytes`/`bytes`/`mtime`),前端只做人话化;
3. 切到「图片」标签看到的是另一个目录(空就是空,不拿附件那两份凑数);
4. 没勾任何一行点删除 → 一次请求都不发;
5. 第一次点只 arm(文案自己变成"再点一次"),第二次才发,且只发一次;
6. 发出去的参数带当前这个目录 —— 在 charts/ 里勾的东西不能删到 uploads/;
7. 后端回 ok:false + refused 列表时,界面不报成功。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_uploads.py
"""
from __future__ import annotations

import json
import re
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LISTINGS = {
    "uploads": {"ok": True, "count": 2, "total_bytes": 3072, "shown": 2, "dir": "uploads",
                "items": [{"name": "课件第3章.pdf", "bytes": 2048, "mtime": "2026-09-24 15:02"},
                          {"name": "错题照片.jpg", "bytes": 1024, "mtime": "2026-09-25 09:11"}]},
    "images": {"ok": True, "count": 0, "total_bytes": 0, "shown": 0, "dir": "images",
               "items": []},
    "charts": {"ok": True, "count": 1, "total_bytes": 512, "shown": 1, "dir": "charts",
               "items": [{"name": "成绩.svg", "bytes": 512, "mtime": "2026-09-26 10:04"}]},
}

STUB_JS = """
window.__DELETE_CALLS__ = [];
window.__DELETE_RESULT__ = {ok:true, deleted:["课件第3章.pdf"], refused:[], freed_bytes:2048,
                            msg:"删掉 1 个,腾出 2048 字节"};
window.__LISTINGS__ = @@LISTINGS@@;
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.41", app_name: "引路学长",
    permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    session_key: "s1", model: "test-model", active_provider: "test",
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    memory_count: 0, memory_user_count: 0, mcp_servers: [],
    sandbox: {}, vision: false, campus_map: {buildings: []}, library_health: {},
    skills: [], kb_count: 0,
    session_metrics: {ok: false, selectable: []}
  }),
  get_workspaces: () => Promise.resolve({current: {name: "campus", path: "D:/demo/campus"},
                                         default: {}, recent: []}),
  workspace_files_list: (folder) => Promise.resolve(
    window.__LISTINGS__[folder] || {ok:false, msg:"只认这三个目录"}),
  workspace_files_delete: (names, folder) => {
    window.__DELETE_CALLS__.push([JSON.parse(names), folder]);
    return Promise.resolve(window.__DELETE_RESULT__);
  },
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k] : ((...a) => Promise.resolve({ok: true}))
})};
"""


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

    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []
    errors: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ok   " if cond else "  FAIL ") + message)

    stub = STUB_JS.replace("@@LISTINGS@@", json.dumps(LISTINGS, ensure_ascii=False))
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.add_init_script(stub)
        page.goto(url)
        page.wait_for_timeout(900)

        page.click("#wsPill")
        page.wait_for_timeout(500)
        check(page.locator("#wsFiles .ws-row").count() == 2,
              f"① 弹窗列出 uploads/ 的两个附件(实得 {page.locator('#wsFiles .ws-row').count()})")
        tabs = page.evaluate("() => [...document.querySelectorAll('.ws-tab')].map(b=>b.textContent)")
        check(tabs == ["附件 2", "图片 0", "图表 1"], f"① 三个标签各带真条数:{tabs}")
        first_title = page.get_attribute(".ws-tab", "title")
        check("3 KB" in first_title and "2 个" in first_title,
              f"② 标签的悬停说明里是后端给的总数:{first_title}")
        row = page.text_content("#wsFiles .ws-row")
        check("2026-09-24" in row and "2 KB" in row, "② 每行有大小与日期,不是一串字节数")
        # 钉住 bidi 那个坑:大小与日期绝不能复用 .p(那是 direction:rtl,给路径截头留尾的),
        # 否则 "2 KB · 2026-09-24 15:02" 会被重排成 "KB · 2026-09-24 15:02 2"。
        size_txt = page.text_content("#wsFiles .ws-row .sz").strip()
        check(re.fullmatch(r"[\d.]+ [KM]?B · \d{4}-\d{2}-\d{2} \d{2}:\d{2}", size_txt),
              f"② 尺寸串的顺序没被双向算法打乱(实得:{size_txt})")

        # ---- 3) 切标签看另一个目录
        page.click(".ws-tab:nth-of-type(2)")
        page.wait_for_timeout(280)
        check("images/ 里现在是空的" in page.text_content("#wsFiles"),
              "③ 切到「图片」看到的是那个目录自己的内容(空就说空)")
        page.click(".ws-tab:nth-of-type(3)")
        page.wait_for_timeout(280)
        check(page.locator("#wsFiles .ws-row").count() == 1 and "成绩.svg" in page.text_content("#wsFiles"),
              "③ 切到「图表」只剩那一张 svg")

        # ---- 4) 没勾选就点删除
        page.evaluate("() => { window.__DELETE_CALLS__ = []; }")
        page.click("#btnWsUplDelete")
        page.wait_for_timeout(220)
        check(page.evaluate("window.__DELETE_CALLS__") == [], "④ 没勾选时一次请求都不发")

        # ---- 5~6) 两步确认,且带当前目录
        page.check("#wsFiles input")
        page.click("#btnWsUplDelete")
        page.wait_for_timeout(220)
        check(page.evaluate("window.__DELETE_CALLS__") == [], "⑤ 第一次点只 arm,不真删")
        check("再点一次" in page.text_content("#btnWsUplDelete"),
              "⑤ 按钮自己说清下一步是什么")
        page.click("#btnWsUplDelete")
        page.wait_for_timeout(300)
        calls = page.evaluate("window.__DELETE_CALLS__")
        check(calls == [[["成绩.svg"], "charts"]],
              f"⑥ 发出去的是当前目录 + 勾中的名字:{calls}")

        # ---- 7) 后端拒了不装成功
        page.evaluate("""() => { window.__DELETE_RESULT__ = {ok:false, deleted:[],
                          refused:['../../etc/passwd'], freed_bytes:0, msg:"没有删掉任何东西"}; }""")
        page.click("#wsFiles .ws-row")            # 回到图表那一行,勾上它
        page.wait_for_timeout(160)
        page.click("#btnWsUplDelete")
        page.wait_for_timeout(160)
        page.click("#btnWsUplDelete")
        page.wait_for_timeout(300)
        body = page.text_content("body")
        check("没有删掉任何东西" in body, "⑦ 后端的原话显示给用户,不报成功")

        page.close()
        browser.close()

    check(not [e for e in errors if "favicon" not in e], f"无 pageerror:{errors[:2]}")
    shutdown()
    print()
    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n{len(failures)} 项不通过")
        return 1
    print("PASS: 三个目录各算各的、两步确认才删、删除带目录参数、被拒不装成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
