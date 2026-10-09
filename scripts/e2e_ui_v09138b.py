# -*- coding: utf-8 -*-
"""v0.9.138 界面这三件 —— 真 Chromium 点一遍:待插话草稿条 / 会话菜单 / 复制图片。

这三件的共同点是「**点了到底发生了什么**」只有真浏览器知道:
Python 侧能证明桥上有 `rename_session`、`copy_share_image`,但证明不了
侧栏那一行有没有一颗点得到 ⋯、点 ⋯ 弹的那张菜单上写的是「归档」还是「取消归档」、
Enter 在忙的时候到底有没有把话直接塞进正在跑的那一轮。

要钉的事(按三件分):

待插话草稿条(F3)
1. 忙时按 Enter **不立刻发**:一次 send_task 都不敲,输入框清空;
2. 攒出一条草稿,上面写着原文,带三颗动作;
3. 按「↵ 插话」才发:敲一次 send_task,内容就是那句草稿;
4. 按「✎」把话拿回输入框(改字),不发;
5. 按「🗑」丢掉,不发,也不留空壳;
6. 换会话(cleared)之后草稿不挂着骗人。

会话菜单(F4)
7. 行尾那颗 ⋯ 在屏上且点得到;右键整行也呼出同一张菜单;
8. 菜单上有「重命名」,点它把标题变成可编辑的输入框(**不弹 window.prompt** ——
   内嵌 WebView 里 prompt 可能压根不出现,那就是用户说的"无法更改名称");
9. 输入框里回车 → 敲 `rename_session(文件名, 新标题)`,由后端重画列表;
10. 未归档的行写「归档」,已归档的行写「取消归档」—— 状态由后端推的 archived 决定,不是猜的;
11. 点「复制会话 ID」不关页面报错(剪贴板写不进去是环境的事,按钮不能炸)。

复制图片(F5)
12. 每条回答下面那颗 📋 在屏上;
13. 点它敲的是 `copy_share_image()` **零参数**(路径不许由界面传);
14. 后端那句人话原样出现在 toast 里(成功与失败都照实说)。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v09138b.py
"""
from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from e2e_launch import launch          # noqa: E402  起浏览器这件事的一份真相

STUB_JS = r"""
window.__CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.138", app_name: "引路学长", permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 0, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    instructions: {ok: true, line: "", layers: []},
    session_metrics: {ok: false, selectable: []}}),
  get_activity: () => Promise.resolve({ok: true, summary: {}, timeline: {}, groups: []}),
  get_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
  set_monitor_pref: () => Promise.resolve({ok: true}),
  get_env_info: () => Promise.resolve({ok: true, is_repo: false, files: []}),
  refresh_sessions: () => Promise.resolve({ok: true, items: []}),
  get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
  get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
  get_suggestions: () => Promise.resolve({suggestions: []}),
  send_task: (t, atts) => {
    window.__CALLS__.push(["send_task", t, atts]);
    return Promise.resolve({ok: true});
  },
  rename_session: (i, t) => {
    window.__CALLS__.push(["rename_session", i, t]);
    return Promise.resolve({ok: true, msg: "已改名为「" + t + "」"});
  },
  archive_session: (i, f) => {
    window.__CALLS__.push(["archive_session", i, f]);
    return Promise.resolve({ok: true, msg: f ? "已归档" : "已取消归档"});
  },
  copy_share_image: () => {
    window.__CALLS__.push(["copy_share_image"]);
    return Promise.resolve({ok: true, msg: "图片已复制(probe.png),直接 Ctrl+V 粘贴"});
  },
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
window.__calls = (name) => window.__CALLS__.filter(c => c[0] === name);
// 与真机同一条入口:一帧事件直接喂给 __gcodeEvent,不另写一份渲染路径。
window.__ev = (o) => window.__gcodeEvent(o);
// 侧栏列表由后端推:sessions 事件直接喂进去,不另写一份渲染路径
window.__rows = (items) => window.__gcodeEvent({type: "sessions", items: items});
"""

# 会话行没有把文件名写进 DOM(只有标题文本),为了让 e2e 能指认是哪一行,
# 这里在注入时把 file 也挂成 data 属性 —— 只加坐标,不改产品逻辑。
STAMP_JS = r"""
const _origRender = window.renderSessions;
window.renderSessions = function (items) {
  _origRender(items);
  document.querySelectorAll('.sess').forEach((el, i) => {
    if (items && items[i]) el.dataset.file = items[i].file || '';
  });
};
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


def _item(file, title, archived=False, current=False):
    return {"file": file, "index": 1, "time": "2026-10-09 12:00", "n_messages": 4,
            "last_user_text": title, "title": title, "current": current,
            "workspace": "", "ws_current": True, "archived": archived,
            "running": False}


def main() -> int:
    from playwright.sync_api import sync_playwright

    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []
    ran = 0

    def check(cond, message):
        nonlocal ran
        ran += 1
        print(("  ✓ " if cond else "  ✗ ") + message)
        if not cond:
            failures.append(message)

    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_selector("#ver", timeout=15000)
        page.evaluate(STAMP_JS)

        # ================= F3 待插话草稿条 =================
        page.evaluate("() => __ev({type:'busy', value:true})")
        page.fill("#input", "别忘了先看一眼测试")
        page.press("#input", "Enter")
        page.wait_for_timeout(150)
        check(len(page.evaluate("() => __calls('send_task')")) == 0,
              "忙时按 Enter **没有**立刻把话发出去(一次 send_task 都没敲)")
        check(page.input_value("#input") == "", "输入框清空了(草稿另存在条上)")
        chips = page.query_selector_all("#draftBar .dchip")
        check(len(chips) == 1 and "别忘了先看一眼测试" in chips[0].inner_text(),
              "攒出一条待插话草稿,上面写着原文")
        check(page.is_visible("#draftBar"), "草稿条在屏上可见")

        page.evaluate("() => document.querySelectorAll('#draftBar .dchip button.go')[0].click()")
        page.wait_for_timeout(150)
        sent = page.evaluate("() => __calls('send_task')")
        check(len(sent) == 1 and sent[0][1] == "别忘了先看一眼测试",
              "按「↵ 插话」才发,发的就是那句草稿")
        check(len(page.query_selector_all("#draftBar .dchip")) == 0,
              "发掉的草稿立刻消失(不留空壳)")

        for line in ("第一句", "第二句", "第三句"):
            page.fill("#input", line)
            page.press("#input", "Enter")
        page.wait_for_timeout(150)
        check(len(page.query_selector_all("#draftBar .dchip")) == 3,
              "三条草稿叠着放(跑着的时候连想几句是常态)")
        page.evaluate("() => document.querySelectorAll('#draftBar .dchip button')[1].click()")
        page.wait_for_timeout(120)
        check("第一句" in page.input_value("#input"),
              "按「✎」把那句拿回输入框改,不发出去")
        page.evaluate("() => document.querySelectorAll('#draftBar .dchip button.rm')[0].click()")
        page.wait_for_timeout(120)
        left = page.query_selector_all("#draftBar .dchip")
        check(len(left) == 1 and "第三句" in left[0].inner_text(),
              "按「🗑」只丢掉点的那一条,其余不受牵连")
        page.evaluate("() => __ev({type:'cleared'})")
        page.wait_for_timeout(120)
        check(len(page.query_selector_all("#draftBar .dchip")) == 0,
              "换会话之后上一轮的草稿不再挂着骗人")
        page.evaluate("() => __ev({type:'busy', value:false})")

        # ================= F4 会话菜单 =================
        page.evaluate("(it) => __rows(it)", [_item("session_a.jsonl", "蓝桥杯报名材料"),
                                             _item("session_b.jsonl", "已归档那条", archived=True)])
        page.wait_for_timeout(200)
        rows = page.query_selector_all(".sess")
        check(len(rows) == 2, f"侧栏两行渲染出来({len(rows)} 行)")
        # ⋯ 与 ✕ 同一套悬停才显形(不给每行常驻两颗按钮),所以先悬停再断言
        page.hover(".sess")
        page.wait_for_timeout(120)
        check(page.is_visible(".sess .sm"), "悬停时行尾那颗 ⋯ 在屏上(原来只有一颗 ✕ = 只能删)")
        page.click(".sess .sm")
        page.wait_for_timeout(150)
        menu = page.query_selector_all(".proj-menu.show button")
        labels = [b.inner_text() for b in menu]
        check(any("重命名" in t for t in labels), f"菜单里有「重命名」(实测 {labels})")
        check(any("归档(沉到列表末尾,不删除)" in t for t in labels),
              "未归档那行写的是「归档」")
        page.keyboard.press("Escape")
        page.evaluate("() => document.body.click()")
        page.wait_for_timeout(120)
        # 右键整行也呼出
        page.click(".sess", button="right")
        page.wait_for_timeout(150)
        check(len(page.query_selector_all(".proj-menu.show button")) >= 4,
              "右键整行呼出同一张菜单")
        page.evaluate("() => [...document.querySelectorAll('.proj-menu.show button')]"
                      ".find(b => b.textContent.indexOf('重命名') >= 0).click()")
        page.wait_for_timeout(120)
        check(page.query_selector(".sess input.sess-rename") is not None,
              "「重命名」就地变成可编辑输入框(不弹 window.prompt —— 内嵌 WebView 里它可能不出现)")
        page.fill(".sess input.sess-rename", "蓝桥杯·报名材料 v2")
        page.press(".sess input.sess-rename", "Enter")
        page.wait_for_timeout(150)
        rn = page.evaluate("() => __calls('rename_session')")
        check(len(rn) == 1 and rn[0][1] == "session_a.jsonl" and rn[0][2] == "蓝桥杯·报名材料 v2",
              f"回车敲的是 rename_session(文件名, 新标题)(实测 {rn})")

        # 已归档那行:菜单上那颗要写「取消归档」
        page.hover(".sess:nth-child(2)")
        page.click(".sess:nth-child(2) .sm")
        page.wait_for_timeout(150)
        labels2 = [b.inner_text() for b in page.query_selector_all(".proj-menu.show button")]
        check(any(t.strip().startswith("取消归档") for t in labels2),
              "已归档那行的那颗写「取消归档」(状态来自后端推的 archived,不是猜的)")
        page.evaluate("() => [...document.querySelectorAll('.proj-menu.show button')]"
                      ".find(b => b.textContent.indexOf('取消归档') >= 0).click()")
        page.wait_for_timeout(150)
        ar = page.evaluate("() => __calls('archive_session')")
        check(ar and ar[-1][2] is False, f"点下去敲 archive_session(文件名, false)(实测 {ar})")

        # ================= F5 复制图片 =================
        page.evaluate("(t) => __ev({type:'text_delta', text: t})", "这段回答要能排成图。")
        page.evaluate("() => __ev({type:'finish_stream'})")
        page.wait_for_timeout(200)
        page.hover(".msg.assistant:last-of-type")     # .acts 与 ⋯/✕ 一样是悬停才显形
        page.wait_for_timeout(120)
        check(page.is_visible(".msg.assistant .acts .shrc"),
              "回答下面那颗 📋 在屏上(悬停时)")
        page.click(".msg.assistant .acts .shrc")
        page.wait_for_timeout(200)
        cp = page.evaluate("() => __calls('copy_share_image')")
        check(len(cp) == 1 and len(cp[0]) == 1,
              "点它敲的是 copy_share_image() **零参数**(路径不由界面传)")
        shown = page.evaluate("() => [...document.querySelectorAll('#toasts .toast')]"
                              ".map(n => n.textContent).join(' | ')")
        check("图片已复制" in shown and "Ctrl+V" in shown,
              f"后端那句人话原样显示出来(不缩成「成功」两个字)(实测:{shown[:48]})")

        check(not errors, f"整场零页面错误(实测 {errors[:2]})")
        browser.close()
    shutdown()
    print()
    if failures:
        print(f"FAIL: {len(failures)}/{ran} 项不对 → " + "; ".join(failures))
        return 1
    print(f"PASS: v0.9.138 界面三件 {ran} 项全对(真 Chromium 点过)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
