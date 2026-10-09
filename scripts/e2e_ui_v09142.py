# -*- coding: utf-8 -*-
"""v0.9.142「智能配置」那一格 —— 真 Chromium 点开一遍。

为什么必须真浏览器:这一格的卖点全在"看得见"上 —— 灰掉哪些输入框、那一行写的
是什么、关掉之后原来填的数还在不在。Python 侧能证明 `auto_config` 存进去了、
运行时读到空,但那三件事一件都不等于"用户知道刚刚发生了什么"。

要钉的九件事:
1. 开关在场,默认是**关**的(今天每一份配置都不该被这次升级改掉);
2. 关着时那一行说"下面填了什么就按什么走";
3. 打开开关 → 下面那些输入框灰掉(不是消失,是灰掉并留在原处);
4. 灰掉**不清空**:原来填的 1000000 还在框里(关掉就该回到那份声明);
5. 开着时那一行报出"窗口 / 图片输入 / 推理档位"三项;
6. 目录里查不到的模型 → 那一行是**警告色**并明说"不知道",而不是显示一个凭空数;
7. 关掉开关 → 输入框恢复可点;
8. 保存开着的那一份 → 交给后端的 cfg 里同时有 `auto_config` 与原来那几项;
9. 全程零页面错误。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v09142.py
"""
from __future__ import annotations

import json
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

from e2e_launch import launch          # noqa: E402

STUB_JS = r"""
window.__SAVE__ = [];
window.__AUTO_MODEL__ = "kimi-k3";
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.142", app_name: "引路学长", permission_mode: "confirm",
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
  save_providers: (s) => { window.__SAVE__.push(s); return Promise.resolve({ok: true}); },
  get_providers: () => Promise.resolve({ok: true, active_provider: "test", presets: [],
    providers: [{id: "test", name: "Test", base_url: "https://x.invalid/v1",
                 api_key: "k", model: "kimi-k3", enabled: true,
                 models: [{name: "kimi-k3", enabled: true,
                           cfg: {context_window: 1000000, max_output_tokens: 8192}}]}]}),
  model_auto_info: (name) => Promise.resolve(
    name === "not-a-real-model-xyz"
      ? {ok: true, model: name, known: false, detected: false, window: 0,
         vision: false, max_out: 0, levels: [], kind: "hidden"}
      : {ok: true, model: name, known: true, detected: false, window: 1000000,
         vision: true, max_out: 0, levels: ["default", "low", "high"], kind: "effort"}),
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__SAVE__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
"""

MODEL = {"name": "kimi-k3", "cfg": {"context_window": 1000000,
                                    "max_output_tokens": 8192}}


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


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

    def state():
        return page.evaluate("""() => {
          const dlg = document.querySelector('#mmCfgOverlay .mcf-dialog');
          const note = document.getElementById('mcfAutoNote');
          const win = document.getElementById('mcfWindow');
          return {auto: document.getElementById('mcfAuto').checked,
                  gray: dlg.classList.contains('auto'),
                  winClickable: getComputedStyle(win).pointerEvents !== 'none',
                  winValue: win.value,
                  note: note.textContent, cls: note.className};
        }""")

    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_selector("#ver", timeout=15000)
        page.wait_for_function("() => typeof mcfOpen === 'function'")

        # 走真路径:顶栏那颗「管理模型」背后的 `openProvDlg()` → 模型清单渲染出来
        # → 点这一行的「配置」。直接调 `mcfOpen` 会跳过整条渲染路,保存那一步就
        # 是在测一个从没被填过的 provData(第一版我就这么骗了自己一次)。
        page.evaluate("() => openProvDlg()")
        page.wait_for_timeout(600)
        page.click(".mmcfg")
        page.wait_for_timeout(250)
        s = state()
        check(s["auto"] is False, "1. 开关默认是**关**的(旧配置一份都不该被这次升级改掉)")
        check("关着" in s["note"], f"2. 关着时那一行说的是今天的行为(实测:{s['note'][:40]})")

        page.evaluate("() => { document.getElementById('mcfAuto').click(); }")
        page.wait_for_timeout(400)
        s = state()
        check(s["auto"] and s["gray"], "3. 打开开关 → 下面那些输入框整块灰掉")
        check(s["winValue"] == "1000000", "4. 灰掉**不清空**:原来填的 1000000 还在框里")
        check("开着 →" in s["note"] and "窗口" in s["note"] and "图片输入" in s["note"]
              and "推理档位" in s["note"], f"5. 那一行报出三项(实测:{s['note'][:70]})")
        check("on" in s["cls"], "   已知模型 → 那一行是正常色")

        page.evaluate("""() => {
            mcfClose();
            provData[0].models[0].name = 'not-a-real-model-xyz';
            provData[0].models[0].cfg = {};
            renderProvAll();
        }""")
        page.click(".mmcfg")
        page.evaluate("() => { document.getElementById('mcfAuto').checked = true;"
                      " document.getElementById('mcfAuto').dispatchEvent("
                      "new Event('change')); }")
        page.wait_for_timeout(400)
        s = state()
        check("warn" in s["cls"] and "目录里没有" in s["note"],
              f"6. 查不到的模型 → 警告色并明说不知道(实测:{s['note'][:60]})")

        page.evaluate("""() => {
            mcfClose();
            provData[0].models[0].name = 'kimi-k3';
            provData[0].models[0].cfg = {context_window: 1000000,
                                         max_output_tokens: 8192};
            renderProvAll();
        }""")
        page.click(".mmcfg")
        page.evaluate("() => { document.getElementById('mcfAuto').checked = true;"
                      " document.getElementById('mcfAuto').dispatchEvent("
                      "new Event('change')); }")
        page.wait_for_timeout(300)
        page.evaluate("() => { document.getElementById('mcfAuto').click(); }")
        page.wait_for_timeout(250)
        s = state()
        check(s["winClickable"] and not s["gray"], "7. 关掉开关 → 输入框恢复可点")

        page.evaluate("() => { document.getElementById('mcfAuto').click(); }")
        page.evaluate("() => mcfSave()")
        page.wait_for_timeout(600)
        saved = page.evaluate("() => window.__SAVE__")
        cfgs = []
        # `saveProvItems` 交出去的是**一个 JSON 字符串**(前端到桥就这一种形状)。
        for item in saved:
            if isinstance(item, str) and item.lstrip().startswith("["):
                for prov in json.loads(item):
                    for model in prov.get("models") or []:
                        if "cfg" in model:
                            cfgs.append(model["cfg"])
        check(any(c.get("auto_config") is True and c.get("context_window") == 1000000
                  for c in cfgs),
              f"8. 保存开着的那一份:cfg 里同时有 auto_config 与原来那几项(实测 {cfgs})")
        check(not errors, "9. 全程零页面错误" + (f":{errors[:2]}" if errors else ""))
        browser.close()
    shutdown()
    print(f"\n{ran - len(failures)}/{ran} 通过")
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("RESULT: CLEAN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
