# -*- coding: utf-8 -*-
"""v0.9.13 前端回归:真实浏览器里验证本轮的模型管理与新增功能。

    p1 模型管理改版:左栏供应商、右栏该供应商的模型清单(可加模型、单模型启停、
       测试连通、从服务端拉取模型);选中供应商与保存提交都要带上模型清单
    新 会话搜索:侧栏按关键词搜历史会话,命中片段可点进对应会话
    新 会话导出:顶栏「⬇ 导出」调用 export_session
    新 快捷键:Ctrl+K 打开模型管理
    新 任务面板:可切列表按「供应商 × 模型」展开(切换要带上具体模型名)

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_ui_v0913.py
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

PAGE_URL = "https://api.deepseek.com/v1"

PROVIDERS = [
    {"id": "gzasc", "name": "广州应用科技学院", "base_url": "", "api_key": "",
     "model": "deepseek-v4-flash", "enabled": True, "builtin": True, "has_key": True,
     "models": [{"name": "deepseek-v4-flash", "enabled": True, "known": True,
                 "context_window": 327680, "supports_vision": False}]},
    {"id": "deepseek", "name": "DeepSeek", "base_url": PAGE_URL, "api_key": "sk-1",
     "model": "deepseek-chat", "enabled": True, "builtin": False, "has_key": True,
     "models": [
         {"name": "deepseek-chat", "enabled": True, "known": True,
          "context_window": 131072, "supports_vision": False},
         {"name": "deepseek-reasoner", "enabled": False, "known": False,
          "context_window": None, "supports_vision": False},
     ]},
]

PRESETS = [{"label": "DeepSeek", "name": "DeepSeek", "base_url": PAGE_URL,
            "models": ["deepseek-chat", "deepseek-reasoner"]},
           {"label": "智谱 GLM", "name": "智谱 GLM",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "models": ["glm-4.6"]}]

SELECTABLE = [
    {"key": "gzasc::deepseek-v4-flash", "id": "gzasc", "name": "广州应用科技学院",
     "model": "deepseek-v4-flash", "has_key": True, "builtin": True,
     "supports_vision": False, "context_window": 327680},
    {"key": "deepseek::deepseek-chat", "id": "deepseek", "name": "DeepSeek",
     "model": "deepseek-chat", "has_key": True, "builtin": False,
     "supports_vision": False, "context_window": 131072},
]

STUB_JS = ("""
window.__E2E_CALLS__ = [];
window.__stubReady = true;
const CALLS = window.__E2E_CALLS__;
function log(){ CALLS.push([...arguments]); return Promise.resolve({ok:true}); }
const METRICS = {provider_id: "gzasc", selected_key: "gzasc::deepseek-v4-flash",
                 model: "deepseek-v4-flash", capabilities: ["文本", "工具调用"],
                 supports_vision: false, known: true, context_window: 327680,
                 context_tokens: 1000, consumed: 0, compact_ratio: 0.7,
                 selectable: __INJ_SELECTABLE};
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.13", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "deepseek-v4-flash", active_provider: "gzasc",
      providers: [{id: "gzasc", name: "广州应用科技学院",
                   model: "deepseek-v4-flash", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/yhbz",
      workspace_name: "yhbz", workspace_is_default: false,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: null, library_health: {},
      skills: [], kb_count: 0,
      session_metrics: METRICS
    }),
    get_recent_projects: () => Promise.resolve({current: null, default: null, recent: []}),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    refresh_sessions: () => {
      CALLS.push(["refresh_sessions"]);
      return Promise.resolve({ok: true, items: []});
    },
    new_task: () => log("new_task"),
    get_providers: () => { CALLS.push(["get_providers"]);
      return Promise.resolve({providers: __INJ_PROVIDERS, active_provider: "gzasc",
                              active_model: "deepseek-v4-flash",
                              presets: __INJ_PRESETS}); },
    save_providers: (js, active) => { CALLS.push(["save_providers", js, active]);
      return Promise.resolve({ok: true, auto_disabled: [], fell_back: ""}); },
    test_model: (pid, model, base, key) => {
      CALLS.push(["test_model", pid, model, base, key]);
      return Promise.resolve({ok: true, model, status: 200,
                              context_window: 262144, supports_vision: false,
                              msg: "已连通 · 窗口 262,144 tokens · 纯文本"}); },
    fetch_models: (pid, base, key) => {
      CALLS.push(["fetch_models", pid, base, key]);
      return Promise.resolve({ok: true,
        models: ["deepseek-chat", "deepseek-reasoner", "deepseek-coder"]}); },
    get_provider_presets: () => { CALLS.push(["get_provider_presets"]);
      return Promise.resolve({ok: true, presets: __INJ_PRESETS}); },
    set_session_model: (pid, model) => { CALLS.push(["set_session_model", pid, model]);
      return Promise.resolve({ok: true}); },
    get_session_metrics: () => Promise.resolve(Object.assign({ok: true}, METRICS)),
    get_token_usage: () => Promise.resolve({ok: true, days: [], models: [],
      summary: {total: 0, prompt: 0, completion: 0, requests: 0}}),
    get_skills: () => Promise.resolve({ok: true, skills: []}),
    get_skill_channels: () => Promise.resolve({ok: true, channels: []}),
    get_workspaces: () => Promise.resolve({ok: true, current: null, default: null, recent: []}),
    export_session: () => { CALLS.push(["export_session"]);
      return Promise.resolve({ok: true, name: "引路学长会话-20260917-2130.md",
                              path: "D:/demo/yhbz/exports/引路学长会话-20260917-2130.md"}); },
    search_sessions: (q) => { CALLS.push(["search_sessions", q]);
      return Promise.resolve({ok: true, query: q, total: 1, results: [
        {index: 1, hits: 2, snippet: "三体这本书在哪个楼层",
         file: "session_20260917_2130.jsonl"}]}); },
    resume_session: (i) => { CALLS.push(["resume_session", i]);
      return Promise.resolve({ok: true, session: "s9"}); },
    set_reasoning_choice: () => log("set_reasoning_choice"),
    get_reasoning: () => Promise.resolve({ok: true, kind: "hidden", choices: [],
                                          choice: "default"}),
  }
};
"""
    .replace("__INJ_PROVIDERS", json.dumps(PROVIDERS, ensure_ascii=False))
    .replace("__INJ_PRESETS", json.dumps(PRESETS, ensure_ascii=False))
    .replace("__INJ_SELECTABLE", json.dumps(SELECTABLE, ensure_ascii=False))
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

    def calls(page, name):
        return page.evaluate(
            "n => window.__E2E_CALLS__.filter(c => c[0] === n)", name)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(STUB_JS)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => window.__stubReady === true")

            # ---- p1:模型管理改成"供应商 + 模型清单" ----
            page.click("#modelPill")
            page.wait_for_selector("#provOverlay.show")
            page.wait_for_function(
                "() => document.querySelectorAll('#provList .prov-item').length === 2")
            check(True, "左栏列出两个供应商")
            check(page.locator("#provList .prov-item .pi-n").first.inner_text() == "内置",
                  "内置供应商标注为「内置」")
            check("2/1" in page.locator("#provList .prov-item .pi-n").nth(1).inner_text()
                  or "1/2" in page.locator("#provList .prov-item .pi-n").nth(1).inner_text(),
                  "自定义供应商显示「启用/总数 模型」")

            # 内置供应商:只读
            check(page.locator("#provMain .prov-ro").count() == 1,
                  "默认选中的内置供应商是只读说明")
            check(page.locator("#pvName").count() == 0, "内置供应商不给编辑表单")

            # 选中自定义供应商 → 右栏成为可编辑表单
            page.click("#provList .prov-item:nth-child(2)")
            page.wait_for_selector("#pvName")
            check(page.input_value("#pvName") == "DeepSeek", "右栏回填供应商名称")
            check(page.input_value("#pvBase") == PAGE_URL, "右栏回填接口地址")
            check(page.input_value("#pvKey") == "sk-1", "右栏回填 API Key")
            check(page.locator("#pvKey").get_attribute("type") == "password",
                  "API Key 默认打码")
            page.click("#btnKeyEye")
            check(page.locator("#pvKey").get_attribute("type") == "text",
                  "点眼睛后明文显示")
            page.click("#btnKeyEye")

            # 供应商里的模型清单
            rows = page.locator("#mmList .mm-row")
            check(rows.count() == 2, "模型清单列出该供应商的 2 个模型")
            check(rows.nth(1).get_attribute("class").find("off") >= 0,
                  "停用的模型行有 off 标记(仍看得见,但不参与切换)")
            check(page.locator("#mmList .mm-row .mmon").nth(1).is_checked() is False,
                  "停用模型的行开关是关闭的")
            caps = page.locator("#mmList .mm-cap").all_inner_texts()
            check(any("K" in c and "未探测" not in c for c in caps),
                  f"模型行显示能力徽标({caps})")

            # 从服务获取:新增服务端有、本地没有的模型
            page.click("#btnFetchModels")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'fetch_models')")
            call = calls(page, "fetch_models")[-1]
            check(call[1] == "deepseek" and call[2] == PAGE_URL and call[3] == "sk-1",
                  f"fetch_models 带上供应商地址与 Key({call[1:]})")
            page.wait_for_function(
                "() => document.querySelectorAll('#mmList .mm-row').length === 3")
            check(True, "从服务获取后补进新模型(2 → 3)")
            names = page.eval_on_selector_all(
                "#mmList .mmname", "els => els.map(e => e.value)")
            check("deepseek-coder" in names, f"新模型出现在清单里({names})")

            # 单个模型测试:调用要带"正在编辑的那份"地址与 Key
            page.click("#mmList .mm-row:nth-child(1) .mmtest")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'test_model')")
            call = calls(page, "test_model")[-1]
            check(call[1] == "deepseek" and call[2] == "deepseek-chat"
                  and call[3] == PAGE_URL and call[4] == "sk-1",
                  f"测试调用带 供应商/模型/地址/Key({call[1:]})")
            page.wait_for_function(
                "() => (document.getElementById('mmNote') || {}).className === 'mm-note good'")
            note = page.inner_text("#mmNote")
            check("已连通" in note and "262" in note, f"测试结果显示连通与窗口({note})")
            cap1 = page.locator("#mmList .mm-cap").first.inner_text()
            check("262" in cap1, f"测试结论回写到能力徽标({cap1})")

            # 手加一个模型
            before = page.locator("#mmList .mm-row").count()
            page.click("#btnAddModel")
            page.wait_for_function(
                f"() => document.querySelectorAll('#mmList .mm-row').length === {before + 1}")
            check(True, "「＋ 添加模型」多出一行")
            page.fill("#mmList .mm-row:nth-child(4) .mmname", "deepseek-chat-new")
            page.fill("#pvName", "DeepSeek 主号")

            # 保存:提交里要带模型清单与启停开关,空名字的行要被清掉
            page.click("#btnProvSave")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'save_providers')")
            call = calls(page, "save_providers")[-1]
            payload = json.loads(call[1])
            check(call[2] == "gzasc", f"没动圆点 → 默认还是内置({call[2]})")
            check(len(payload) == 1 and payload[0]["id"] == "deepseek",
                  "提交里不含内置项(内置由后端按代码补回)")
            item = payload[0]
            check(item["name"] == "DeepSeek 主号", "改名被提交")
            check([m["name"] for m in item["models"]] ==
                  ["deepseek-chat", "deepseek-reasoner", "deepseek-coder",
                   "deepseek-chat-new"], f"模型清单完整提交({item['models']})")
            check(item["models"][1]["enabled"] is False, "停用状态被提交")
            check(item["enabled"] is True, "供应商启停开关被提交")
            page.wait_for_function(
                "() => !document.getElementById('provOverlay').classList.contains('show')")
            check(True, "保存后弹窗关闭")

            # ---- 新:Ctrl+K 打开模型管理 ----
            page.keyboard.press("Control+k")
            page.wait_for_selector("#provOverlay.show")
            check(True, "Ctrl+K 打开模型管理")
            page.click("#btnProvClose")

            # ---- 新:侧栏会话搜索 ----
            page.fill("#sessSearch", "三体")
            page.press("#sessSearch", "Enter")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'search_sessions')")
            check(calls(page, "search_sessions")[-1][1] == "三体",
                  "搜索用了输入的关键词")
            page.wait_for_function(
                "() => document.querySelectorAll('#sessions .sess').length === 1")
            check("三体" in page.inner_text("#sessions .sess-hit"),
                  "命中条目显示上下文片段")
            page.click("#sessions .sess")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'resume_session')")
            check(calls(page, "resume_session")[-1][1] == 1, "点命中项切到该会话")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'refresh_sessions')")
            check(page.input_value("#sessSearch") == "", "点进去后自动清空搜索框")

            # ---- 新:导出当前会话 ----
            page.click("#btnExport")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'export_session')")
            page.wait_for_function(
                "() => document.getElementById('toasts').innerText.includes('已导出')")
            check(True, "点「⬇ 导出」调用 export_session 并提示结果")

            # ---- 新:任务面板按「供应商 × 模型」列可切项 ----
            page.click("#taskModelBtn")
            page.wait_for_function(
                "() => document.querySelectorAll('#taskModelMenu .trow').length === 2")
            check(True, "可切列表按供应商×模型展开(2 项)")
            check(page.locator("#taskModelMenu .trow.on").count() == 1,
                  "当前在用的那一项有选中标记(用 供应商::模型 判断)")
            # 注意:菜单第一行是提示文字(.tmenu-hint),所以按文本点模型行
            page.click("#taskModelMenu .trow:has-text('deepseek-chat')")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'set_session_model')")
            call = calls(page, "set_session_model")[-1]
            check(call[1] == "deepseek" and call[2] == "deepseek-chat",
                  f"切换带走具体模型名({call[1:]})")

            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"✗ {len(failures)} 项失败:")
        for f in failures:
            print("  -", f)
        return 1
    print("✓ v0.9.13 前端回归全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
