# -*- coding: utf-8 -*-
"""前端回归:工作区弹窗里那段「这里的附件」看得见大小、删得掉,而且两步确认。

`uploads/` 以前只进不出:三个入口往里拷,面板看不见、也没地方删(审计的发现四)。
后端 `uploads_list` / `uploads_delete` 有一堆测试盯着路径安全,这份文件盯的是**界面上
那几件事**:数字是从后端来的不是前端自己数的、没勾选时不误删、第一次点只 arm 不删、
删完清单会自己刷新、后端拒了要让用户看到"有几个没删"而不是假装成功。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_uploads.py
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LISTING = {
    "ok": True, "count": 2, "total_bytes": 3072, "shown": 2, "dir": "uploads",
    "items": [{"name": "课件第3章.pdf", "bytes": 2048, "mtime": "2026-09-24 15:02"},
              {"name": "错题照片.jpg", "bytes": 1024, "mtime": "2026-09-25 09:11"}],
}

STUB_JS = """
window.__DELETE_CALLS__ = [];
window.__DELETE_RESULT__ = {ok:true, deleted:["课件第3章.pdf"], refused:[], freed_bytes:2048,
                            msg:"删掉 1 个,腾出 2048 字节"};
window.__LIST__ = @@LIST@@;
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.40", app_name: "引路学长",
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
  uploads_list: () => Promise.resolve(window.__LIST__),
  uploads_delete: (names) => {
    window.__DELETE_CALLS__.push(JSON.parse(names));
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

    stub = STUB_JS.replace("@@LIST@@", json.dumps(LISTING, ensure_ascii=False))
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.add_init_script(stub)
        page.goto(url)
        page.wait_for_timeout(900)

        page.click("#wsPill")
        page.wait_for_timeout(400)
        check(page.locator("#wsUploads .ws-row").count() == 2,
              "① 弹窗里列出了 uploads/ 的两个附件")
        title = page.text_content("#wsUplTitle")
        check("3 KB" in title and "2 个" in title,
              f"② 总大小与条数是后端给的:{title.strip()[:60]}")
        sizes = page.text_content("#wsUploads")
        check("2 KB" in sizes and "1 KB" in sizes and "2026-09-24" in sizes,
              "② 每行有大小与日期,不是一串字节数")

        # 没勾任何一行就点删除 → 一次都不该发给桥
        page.click("#btnWsUplDelete")
        page.wait_for_timeout(200)
        check(page.evaluate("window.__DELETE_CALLS__") == [],
              "③ 没勾选时不误删,也不把空清单发给后端")

        # 勾一个 → 第一次点只是"再点一次",第二次才真发
        page.check("#wsUploads input")
        page.click("#btnWsUplDelete")
        page.wait_for_timeout(220)
        calls = page.evaluate("window.__DELETE_CALLS__")
        check(calls == [], f"④ 第一次点不该真删(只 arm):{calls}")
        check("再点一次" in page.text_content("#btnWsUplDelete"),
              "④ 按钮自己说清下一步是什么")
        page.click("#btnWsUplDelete")
        page.wait_for_timeout(260)
        calls = page.evaluate("window.__DELETE_CALLS__")
        check(calls == [["课件第3章.pdf"]], f"④ 第二次才把选中的发出去:{calls}")
        check(len(calls) == 1, "④ 一次点击只发一次请求")

        # 后端拒了一部分 → 界面上要看得见有几个没删,不能报成功
        page.evaluate("""() => { window.__DELETE_RESULT__ = {ok:false, deleted:[],
                          refused:['../config.json'], freed_bytes:0,
                          msg:"没有删掉任何东西"}; }""")
        page.check("#wsUploads input")
        page.click("#btnWsUplDelete")
        page.wait_for_timeout(200)
        page.click("#btnWsUplDelete")
        page.wait_for_timeout(300)
        check(len(page.evaluate("window.__DELETE_CALLS__")) == 2,
              "⑤ 被拒的那次也照样发出去(拒的原因是后端判的,前端不替它决定)")

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
    print("PASS: uploads 清单来自后端、两步确认才删、没勾不误删、被拒不装成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
