"""v0.9.03 前端回归:真实浏览器里验证「添加模型」与「每日 token 用量」弹窗。

被测的是交付用的前端(frontend/index.html),Playwright + 真 Chromium,
stub 掉 pywebview 桥(get_providers / get_token_usage / save_providers 返回
结构上与后端一致的数据),然后按用户真实操作路径点开弹窗,
用 DOM 事实断言(不看截图):

    1. 顶栏「模型」标识可点开「模型管理」:内置行不可删除、key 显示「已内置」;
    2. 填表添加一个新模型 → save_providers 收到的 payload 含新模型与所选 active;
    3. 顶栏「📊 用量」弹窗:汇总卡片、每日圆环(conic-gradient)、模型明细表;
    4. 范围切换(今日/近7天/近30天)会带不同 days 重新请求。

跑法(项目根目录):.venv\\Scripts\\python.exe scripts\\e2e_model_usage.py
"""
from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "_tmp_model_check"
SHOTS = {
    "prov": OUT_DIR / "model-dlg.png",
    "usage": OUT_DIR / "usage-dlg.png",
}


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/index.html", httpd.shutdown


def _stub_js(calls_json: str) -> str:
    return f"""
window.__E2E_CALLS__ = {calls_json};
window.__E2E_STATS__ = {{
  ok: true,
  range: {{ days: 7, start_date: "2026-09-08", end_date: "2026-09-14" }},
  summary: {{ prompt: 1500, completion: 300, total: 1800, requests: 6 }},
  models: [
    {{ key: "gzasc/qwen3.8-27b", provider: "gzasc", model: "qwen3.8-27b",
       prompt: 1200, completion: 200, total: 1400, requests: 4 }},
    {{ key: "openai/gpt-x", provider: "openai", model: "gpt-x",
       prompt: 300, completion: 100, total: 400, requests: 2 }},
  ],
  days: Array.from({{length: 7}}, (_, i) => {{
    const d = new Date(2026, 8, 8 + i);
    const iso = d.toISOString().slice(0, 10);
    if (i === 6) return {{ date: iso, prompt: 1500, completion: 300,
      total: 1800, requests: 6,
      models: [
        {{ key: "gzasc/qwen3.8-27b", provider: "gzasc", model: "qwen3.8-27b",
           prompt: 1200, completion: 200, total: 1400, requests: 4 }},
        {{ key: "openai/gpt-x", provider: "openai", model: "gpt-x",
           prompt: 300, completion: 100, total: 400, requests: 2 }},
      ] }};
    return {{ date: iso, prompt: 0, completion: 0, total: 0, requests: 0, models: [] }};
  }}),
}};
window.pywebview = {{
  api: {{
    get_info: () => Promise.resolve({{
      version: "0.9.03", workspace: "D:/e2e", permission_mode: "confirm",
      session_key: "s1", api_key_configured: true, vision: false,
      model: "qwen3.8-27b", active_provider: "gzasc",
      providers: [
        {{ id: "gzasc", name: "广州应用科技学院", model: "qwen3.8-27b", has_key: true }},
      ],
      campus_map: null, memory_count: 0, memory_user_count: 0,
      sandbox: {{ enabled: false }}, library_health: {{ status: "ok", notice: "" }},
    }}),
    get_providers: () => {{
      window.__E2E_CALLS__.push(["get_providers"]);
      return Promise.resolve({{
        providers: [
          {{ id: "gzasc", name: "广州应用科技学院",
             base_url: "http://10.223.24.16:8080/v1", api_key: "", model: "qwen3.8-27b",
             builtin: true }},
        ],
        active_provider: "gzasc",
      }});
    }},
    save_providers: (providersJson, activeId) => {{
      window.__E2E_CALLS__.push(["save_providers",
                                  JSON.parse(providersJson), activeId]);
      return Promise.resolve({{ ok: true }});
    }},
    get_token_usage: (days) => {{
      window.__E2E_CALLS__.push(["get_token_usage", days]);
      return Promise.resolve(window.__E2E_STATS__);
    }},
    refresh_sessions: () => Promise.resolve({{ ok: true, items: [] }}),
    get_suggestions: () => Promise.resolve({{ items: [] }}),
    get_asset: () => Promise.resolve({{ ok: false }}),
    set_provider: () => Promise.resolve({{ ok: true }}),
    confirm_reply: () => Promise.resolve({{ ok: true }}),
    delete_session: () => Promise.resolve({{ ok: true }}),
  }},
}};
"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    url, shutdown = _serve(FRONTEND)
    calls: list = []
    failures: list[str] = []
    OUT_DIR.mkdir(exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.add_init_script(_stub_js("[]"))
            page.goto(url, wait_until="load")

            def read_calls() -> list:
                return page.evaluate("() => window.__E2E_CALLS__")
            page.wait_for_function(
                "() => document.getElementById('modelPill') && "
                "document.getElementById('modelPill').textContent.includes('广州应用科技学院')")

            # --- 1. 模型管理弹窗:内置行不可删、无 key 输入 ---
            page.click("#modelPill")
            page.wait_for_selector("#provOverlay.show")
            page.wait_for_selector(".prov-row")
            rows = page.eval_on_selector_all(".prov-row", "els => els.length")
            del_btns = page.eval_on_selector_all(
                ".prov-row .del", "els => els.length")
            builtin_tag = page.evaluate(
                "() => document.querySelector('.prov-row .buitag') !== null")
            page.screenshot(path=str(SHOTS["prov"]))
            if rows != 1:
                failures.append(f"模型列表应有 1 行(内置),实际 {rows}")
            if del_btns != 0:
                failures.append(f"内置行不应有删除按钮,实际 {del_btns} 个")
            if not builtin_tag:
                failures.append("内置行缺少「内置」标签")

            # --- 2. 填表添加新模型并保存 ---
            page.fill("#pvName", "DeepSeek")
            page.fill("#pvBase", "https://api.deepseek.com/v1")
            page.fill("#pvKey", "sk-test-123")
            page.fill("#pvModel", "deepseek-chat")
            page.click("#btnProvSave")
            page.wait_for_timeout(300)
            calls = read_calls()
            saved = [c for c in calls if c[0] == "save_providers"]
            if not saved:
                failures.append("保存没有触发 save_providers")
            else:
                items, active = saved[0][1], saved[0][2]
                if len(items) != 2 or items[1]["name"] != "DeepSeek":
                    failures.append(f"保存的列表应为 内置+DeepSeek,实际 {items}")
                if any(it["id"] == "gzasc" and it["base_url"] != "http://10.223.24.16:8080/v1"
                       for it in items):
                    failures.append("内置供应商被改写(不应发生)")
                if active != "gzasc":
                    failures.append(f"active 应保持 gzasc,实际 {active}")
            still_open = page.evaluate(
                "() => document.getElementById('provOverlay').classList.contains('show')")
            if still_open:
                failures.append("保存成功后弹窗应关闭")

            # --- 3. 用量弹窗:汇总/圆环/明细 ---
            page.click("#btnUsage")
            page.wait_for_selector("#usageOverlay.show")
            page.wait_for_selector(".usage-day")
            page.wait_for_timeout(200)
            n_days = page.eval_on_selector_all(".usage-day", "els => els.length")
            rings = page.eval_on_selector_all(".usage-ring", "els => els.length")
            rows_tbl = page.eval_on_selector_all(".usage-table tbody tr", "els => els.length")
            grad = page.evaluate(
                "() => document.querySelector('.usage-ring').style.background")
            page.screenshot(path=str(SHOTS["usage"]))
            if n_days != 7:
                failures.append(f"近 7 天应渲染 7 张日卡,实际 {n_days}")
            if rings < 7:
                failures.append(f"圆环数量异常:{rings}")
            if rows_tbl != 2:
                failures.append(f"模型明细应 2 行,实际 {rows_tbl}")
            if "conic-gradient" not in grad:
                failures.append(f"圆环背景应为 conic-gradient,实际 {grad!r}")

            # --- 4. 范围切换带 days 重新请求 ---
            page.click("#usageRange button[data-days='30']")
            page.wait_for_timeout(200)
            calls = read_calls()
            day_calls = [c for c in calls if c[0] == "get_token_usage"]
            if not day_calls or day_calls[-1][1] != 30:
                failures.append(f"切到近 30 天应带 days=30 请求,实际 {day_calls}")

            browser.close()
    finally:
        shutdown()

    if failures:
        print("\n失败项:")
        for f in failures:
            print("  -", f)
        return 1
    print(f"调用序列:{calls}")
    print(f"截图:{SHOTS['prov']} , {SHOTS['usage']}")
    print("\nv0.9.03 前端验证通过:模型管理/添加模型与每日 token 用量弹窗均正常。")
    return 0


if __name__ == "__main__":
    calls_init = None
    sys.exit(main())
