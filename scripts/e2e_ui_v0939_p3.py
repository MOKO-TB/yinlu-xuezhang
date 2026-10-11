# -*- coding: utf-8 -*-
"""v0.9.39 p3 前端回归:14 个面板入口搬到最左入口栏以后,**每一颗仍点到原来那个地方**。

用户拿图 p3(Qoder 左栏)/p4(ZCode)点名:「上面的工具堆太多了没有简洁性希望可以再
进一步修改一下」。拍板 = 「面板入口移到左侧栏」。这一轮改的是**位置**,所以这一场
只回答一个问题:搬家有没有把哪个入口搬丢、搬成死图标、或者把"沙箱开/关""记忆 7 条"
这种真状态搬没。

做法:**真点击每一颗,一颗不落**,每颗断它自己那条真去处 ——
  弹层类 → 那个 `#*Overlay` 真的 `.show` 了,而且里面画的是后端给的内容(不是空壳);
  右侧那栏 → `.app.act-open` + 按钮 `aria-expanded="true"`;
  地图 → `.map-panel.open`(地图数据出自真 `minicode.campus.frontend_payload()`);
  动作类 → 本机桥那个**具体方法**被叫到、参数对(记忆整理送 "all"、沙箱送的是翻转后的值);
  主题 → `html.dark` 真翻了,而且把当下档位报给了本机(p2 那条线不能被 p3 弄断)。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0939_p3.py
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

# 界面上从上到下那一串(与 tests/test_rail_entries.py 那份清单同源):
# v0.9.39 搬进来 14 颗,v0.9.89 加「体检」(后来下了架,清单跟着摘),
# v0.9.112 加「自动化」;v0.9.159 加底座三键。
# ⚠ 「◀ 收起左侧栏」**不在**这份清单里:它不是面板入口,是开关,归顶栏
#   (tests/test_rail_entries.py 的 TOPBAR_ICON_IDS 同一口径)—— v0.9.112 起
#   它搬进顶栏,留在这里会把「顶栏不再有面板入口」那条检查自己撞红。
RAIL_IDS = ["btnMap", "btnSchedule", "btnMood", "btnPlan", "btnActivity",
            "btnSkills", "btnMemory", "btnUsage",
            "btnTerminal", "btnAutomate",        # v0.9.112:终端(162 起指向底部终端坞)
            "btnMyData", "btnSandbox",
            "btnExport", "btnUndo", "btnCompact", "btnTheme"]
# v0.9.162:底座四入口(Git/文件/帮手/侧聊)从入口栏撤下,入口栏回原样 ——
# 页面入口收进顶栏「切换面板」(btnPanelToggle)与底座自己的标签条。

STUB_JS = """
window.__CALLS__ = [];
window.__CAMPUS_MAP__ = @@MAP@@;
/* 记忆条数与沙箱状态都从 get_info 来:这两格上的字必须是真数,不是写死的样例。 */
window.__SANDBOX__ = {available: true, enabled: true,
                      description: "沙箱:工作区内自动,区外按权限档"};
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.39", app_name: "引路学长",
    permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    session_key: "s1", model: "test-model", active_provider: "test",
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    memory_count: 3, memory_user_count: 4,        // → 格子上应写「记忆 7」
    mcp_servers: [], sandbox: window.__SANDBOX__, vision: false,
    campus_map: window.__CAMPUS_MAP__, library_health: {},
    skills: [], kb_count: 0, session_metrics: {ok: false, selectable: []}
  }),
  /* ---- 弹层各自的数据(形状照后端,不是随手编的字典) ---- */
  get_schedule_view: () => Promise.resolve({ok: true, schedule: {registered: false}}),
  mood_review: () => Promise.resolve({ok: true, text: "", days: 7, count: 0, labels: {}}),
  get_plan: () => Promise.resolve({ok: true, has_plan: false, steps: [], total: 0,
                                   done: 0, ready: [], blocked: [], failed: [],
                                   running: [], dispatch_text: ""}),
  get_skills: () => Promise.resolve({ok: true, skills: []}),
  get_token_usage: () => Promise.resolve({ok: true, summary: {total: 1234, prompt: 900,
                                                              completion: 334, requests: 7},
                                          models: [], days: []}),
  mydata_inventory: () => Promise.resolve({ok: true, app_dir: "D:/demo/app",
                                           backend: "dir", workspace: "D:/demo/campus",
                                           warnings: [], groups: [],
                                           totals: {files: 0, size: "0 B"}}),
  /* 体检载荷由真 minicode.health 造(@@HEALTH@@,见 _stub_health):状态凑齐三种,
     好让"徽章字来自后端"这条在浏览器里也验得到。 */
  get_health: (deep) => { window.__CALLS__.push(["get_health", deep]);
    return Promise.resolve(@@HEALTH@@); },
  get_activity: () => Promise.resolve({ok: true, empty: true, groups: [],
                                       summary: {calls: 0, errors: 0, running: 0}}),
  get_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
  /* ---- 动作类:这一场要的就是"哪个方法被叫到、参数是什么" ---- */
  consolidate_memory: (scope) => { window.__CALLS__.push(["consolidate_memory", scope]);
    return Promise.resolve({ok: true, project: 4, user: 3}); },   // → 格子改口「记忆 7」
  set_sandbox: (on) => { window.__CALLS__.push(["set_sandbox", on]);
    window.__SANDBOX__ = Object.assign({}, window.__SANDBOX__, {enabled: on});
    return Promise.resolve({ok: true, sandbox: window.__SANDBOX__}); },
  export_session: () => { window.__CALLS__.push(["export_session"]);
    return Promise.resolve({ok: true, msg: "已导出"}); },
  undo: () => { window.__CALLS__.push(["undo"]); return Promise.resolve({ok: true}); },
  compact_now: () => { window.__CALLS__.push(["compact_now"]);
    return Promise.resolve({ok: true, msg: "已压缩"}); },
  /* v0.9.162:终端坞要真行 —— 兜底 {ok:true} 没有 jobs,坞里永远只有空态那句。 */
  get_terminal: () => { window.__CALLS__.push(["get_terminal"]);
    return Promise.resolve({ok: true, jobs: [
      {id: "j1", command: "pytest -q tests", running: true, exit_code: null,
       killed: false, chars: 12, dropped: 0, elapsed: 5, tail: "testing..."}],
      buffer_chars: 4000, note: ""}); },
  set_chrome: (dark) => { window.__CALLS__.push(["set_chrome", dark]);
    return Promise.resolve({ok: true, applied: ["immersive-dark-mode"], failed: [], msg: ""}); },
  /* ---- 开机那一路要问的,给到不报错的最简真相 ---- */
  get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
  get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
  get_suggestions: () => Promise.resolve({suggestions: []}),
  get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
  get_session_metrics: () => Promise.resolve({ok: false}),
  refresh_sessions: () => Promise.resolve({ok: true}),
  read_clipboard: () => Promise.resolve({text: "", image: false, source: "empty"}),
  /* v0.9.117 两栏仪表盘的数据契约:summary 至少是空对象(renderUsage 直接读
     sum.cost_usd,undefined 会炸进 catch),heatmap 缺席走"暂无记录"那句。
     已登记的方法不走兜底记录器,账要自己记。 */
  get_usage_dashboard: () => {
    window.__CALLS__.push(["get_usage_dashboard"]);
    return Promise.resolve({ok: true, summary: {}, tiles: {},
                            meta: {}, models: [], heatmap: null});
  },
};
/* 没列出来的方法:记一笔账再回 {ok: true}。断言查的是"该出现的那个名字出现了",
   所以名字打错/换掉照样红 —— 兜底不会把错的方法名蒙过去。 */
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
"""

# 沙箱那颗在"这台没有沙箱"的机器上应当整颗不出现(不留一颗点了没反应的开关)
STUB_NO_SANDBOX = "window.__SANDBOX__ = {available: false, enabled: false, description: ''};"


def _stub_health() -> dict:
    """体检面板的载荷:行由真 `health._row` 造(字段名一漂这场就红),状态凑齐三种。

    写这份而不是随手编字典,是因为面板只认后端给的 `status`/`badge`/`action.kind`;
    后端改了字段名而这里还是老字典,浏览器就会画出一排空白格子还全绿。
    """
    from minicode import health as H
    rows = [
        H._row("dirs", "local", "数据目录可写", H.OK, detail="可写:D:/demo/app"),
        H._row("modelcfg", "local", "模型配置", H.BAD, detail="当前供应商没有可用密钥",
               fix="点右边「去配置模型」填 Key",
               action={"kind": "models", "label": "去配置模型"}),
        H._row("permission", "local", "权限与项目白名单", H.WARN,
               detail="档位「confirm」 · 白名单写了 2 条但**未生效**(没核对批准过)",
               fix="要生效请在对话里说一声,学长会弹确认框请你核对",
               action=H._ask("请把这个项目根下的 permissions.json 白名单批准一下"
                             "(用 permissions_trust 工具,我要在确认框里核对)。")),
    ]
    summary = {s: sum(1 for r in rows if r["status"] == s) for s in H.BADGE}
    return {"ok": True, "deep": False, "repo": False, "checked_at": "10:00:00",
            "total": len(rows), "summary": summary, "attention": ["模型配置"],
            "verdict": H._verdict(summary, rows, len(rows)),
            "groups": [{"key": k, "label": lb, "icon": ic,
                        "rows": [r for r in rows if r["group"] == k]}
                       for k, lb, ic in H.GROUPS]}


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


def main() -> int:
    from minicode import campus
    from playwright.sync_api import sync_playwright

    stub = STUB_JS.replace("@@MAP@@", json.dumps(campus.frontend_payload(),
                                                 ensure_ascii=False))
    stub = stub.replace("@@HEALTH@@", json.dumps(_stub_health(), ensure_ascii=False))
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []
    errors: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ✓ " if cond else "  ✗ ") + message)

    with sync_playwright() as p:
        from e2e_launch import launch      # 这台机器没装 headless shell:退路在同一份 helper 里
        browser = launch(p)

        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("response", lambda r: errors.append(f"http {r.status}: {r.url}")
                if r.status >= 400 else None)
        page.add_init_script(stub)
        page.goto(url, wait_until="load")
        page.wait_for_function("() => document.getElementById('btnMap') !== null")
        page.wait_for_timeout(400)

        def calls(name=None):
            allc = page.evaluate("() => window.__CALLS__")
            return allc if name is None else [c for c in allc if c[0] == name]

        def shown(ov):
            return page.evaluate(
                f"() => document.getElementById({json.dumps(ov)}).classList.contains('show')")

        def cap(bid):
            return page.evaluate(
                f"() => document.querySelector('#{bid} .cap').textContent.trim()")

        def close_overlays():
            page.evaluate("""() => document.querySelectorAll('.overlay.show, #appConfirmOverlay.show')
                .forEach(el => el.classList.remove('show'))""")

        # ---- 0. 结构:入口栏在最左,顶栏只剩状态 ----
        geo = page.evaluate("""() => {
            const rail = document.querySelector('.rail');
            const app = document.querySelector('.app');
            const tb = document.querySelector('.topbar');
            const ids = [...rail.querySelectorAll('button')].map(b => b.id);
            return {first: app.firstElementChild === rail, n: ids.length, ids: ids,
                    inTopbar: [...tb.querySelectorAll('button')].map(b => b.id)};
        }""")
        check(geo["first"], "入口栏是 .app 的第一个子元素(视觉上最左)")
        check(geo["ids"] == RAIL_IDS, f"入口栏里就是这一串、顺序也没乱:{geo['ids']}")
        check(not [b for b in geo["inTopbar"] if b in RAIL_IDS],
              f"顶栏不再有任何面板入口(只剩):{geo['inTopbar']}")

        # ---- 1. 两行格子上写的是真状态,不是样例字 ----
        check(cap("btnMemory") == "记忆 7",
              f"记忆那一格写的是后端给的两数之和:{cap('btnMemory')!r}")
        check(cap("btnSandbox") == "沙箱开",
              f"沙箱那一格写的是真开关状态:{cap('btnSandbox')!r}")
        check(page.is_visible("#btnSandbox"), "这台有沙箱 → 那颗露出来")
        check(page.evaluate("() => document.getElementById('btnSandbox')"
                            ".classList.contains('on')"),
              "沙箱开着时那一格有'开着'的样子(青底跟着真状态,不是装饰)")

        # ---- 2. 🗺 地图:开的是原来那块地图面板 ----
        page.click("#btnMap")
        page.wait_for_timeout(300)
        check(page.evaluate("() => !!document.querySelector('.map-panel.open')"),
              "地图入口仍然打开校园地图面板(不是新开一个弹层)")
        page.evaluate("() => window.CampusMap && window.CampusMap.toggle(false)")
        page.wait_for_timeout(150)

        # ---- 3. 📅 课表 ----
        page.click("#btnSchedule")
        page.wait_for_selector("#scheduleOverlay.show")
        check(shown("scheduleOverlay") and "还没有登记课表" in page.inner_text("#scBody"),
              "课表弹层打开,里面画的是后端那份 view 的真空档态")
        close_overlays()

        # ---- 4. 🌱 心情 ----
        page.click("#btnMood")
        page.wait_for_selector("#moodOverlay.show")
        check(shown("moodOverlay")
              and page.locator("#moScores button").count() == 6,
              "情绪弹层打开,五个分 + 「只写一句话」都在")
        close_overlays()

        # ---- 5. 🗂 计划 ----
        page.click("#btnPlan")
        page.wait_for_selector("#planOverlay.show")
        check(shown("planOverlay"), "计划弹层还是原来那个 #planOverlay")
        close_overlays()

        # ---- 6. 🛰 监控(右侧那一栏,不是弹层) ----
        page.click("#btnActivity")
        page.wait_for_selector(".app.act-open")
        check(page.get_attribute("#btnActivity", "aria-expanded") == "true",
              "监控那一格用 aria-expanded 说实话(展开=真展开)")
        page.click("#btnActivity")
        page.wait_for_timeout(200)
        check(not page.evaluate("() => document.querySelector('.app')"
                                ".classList.contains('act-open')"),
              "再点一次收得回去(两行格子不是一扇单向门)")

        # ---- 7. ⭐ 技能 ----
        page.click("#btnSkills")
        page.wait_for_selector("#skillsOverlay.show")
        check("还没有任何技能" in page.inner_text("#skillsBody"),
              "技能弹层里是 get_skills 给的空态那句话")
        close_overlays()

        # ---- 8. 🧠 记忆整理(动作类:要看见桥被叫到)----
        # v0.9.97 起这颗按钮打开的是「记一条 / 整理」卡片,**整理要点卡片里那颗
        # 「整理记忆」** —— 按钮本身不再自动整理(旧断言在这里空转过一场)。
        before = len(calls("consolidate_memory"))
        page.click("#btnMemory")
        page.wait_for_selector("#memoryOverlay.show")
        page.click("#memDream")
        page.wait_for_timeout(400)
        got = calls("consolidate_memory")
        check(len(got) == before + 1 and got[-1][1] == "all",
              f"记忆那一格仍然叫 consolidate_memory('all'):{got[-1:] }")
        check(cap("btnMemory") == "记忆 7",
              f"整理完按后端回的新条数改口:{cap('btnMemory')!r}")
        page.click("#memClose")
        page.wait_for_timeout(150)

        # ---- 9. 📊 用量 ----
        # v0.9.117 两栏仪表盘改版后,旧断言里的 `.usage-sum` 四格已不在 DOM 上
        # (CSS 还留着,渲染早换成 tiles/热力图)—— 现在钉的是:弹层打开、
        # 数据真敲了 get_usage_dashboard、空账说"暂无记录"而不是画零。
        page.click("#btnUsage")
        page.wait_for_selector("#usageOverlay.show")
        page.wait_for_timeout(250)
        check(len(calls("get_usage_dashboard")) == 1,
              "用量弹层的数据是问本机要的(get_usage_dashboard)")
        check("暂无记录" in page.inner_text("#usageBody"),
              "空账明说暂无记录,不拿零充数")
        close_overlays()

        # ---- 10. 💾 我的数据 ----
        page.click("#btnMyData")
        page.wait_for_selector("#myDataOverlay.show")
        check("D:/demo/app" in page.inner_text("#mdBody"),
              "我的数据弹层里是真的数据目录路径(mydata_inventory 给的)")
        close_overlays()

        # ---- 10b. 🩺 体检( v0.9.89 加,v0.9.112 下了架):这一段随之退役 ——
        # 面板、按钮、get_health 三样在前端里一个都不剩,留着只会在这里超时。

        # ---- 11. 🛡 沙箱:点下去送的是"翻转后"的值,格子上跟着改口 ----
        before = len(calls("set_sandbox"))
        page.click("#btnSandbox")
        page.wait_for_timeout(300)
        got = calls("set_sandbox")
        check(len(got) == before + 1 and got[-1][1] is False,
              f"沙箱那颗送的是翻转后的值:{got[-1:] }")
        check(cap("btnSandbox") == "沙箱关", f"关了就写在格子上:{cap('btnSandbox')!r}")

        # ---- 12. ⬇ 导出 / ↶ 撤销 / 🗜 压缩 ----
        page.click("#btnExport")
        page.wait_for_timeout(250)
        check(len(calls("export_session")) == 1, "导出仍叫 export_session")
        page.click("#btnUndo")
        page.wait_for_timeout(250)
        check(len(calls("undo")) == 1, "撤销仍叫 undo")
        page.click("#btnCompact")
        page.wait_for_timeout(250)
        check(len(calls("compact_now")) == 1, "压缩仍叫 compact_now")

        # ---- 13. 🌙 主题:翻了界面,也照 p2 那条线报给本机 ----
        dark_before = page.evaluate(
            "() => document.documentElement.classList.contains('dark')")
        page.click("#btnTheme")
        page.wait_for_timeout(300)
        dark_after = page.evaluate(
            "() => document.documentElement.classList.contains('dark')")
        check(dark_after is not dark_before, "主题真的翻了")
        check(["set_chrome", dark_after] in calls(),
              f"翻完把当下档位报给了本机(p2 那条线没被搬家弄断):{calls('set_chrome')}")
        # 格子上写的是"下一步能切去的那一档"(与 p2 那一场同一口径):
        # 现在深色 → 字写「浅色」。这一条一开始我写反了,红的是断言,不是界面。
        check(cap("btnTheme") == ("浅色" if dark_after else "深色"),
              f"格子上写的仍是'下一步能切去的那一档':{cap('btnTheme')!r}")

        # ---- 13.5 🖥 终端 / ⏱ 自动化 / ◀ 收起侧栏 ----
        # v0.9.162:btnTerminal 指向**底部终端坞**(ZCode p4 同款,占内容区整行);
        # 底座四入口已从入口栏撤下,页面的家在右侧「切换面板」(顶栏 btnPanelToggle)
        # 与底座自己的标签条 —— 那两块的深度回归由 v09159/v09160/v09161 三场管。
        page.click("#btnTerminal")
        page.wait_for_function(
            "() => document.querySelector('.term-dock .t-cmd') !== null")
        check(page.evaluate("() => !document.getElementById('termDock').hidden"),
              "🖥 那一点拉开底部终端坞(v0.9.162)")
        check(len(calls("get_terminal")) == 1,
              "终端坞的账是问本机要的,不是前端自己攒一份")
        page.click("#tdClose")
        page.wait_for_timeout(150)
        check(page.evaluate("() => document.getElementById('termDock').hidden"),
              "✕ 收得回坞")
        page.click("#btnAutomate")
        page.wait_for_timeout(250)
        check(page.evaluate("() => document.getElementById('autoOverlay')"
                            ".classList.contains('show')"),
              "⏱ 那一点真的打开自动化面板")
        check(len(calls("get_automation")) == 1, "定时任务的清单来自 jobs.facts()")
        page.click("#autoClose")
        closed_before = page.evaluate("() => document.querySelector('.app')"
                                      ".classList.contains('side-closed')")
        page.click("#btnSideToggle")
        page.wait_for_timeout(250)
        check(page.evaluate("() => document.querySelector('.app')"
                            ".classList.contains('side-closed')") is not closed_before,
              "◀ 那一点真的把侧栏收/放了一次")
        check(page.evaluate("() => localStorage.getItem('gcode-side')") is not None,
              "收起这件事写到本机偏好里(下次开机还记得)")
        page.click("#btnSideToggle")            # 放回去,后面那一节要看全部格子
        page.wait_for_timeout(200)

        # ---- 14. 矮窗口里每一颗仍全部够得到(入口看不见 = 功能没了) ----
        page.set_viewport_size({"width": 1280, "height": 480})
        page.wait_for_timeout(250)
        reach = page.evaluate("""() => {
            const rail = document.querySelector('.rail');
            const rb = rail.getBoundingClientRect();
            const out = [];
            for (const b of rail.querySelectorAll('button')) {
                if (b.style.display === 'none') continue;
                b.scrollIntoView({block: 'center'});
                const r = b.getBoundingClientRect();
                if (r.top < rb.top - 1 || r.bottom > rb.bottom + 1) out.push(b.id);
            }
            return {scrollable: rail.scrollHeight > rail.clientHeight, gone: out};
        }""")
        check(not reach["gone"], f"矮窗口(480 高)里每一颗都能滚到(够不着的:{reach['gone']})")
        check(reach["scrollable"], "480 高时入口栏确实要滚(说明 overflow 那条在生效)")
        page.set_viewport_size({"width": 1280, "height": 900})

        # ---- 15. 这台没沙箱:那颗整颗不出现,而不是亮着一颗死开关 ----
        page2 = browser.new_page(viewport={"width": 1280, "height": 900})
        page2.on("pageerror", lambda e: errors.append(f"pageerror(2): {e}"))
        page2.add_init_script(stub + "\n" + STUB_NO_SANDBOX)
        page2.goto(url, wait_until="load")
        page2.wait_for_function("() => document.getElementById('btnSandbox') !== null")
        page2.wait_for_timeout(400)
        check(page2.evaluate("() => getComputedStyle(document.getElementById('btnSandbox'))"
                             ".display") == "none",
              "后端说没有沙箱 → 那一格整颗藏起来(不留死开关)")
        check(page2.evaluate(
            "() => document.querySelector('#btnMemory .cap').textContent.trim()") == "记忆 7",
            "另一场开机也一样把条数写在格子上(不是只有第一场那样)")
        page2.close()

        browser.close()

    shutdown()
    bad_http = [e for e in errors if e.startswith("http ")]
    check(all("favicon" in e for e in bad_http),
          f"除 favicon 外没有丢资源(拿到 {len(bad_http)} 条):{bad_http[:3]}")
    real = [e for e in errors if "favicon" not in e and "Failed to load resource" not in e]
    check(not real, f"整场点完每一颗,控制台没有一条报错:{real[:3]}")

    print()
    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n{len(failures)} 项不通过")
        return 1
    print("PASS: 14 个入口搬进左栏后,每一颗仍点到原来那个面板/原来那个桥方法")
    return 0


if __name__ == "__main__":
    sys.exit(main())
