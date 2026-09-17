# -*- coding: utf-8 -*-
"""v0.9.12 前端回归:真实浏览器里验证本轮三项需求。

    p1 用量弹窗:近 30 天数据很长时弹窗限高、内部滚动(不把整个弹窗拉出屏幕)
    p2 侧栏:独立「＋ 新建任务」按钮回归,点击调用 new_task
    p3 技能与渠道:顶栏「⭐ 技能」开弹窗;技能 tab 渲染卡片/详情;渠道 tab 渲染
       三个目录;「＋ 添加技能」表单展开并调用 add_skill

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_ui_v0912.py
退出码 0 = 通过;1 = 有断言失败。
"""
from __future__ import annotations

import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROJ = {
    "current": {"path": "D:/demo/yhbz", "name": "yhbz", "is_default": False},
    "default": {"path": "D:/app", "name": "默认工作区(程序目录)", "is_default": True},
    "recent": [
        {"path": "D:/demo/yhbz", "name": "yhbz", "is_default": False},
    ],
}

SKILLS = [
    {"name": "git-commit", "description": "提交规范:怎么写 commit 信息",
     "channel": "内置技能", "path": "skills/git-commit/SKILL.md", "is_builtin": True},
    {"name": "library-catalog", "description": "馆藏书目查询:书名/作者/索书号",
     "channel": "应用目录", "path": "skills/library-catalog/SKILL.md", "is_builtin": False},
]

CHANNELS = [
    {"key": "builtin", "label": "内置技能", "note": "随应用分发的只读技能",
     "path": "…/skills", "exists": True, "count": 2},
    {"key": "app", "label": "应用目录", "note": "所有工作区共用",
     "path": "…/app/skills", "exists": True, "count": 3},
    {"key": "workspace", "label": "当前工作区", "note": "只在当前项目生效",
     "path": "D:/demo/yhbz/skills", "exists": False, "count": 0},
]

# 30 天用量数据:每天都给点量,逼出"内容比弹窗高"的滚动场景
DAYS = [{"date": f"2026-08-{d:02d}", "total": 1500 + d * 37, "requests": d % 5,
         "models": [{"key": "m", "model": "m", "prompt": 900, "completion": 600}]}
        for d in range(19, 32)] + \
       [{"date": f"2026-09-{d:02d}", "total": 900 + d * 53, "requests": d % 7,
         "models": [{"key": "m", "model": "m", "prompt": 700, "completion": 400}]}
        for d in range(1, 18)]

STUB_JS = (
    """
window.__E2E_CALLS__ = [];
window.__stubReady = true;
const CALLS = window.__E2E_CALLS__;
function log(){ CALLS.push([...arguments]); return Promise.resolve({ok:true}); }
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.12", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"},
                         {value: "accept_all", label: "自动放行"}],
      session_key: "s1", model: "deepseek-v4-flash", active_provider: "gzasc",
      providers: [{id: "gzasc", name: "广州应用科技学院", model: "deepseek-v4-flash"}],
      api_key_configured: true, workspace: "D:/demo/yhbz",
      workspace_name: "yhbz", workspace_is_default: false,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: null, library_health: {},
      session_metrics: {provider_id: "gzasc", model: "deepseek-v4-flash",
                        consumed: 0, context_tokens: 0, estimated: false,
                        providers: []}
    }),
    get_recent_projects: () => Promise.resolve(__INJ_PROJ),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    refresh_sessions: () => Promise.resolve({ok: true, items: []}),
    new_task: () => log("new_task"),
    get_token_usage: (days) => {
      CALLS.push(["get_token_usage", days]);
      return Promise.resolve({ok: true, days: __INJ_DAYS, models: [], summary:
        {total: 123456, prompt: 100000, completion: 23456, requests: 42}});
    },
    get_skills: () => { CALLS.push(["get_skills"]);
      return Promise.resolve({ok: true, skills: __INJ_SKILLS}); },
    get_skill_body: (name) => { CALLS.push(["get_skill_body", name]);
      return Promise.resolve({ok: true, name, body: "---\\nname: " + name +
        "\\n---\\n技能全文演示"}); },
    get_skill_channels: () => { CALLS.push(["get_skill_channels"]);
      return Promise.resolve({ok: true, channels: __INJ_CHANNELS}); },
    add_skill: (n, d, b, c) => { CALLS.push(["add_skill", n, d, b, c]);
      return Promise.resolve({ok: true, name: n, msg: "已添加"}); },
  }
};
"""
    .replace("__INJ_PROJ", json.dumps(PROJ, ensure_ascii=False))
    .replace("__INJ_SKILLS", json.dumps(SKILLS, ensure_ascii=False))
    .replace("__INJ_CHANNELS", json.dumps(CHANNELS, ensure_ascii=False))
    .replace("__INJ_DAYS", json.dumps(DAYS, ensure_ascii=False))
)


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    import socket
    port = None
    for _ in range(200):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        got = probe.getsockname()[1]
        probe.close()
        if 6000 <= got <= 7999 or got in (25, 501, 6665, 6666, 6667, 6668, 6669):
            continue
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", got), handler)
            port = got
            break
        except OSError:
            continue
    assert port is not None, "拿不到 Chromium 允许的端口"
    import threading
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/index.html", httpd.shutdown


def main() -> int:
    from playwright.sync_api import sync_playwright

    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ✓ " if cond else "  ✗ ") + message)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(STUB_JS)
            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.querySelectorAll('#projects .proj').length >= 1")

            # ---- p2:侧栏独立「＋ 新建任务」回归 ----
            btn = page.locator("#btnNewTask")
            check(btn.count() == 1 and btn.is_visible(), "侧栏有独立的「＋ 新建任务」按钮")
            btn.click()
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'new_task')")
            check(True, "点侧栏「＋ 新建任务」→ new_task()")
            check(page.locator(".proj.current .newhere").count() == 1,
                  "项目卡片内的新建任务按钮保留(两处并存)")

            # ---- p1:用量弹窗限高 + 内部滚动(近 30 天) ----
            page.click("#btnUsage")
            page.wait_for_selector("#usageOverlay.show")
            page.wait_for_function(
                "() => document.querySelectorAll('#usageBody .usage-day').length >= 30")
            metrics = page.evaluate("""() => {
              const dlg = document.querySelector('#usageOverlay .dialog');
              const body = document.getElementById('usageBody');
              const r = dlg.getBoundingClientRect();
              return {dlgH: r.height, vh: window.innerHeight,
                      scrollable: body.scrollHeight > body.clientHeight + 2,
                      bodyTop: body.getBoundingClientRect().top >=
                               document.querySelector('#usageOverlay .usage-head')
                                       .getBoundingClientRect().bottom - 2};
            }""")
            check(metrics["dlgH"] <= metrics["vh"] * 0.88 + 2,
                  f"弹窗高度不超过视口({metrics['dlgH']:.0f} / {metrics['vh']})")
            check(metrics["scrollable"], "30 天圆环列表在弹窗内部滚动(scrollHeight > clientHeight)")
            check(metrics["bodyTop"], "头部(标题+范围切换)不被滚动内容顶出弹窗")
            page.keyboard.press("Escape")   # 技能弹窗有自己的 Esc 分支,用量弹窗走通用分支
            page.click("#btnUsageClose")

            # ---- p3:技能与渠道弹窗 ----
            page.click("#btnSkills")
            page.wait_for_selector("#skillsOverlay.show")
            page.wait_for_function(
                "() => document.querySelectorAll('#skillsBody .skillcard').length === 2")
            check(True, "技能 tab:两张技能卡片(名字+简介+渠道徽标)")
            names = page.eval_on_selector_all(
                "#skillsBody .skillcard .sname", "els => els.map(e => e.textContent.trim())")
            check(any("git-commit" in n for n in names), f"卡片显示技能名({names})")

            # 点卡片看详情(全文),Esc 退回列表
            page.click("#skillsBody .skillcard")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'get_skill_body')")
            check(page.locator("#skBodyPre").count() == 1, "点卡片进入技能详情(全文)")
            page.keyboard.press("Escape")
            page.wait_for_function(
                "() => document.querySelectorAll('#skillsBody .skillcard').length === 2")
            check(True, "Esc 从详情退回技能列表")

            # 渠道 tab:三个技能目录
            page.click("#skillTabs button[data-tab='channels']")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'get_skill_channels')")
            page.wait_for_function(
                "() => document.querySelectorAll('#skillsBody .skch').length === 3")
            check(True, "渠道 tab:三个技能目录(内置/应用/工作区)")
            ro = page.locator("#skillsBody .skch .ro").count()
            check(ro == 1, "内置渠道带「只读」标记")

            # 添加技能:表单展开 → 填写 → 保存 → add_skill(四参)
            page.click("#skillTabs button[data-tab='skills']")
            page.wait_for_function(
                "() => document.querySelectorAll('#skillsBody .skillcard').length === 2")
            page.click("#btnSkillAdd")
            check(page.locator("#skillForm").is_visible(), "「＋ 添加技能」展开表单")
            page.fill("#skName", "paper-polish")
            page.fill("#skDesc", "论文润色规范")
            page.fill("#skBody", "先改结构再改句子")
            page.click("#btnSkillSave")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'add_skill')")
            call = page.evaluate(
                "() => window.__E2E_CALLS__.filter(c => c[0] === 'add_skill').pop()")
            check(call[1] == "paper-polish" and call[2] == "论文润色规范"
                  and "结构" in call[3] and call[4] == "app",
                  f"add_skill 收到 名称/简介/正文/渠道({call[1:]})")
            page.wait_for_function(
                "() => document.querySelectorAll('#skillsBody .skillcard').length === 2")
            check(page.locator("#skillForm").is_hidden(), "保存成功后表单收起并刷新列表")

            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"✗ {len(failures)} 项失败:")
        for f in failures:
            print("  -", f)
        return 1
    print("✓ v0.9.12 前端回归全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
