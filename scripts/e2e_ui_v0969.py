# -*- coding: utf-8 -*-
"""v0.9.69 前端回归:恢复会话的附件缩略图 + 技能卡片上的使用账。

钉三件事:①恢复会话时用户消息带出附件标签 —— 图片有真缩略图(data URL 出自
真 restore_user_atts 对真文件算的预览)、文件是 📄 占位、被删了的标"已不在
工作区"且没有任何缩略图;②视觉消息留档(content parts)复原的附件一样上标签;
③技能卡片只给真用过的摆「用过 N 次 · 最近 …」,没用过的保持安静。

payload 全部出自真后端:真 save_attachment 落盘、真 build_content 生成留档
内容、真 restore_user_atts/display_text 生成 resumed 消息、真 load_skills +
merge_usage 生成技能清单(使用账指向临时账本文件)。

跑法(项目根目录,GBK 控制台必须带 PYTHONIOENCODING=utf-8):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0969.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from minicode.attachments import (  # noqa: E402
    build_content, display_text, save_attachment,
)
from minicode.gui_bridge import restore_user_atts  # noqa: E402
from minicode import skills as sk_mod  # noqa: E402
from minicode.skills import load_skills, merge_usage, skill_dirs  # noqa: E402


def _build_real_payload() -> tuple[dict, list[dict]]:
    """真后端载荷:resumed 事件与技能清单,形状与桥推送的逐字一致。"""
    tmp = Path(tempfile.mkdtemp())
    ws = tmp / "ws"
    (ws / "uploads").mkdir(parents=True)

    def src_png(name, size, color):
        p = tmp / name
        Image.new("RGB", size, color).save(p)
        return p

    a_img = save_attachment(ws, src_png("s1.png", (640, 400), (86, 140, 120)))
    pdf = tmp / "notes.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fake-for-e2e\n")
    a_pdf = save_attachment(ws, pdf)
    a_gone = save_attachment(ws, src_png("s2.png", (300, 200), (200, 120, 60)))
    (ws / a_gone.path).unlink()          # 发送之后文件从工作区消失(真删)

    m1 = build_content("帮我看这三张图,重点是第二张", [a_img, a_pdf, a_gone],
                       vision=False)
    m3_parts = build_content("这张课表截图帮我排一下下周", [a_img], vision=True)

    def user_msg(content):
        atts = restore_user_atts(ws, content)
        msg = {"role": "user", "text": display_text(content)}
        if atts:
            msg["atts"] = atts
        return msg

    resumed = {"type": "resumed", "session": "s-e2e", "messages": [
        user_msg(m1),
        {"role": "assistant", "text": "好的,我逐张说明:第一张是…"},
        user_msg(m3_parts),
    ]}

    usage = tmp / "skill-usage.jsonl"
    usage.write_text(
        '{"ts": "2026-09-28 10:00:00", "skill": "git-commit"}\n'
        '{"ts": "2026-09-29 08:30:00", "skill": "git-commit"}\n', encoding="utf-8")
    sk_mod.usage_path = lambda: usage    # 脚本级替换:账本指到临时文件
    skills = [{"name": s.name, "description": s.description, "channel": "",
               "path": "", "is_builtin": False}
              for s in load_skills(skill_dirs(None))]
    return resumed, merge_usage(skills)


_RESUMED, _SKILLS = _build_real_payload()

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__V969_RESUMED__ = @@RESUMED@@;
window.__V969_SKILLS__ = @@SKILLS@@;
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.69", app_name: "引路学长",
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
    get_skills: () => Promise.resolve({ok: true, skills: window.__V969_SKILLS__}),
    send_task: (text) => {
      window.__E2E_CALLS__.push(["send_task", text]);
      return Promise.resolve({ok: true, session: "s1"});
    },
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
""".replace("@@SKILLS@@", json.dumps(_SKILLS, ensure_ascii=False)).replace(
    "@@RESUMED@@", json.dumps(_RESUMED, ensure_ascii=False))

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
            page.wait_for_timeout(250)

            # ---- 1. 推送真 resumed 事件:附件标签按留档复原 ----
            page.evaluate("e => window.__gcodeEvent(e)", _RESUMED)
            page.wait_for_selector(".msg.user .att")
            user_msgs = page.locator(".msg.user")
            check(user_msgs.count() == 2, f"恢复出 2 条用户消息(实测 {user_msgs.count()})")
            first = user_msgs.nth(0)
            check(first.locator(".bubble").inner_text() == "帮我看这三张图,重点是第二张",
                  "气泡正文是用户原话([附件] 清单不出现)")

            atts = first.locator(".att")
            check(atts.count() == 3, f"第一条带 3 枚附件标签(实测 {atts.count()})")
            check(atts.nth(0).locator("img").count() == 1
                  and atts.nth(0).locator("img").get_attribute("src").startswith("data:image/"),
                  "图片附件有真缩略图(data URL 出自真后端)")
            check(atts.nth(1).locator(".ph").count() == 1
                  and " · 图片" not in atts.nth(1).locator(".sz").inner_text(),
                  "非图片附件是 📄 占位、不标「图片」")
            sz = atts.nth(1).locator(".sz").inner_text()
            check(sz.endswith("B") or "KB" in sz or "MB" in sz,
                  f"在盘文件显示真实大小(实测 {sz!r})")
            gone = atts.nth(2)
            check("missing" in (gone.get_attribute("class") or ""),
                  "被删文件标签带 missing 样式")
            check(gone.locator("img").count() == 0,
                  "被删文件绝不补一张编出来的缩略图")
            check("已不在工作区" in gone.get_attribute("title")
                  and "已不在工作区" in gone.locator(".sz").inner_text(),
                  "被删文件两处都说「已不在工作区」")

            second = user_msgs.nth(1)
            check(second.locator(".att").count() == 1
                  and second.locator(".att img").count() == 1,
                  "视觉消息留档(content parts)复原的图片一样上标签")

            # ---- 2. 技能面板:使用账只给真用过的摆一笔 ----
            page.click("#btnSkills")
            page.wait_for_selector("#skillsOverlay.show")
            page.wait_for_selector(".skillcard")
            gc = page.locator(".skillcard", has_text="git-commit").first
            check(gc.locator(".suse").inner_text() == "用过 2 次 · 最近 2026-09-29",
                  f"git-commit 卡片写「用过 2 次 · 最近 2026-09-29」"
                  f"(实测 {gc.locator('.suse').inner_text()!r})")
            pt = page.locator(".skillcard", has_text="python-testing").first
            check(pt.locator(".suse").count() == 0,
                  "没用过的技能卡片保持安静")
            check(page.locator(".suse").count() == 1,
                  f"整面只有 1 条使用账(实测 {page.locator('.suse').count()})")

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
    print("全绿 13 项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
