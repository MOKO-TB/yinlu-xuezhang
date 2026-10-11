"""模型管理弹窗回归:真 Chromium 里走一遍「供应商清单 + 详情面板」。

**这一支是 v0.9.152 重写的,旧名叫 `e2e_model_usage.py`。** 为什么重写、为什么改名:

- 旧那支是 **v0.9.03 时代**的,验的是「模型管理 = 一张表单」(填名字 / 接口 / Key /
  模型名 → 保存)。界面后来改成了**供应商清单 + 详情面板**(`.prov-row` / `.buitag` /
  `#pvModel` 在前端里各 **0 命中**),而它**没有跟着改** —— 于是从那以后一直是红的,
  红到没人看;最后被改成"自报已过时、以退出码 2 退出"。**红着没人看的测试等于没有。**
- 可这一格**是用户最需要的那一格**:内置校园 Key 今天被网关拒着(`HANDOFF.md` §6 第 54 条),
  而"换成自己的模型"这条路**只有这一个入口** —— 它此前**零断言覆盖**
  (`scripts/shot_model_dialog.py` 只有截图、没有一条断言;`scripts/e2e_ui_v09142.py`
  验的是「智能配置」那颗开关,不是这份清单)。
- **改名的理由**:旧名字里的 `usage` 已经名不副实 —— 用量那半后来被重写成一个大面板
  (热力图 / 费用 / 预算条 / 供应商占比卡),那是另一轮的事。**这一支不假装覆盖它**
  (名字里留着 `usage` 就是在假装)。

判据全用 **DOM 事实 + 桥调用记录**,不看截图。跑法(项目根目录,需要真 Chromium):

    .venv\\Scripts\\python.exe scripts\\e2e_model_config.py

退出码 0 = 通过;1 = 有断言失败(会打印明细 + 错误清单)。
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
# 这一件的东西**全在界面上**,所以出包那一场必须对着包内那份前端再跑一遍:
#   python scripts/e2e_model_config.py --frontend dist/引路学长/_internal/frontend
if "--frontend" in sys.argv:
    FRONTEND = Path(sys.argv[sys.argv.index("--frontend") + 1]).resolve()
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from e2e_launch import launch          # noqa: E402

# 预设那一项的形状由 `get_providers` 的 `presets` 字段决定,断言里用的名字从这里取,
# 免得脚本里同一个字符串写三遍(改一处漏一处,红起来看着像界面坏了)。
PRESET_NAME = "DeepSeek"
PRESET_BASE = "https://api.deepseek.com/v1"

STUB_JS = r"""
window.__SAVE__ = [];
window.__PROV__ = {ok: true, active_provider: "gzasc", presets: [
  {label: "DeepSeek", name: "DeepSeek", base_url: "https://api.deepseek.com/v1",
   models: ["deepseek-chat"]},
], providers: [
  {id: "gzasc", name: "广州应用科技学院", base_url: "http://10.223.24.16:8080/v1",
   api_key: "", model: "qwen3.8-27b", builtin: true, enabled: true,
   models: [{name: "qwen3.8-27b", enabled: true}]},
]};
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.152", app_name: "引路学长", permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    model: "qwen3.8-27b", active_provider: "gzasc", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "gzasc", name: "广州应用科技学院", model: "qwen3.8-27b",
                 has_key: true}],
    api_key_configured: true, memory_count: 0, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    instructions: {ok: true, line: "", layers: []},
    session_metrics: {ok: false, selectable: []}}),
  get_providers: () => { window.__SAVE__.push(["get_providers"]);
                         return Promise.resolve(window.__PROV__); },
  save_providers: (s, active) => {
    window.__SAVE__.push(["save_providers", JSON.parse(s), active]);
    return Promise.resolve({ok: true}); },
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__SAVE__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
"""


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *a):          # 静态服务器的日志会把断言刷没
        pass


def _serve(directory: Path):
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

    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.add_init_script(STUB_JS)
        try:
            page.goto(url)
            page.wait_for_selector("#ver", timeout=15000)

            # --- 1. 打开「模型管理」:走真路径(点顶栏那颗),不直接调 openProvDlg ---
            page.click("#modelPill")
            page.wait_for_selector("#provOverlay.show", timeout=8000)
            check(page.evaluate("() => document.getElementById('provOverlay')"
                                ".classList.contains('show')"),
                  "1. 顶栏那颗「模型」点开的是模型管理弹窗")

            # --- 2. 清单:内置那一行,而且它是当前默认 ---
            rows = page.eval_on_selector_all("#provList .prov-item", "els => els.length")
            check(rows == 1, f"2. 清单里只有内置那一条(实际 {rows} 行)")
            name = page.eval_on_selector(
                "#provList .prov-item .pi-name", "el => el.textContent")
            check(name == "广州应用科技学院", f"2. 那一行报的是供应商名(实际 {name!r})")
            tag = page.eval_on_selector(
                "#provList .prov-item .pi-n", "el => el.textContent")
            check(tag == "内置", f"2. 它带「内置」标(实际 {tag!r})")
            is_default = page.eval_on_selector(
                "#provList .prov-item", "el => !!el.querySelector('.pi-default')")
            check(is_default, "2. 它标着「默认」(当前选中的那个)")
            checked = page.eval_on_selector(
                "#provList .prov-item input[type=radio]", "el => el.checked")
            check(checked, "2. 它是勾上的那一个")

            # --- 3. 内置那条**改不了**:详情面板是只读块,没有输入框也没有删除键 ---
            page.click("#provList .prov-item")
            page.wait_for_timeout(200)
            ro = page.eval_on_selector_all("#provMain .prov-ro", "els => els.length")
            check(ro == 1, "3. 内置那条的详情是只读块(.prov-ro)")
            for sel, label in (("#pvName", "名称输入框"), ("#pvKey", "Key 输入框"),
                               ("#btnDelProv", "删除键")):
                n = page.eval_on_selector_all(sel, "els => els.length")
                check(n == 0, f"3. 内置那条**没有**{label}(它由学校侧提供)")

            # --- 4. 用「＋ 常用供应商」加一个:预设把地址与模型名一起填好 ---
            page.select_option("#pvPreset", index=1)
            page.wait_for_timeout(250)
            rows2 = page.eval_on_selector_all("#provList .prov-item", "els => els.length")
            check(rows2 == 2, f"4. 清单变成两条(实际 {rows2} 行)")
            pv_name = page.eval_on_selector("#pvName", "el => el.value")
            pv_base = page.eval_on_selector("#pvBase", "el => el.value")
            check(pv_name == PRESET_NAME, f"4. 名称被预设填好(实际 {pv_name!r})")
            check(pv_base == PRESET_BASE, f"4. 接口地址被预设填好(实际 {pv_base!r})")
            mm = page.eval_on_selector("#mmCount", "el => el.textContent")
            check(mm == "1/1", f"4. 模型数报的是「启用/总数」(实际 {mm!r})")

            # --- 5. 再加一个模型:行数跟着长,新那行的名字框是空的 ---
            page.click("#btnAddModel")
            page.wait_for_timeout(200)
            mrows = page.eval_on_selector_all("#mmList .mm-row", "els => els.length")
            check(mrows == 2, f"5. 模型行变成两条(实际 {mrows} 行)")
            names = page.eval_on_selector_all("#mmList .mmname", "els => els.map(e => e.value)")
            check(names[-1] == "", f"5. 新那行是空的、等你去填(实际 {names[-1]!r})")
            page.fill("#mmList .mm-row:last-child .mmname", "deepseek-reasoner")

            # --- 6. 保存:载荷里两条,内置那条一个字节都没被改,默认仍是内置 ---
            page.click("#btnProvSave")
            page.wait_for_timeout(500)
            saved = page.evaluate(
                "() => window.__SAVE__.filter(c => c[0] === 'save_providers')")
            check(len(saved) == 1, f"6. 保存触发了 save_providers(实际 {len(saved)} 次)")
            if saved:
                items, active = saved[0][1], saved[0][2]
                # **载荷里只该有 1 条,而且内置那条不在里面** —— 这不是漏了,是机制:
                # `saveProvItems` 头一行就是 `provData.filter(p => !p.builtin)`
                # (前端 5338 那行的注释原话:「已剔除内置项,内置由后端按代码补回」)。
                # 所以"内置改不了"这件事有两层:界面上没有输入框(第 3 组),
                # **载荷里根本不带它**(这一组)。第一版我把旧脚本的口径照搬过来、
                # 断言"两条且内置的地址没变",结果红在一个**假的**丢数据上 ——
                # 真相是这条设计比那条断言更强。
                check(len(items) == 1, f"6. 载荷里只有用户加的那条(实际 {len(items)} 条)")
                check(not any(it.get("id") == "gzasc" for it in items),
                      "6. **内置那条不进载荷**(它由后端按代码补回,界面改不动它)")
                new = [it for it in items if it.get("id") != "gzasc"]
                check(len(new) == 1 and new[0].get("name") == PRESET_NAME,
                      f"6. 新那条带着名字与地址({new[0].get('name') if new else None!r})")
                if new:
                    mnames = [m.get("name") for m in (new[0].get("models") or [])]
                    check("deepseek-reasoner" in mnames,
                          f"6. 刚填的模型名进了载荷({mnames})")
                    check(new[0].get("base_url") == PRESET_BASE,
                          f"6. 地址跟着走(实际 {new[0].get('base_url')!r})")
                check(active == "gzasc",
                      f"6. 默认仍是内置那条(选中哪个由左边那个圆点决定,实际 {active!r})")
            closed = page.evaluate(
                "() => !document.getElementById('provOverlay').classList.contains('show')")
            check(closed, "6. 保存成功后弹窗自己关掉")

            check(not errors, f"全程没有 pageerror/console error({errors[:3]})")
        finally:
            browser.close()
            shutdown()

    print()
    if failures:
        print(f"失败项({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print(f"PASS: 模型管理弹窗 {ran} 项全过 —— 清单/只读的内置/预设加供应商/"
          "加模型/保存载荷/默认不被动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
