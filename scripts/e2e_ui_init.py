# -*- coding: utf-8 -*-
"""开机冒烟:窗口打开后 boot() 必须**跑完整**,不是"没白屏就算好"。

为什么单独要这一支:v0.9.22 起 renderTaskMetrics 里写错一个变量名(用了没声明过的
`canVisionToggle`),它在 boot() 中间抛 ReferenceError,被最外层 .catch 接住后只在
输入框下面留一行小字「界面初始化失败:...」。窗口照样开、聊天照样能用,但它后面
的六件事全没做 —— 其中一件就是 `CampusMap.setData(info.campus_map)`,于是免安装版
里地图面板是空的(用户看到的正是这个)。已有的 e2e_ui_v09xx 系列没有一个去碰
hint 和地图,所以这条 bug 一路绿着发到了 Release 里。

这一支的口径:
1. **payload 出自真后端** —— campus_map 走 `campus.frontend_payload()`,
   session_metrics 走 `model_catalog.session_metrics()`(拿真 Bridge 算),
   不是我手抄一份"我以为的形状";抄的夹具只会一直绿。
2. 断言初始化没报错 **且** 报错之后的每一步都真的执行到了(桥上有调用记录)。
3. 断言地图拿到数据后画得出来(#mapStage 里有 svg、图例徽章数对得上建筑类别),
   并且点顶栏「地图」能开面板 —— 这是用户判断"地图能用"的唯一依据。
4. v0.9.32 起还盯**情绪面板**:`mood_review` / `mood_help` 的 payload 由**真 bridge**
   现算(先把 `paths.APP_DIR` 指到临时目录再塞三条记录 —— 否则会去读写开发者自己的
   `mood.json`)。断言的是"界面摆的就是后端算的":五档标签来自 `SCORE_LABELS`、
   `#moBody` 逐字等于 `weekly_review` 的文本、保存必须叫到桥上的 `mood_log`、
   求助文本里既有 `HELP_FACTS`(地址)又没有**给模型的转述规矩**,而号码**只允许**是学校
   心理中心公众号原文里那几个(白名单同 `tests/test_mood.py`),而「删除全部」第一次点
   **不得**叫 `mood_clear`。
   (往 STUB_JS 塞 JSON 记得写 `() => (@@X@@)`:箭头函数体以 `{` 开头会被当代码块。)
5. v0.9.33 起还盯**对话流里的计划卡片**:状态由真 plan 工具改出来的 `plan_changed`
   payload 驱动。断言四件事 —— 开机时**不补画**(上一轮没标完的计划不该在新会话
   开头转圈)、来一次真事件才画得出三行清单且「正在做第 N 步」写进脚注、
   **别的会话**推的计划不会往这段对话里插卡片、一轮结束(`setBusy(false)`)还挂着
   「进行中」的那一步会停转并被点名。这些都是"界面上看着能用"和"真能用"的分界。

跑法(项目根目录,需要 PYTHONIOENCODING=utf-8 否则中文断言在 GBK 控制台炸):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_init.py
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


# --------------------------------------------------------------- 真后端 payload

def build_info() -> dict:
    """get_info 的那一坨字段:地图与任务面板两块都由真后端算,别的手抄。"""
    from minicode import campus
    from minicode.config import Config, Provider
    from minicode.gui_bridge import create_app
    from minicode.model_catalog import session_metrics
    from minicode import paths
    from tests.test_gui_bridge import FakeLLM, FakeWindow

    tmp = Path(tempfile.mkdtemp(prefix="yx-init-"))
    cfg = Config(providers=[Provider(id="test", name="Test",
                                      base_url="https://example.invalid/v1",
                                      api_key="test-key", model="test-model")],
                 active_provider_id="test")
    bridge, ui, _agent, _session = create_app(
        cfg, tmp, llm=FakeLLM([]), sessions_dir=tmp / "sessions",
        config_path=tmp / "config.json")
    ui.attach(FakeWindow())
    metrics = session_metrics(bridge.agent, bridge.session, bridge.config,
                              bridge._current.llm_override)
    payload = campus.frontend_payload()
    assert payload["items"], "campus-index.json 里一个建筑都没有,这支测试就没意义了"

    # 情绪面板同理:回顾文本与标签由真 bridge.mood_review() 现算(往临时数据目录
    # 里塞三天记录),求助文本来自真 bridge.mood_help()。手抄一份"我以为的形状"
    # 只会让这支测试一直绿着,而面板里其实什么都渲染不出来。
    global MOOD, HELP
    from datetime import date, timedelta
    from minicode import mood as _mood
    paths.APP_DIR = tmp / "app"
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today()
    _mood.save_entries([
        {"date": (today - timedelta(days=d)).isoformat(), "score": s, "note": n,
         "created_at": f"{(today - timedelta(days=d)).isoformat()} 21:00"}
        for d, s, n in ((2, 4, "把答辩稿交上去了"),
                        (1, 3, "还行,就是没睡够"),
                        (0, 5, "跑完三公里,人活过来了"))
    ])
    MOOD = bridge.mood_review(7)
    HELP = bridge.mood_help()
    assert MOOD.get("ok") and MOOD.get("labels"), "后端 mood_review 没给出标签"
    assert MOOD.get("count") == 3, f"该回读到 3 天,实际 {MOOD.get('count')}"
    assert HELP.get("ok") and "博雅楼" in HELP.get("text", ""), "求助文本没读出来"

    # 计划卡片(v0.9.33):计划由**真 plan 工具**写进同一个工作区,状态也由它改,
    # 面板数据再走真 bridge.get_plan()。手抄一份 payload 的话,plan_state 少吐一个
    # running 字段这种 bug 就永远测不出来 —— 而卡片正是照那个字段画转圈的。
    global PLAN
    from minicode.planning import plan as _plan
    from minicode.tools.base import ToolContext
    global PCTX
    PCTX = ToolContext(workspace=tmp, config=cfg)
    _plan(PCTX, "write", goal="把答辩 PPT 做完", steps=[
        {"title": "梳理答辩逻辑"},
        {"title": "做幻灯片", "depends_on": [1]},
        {"title": "排练一遍计时", "depends_on": [2]},
    ])
    _plan(PCTX, "update", step=1, status="in_progress")
    PLAN = bridge.get_plan()
    assert PLAN.get("ok") and PLAN.get("has_plan"), "get_plan 没读到刚写的计划"
    assert PLAN["running"] == [1] and PLAN["total"] == 3, f"计划状态不对:{PLAN}"

    return {
        "version": "0.9.34", "app_name": "引路学长",
        "permission_mode": "confirm",
        "permission_modes": [{"value": "confirm", "label": "变更前确认"}],
        "session_key": "s1", "model": "test-model", "active_provider": "test",
        "providers": [{"id": "test", "name": "Test", "model": "test-model",
                       "has_key": True}],
        "api_key_configured": True, "workspace": str(tmp),
        "workspace_name": tmp.name, "workspace_is_default": True,
        "memory_count": 0, "memory_user_count": 0, "mcp_servers": [],
        "sandbox": {}, "vision": bool(metrics["vision"]),
        "campus_map": payload, "library_health": {}, "skills": [], "kb_count": 0,
        "session_metrics": metrics,
        "paths": {"dir": str(tmp / "app"), "writable": True, "files": []},
    }


INFO = build_info()
# 只声明不赋值:上面 build_info() 已经把真后端 payload 写进这两个名字了,
# 这里再 `= {}` 会把它自己清掉。
MOOD: dict      # 真 bridge.mood_review(7) 的返回
HELP: dict      # 真 bridge.mood_help() 的返回
PLAN: dict      # 真 bridge.get_plan() 的返回(计划已写到临时工作区)
# PCTX 同样只由 build_info() 赋值:这里再写一次 `PCTX = None` 就会把它清掉,
# plan_event() 会在"None 上没有 workspace"上炸(和上面那条是同一个坑)。


def plan_event(**updates) -> dict:
    """按真 plan 工具改状态,返回后端在 plan_changed 里推的那一坨。

    口径照 notify_plan:plan_state + dispatch_text + 会话键。
    在测试里手拼 steps 是最省事的,但那也正是"后端改了形状、测试还绿着"的来路。
    """
    from minicode.planning import dispatch_text, plan as _plan, plan_state
    for step, status in updates.items():
        _plan(PCTX, "update", step=int(step), status=status)
    st = plan_state(PCTX.workspace)
    return {"type": "plan_changed", "session": "s1",
            "dispatch_text": dispatch_text(st), **st}
# boot() 里 renderTaskMetrics **之后**必须发生的调用;少一个就是初始化半路死了
AFTER_METRICS = ["refresh_sessions", "get_suggestions", "get_recent_projects",
                 "get_reasoning", "get_plan"]

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__INFO__ = @@INFO@@;
window.__AFTER__ = [];
const __RET__ = {
  // 真后端算的那份计划:第 1 步还挂着「进行中」——正是"上一轮没标完就关窗"的现场
  get_plan: () => (@@PLAN@@),
  get_recent_projects: () => ({ok: true, current: {}, default: {}, recent: []}),
  get_reasoning: () => ({visible: false, options: [], choice: "default"}),
  get_suggestions: () => ({suggestions: []}),
  get_countdowns: () => ({ok: true, countdowns: []}),
  get_schedule_view: () => ({ok: true, schedule: null}),
  get_session_metrics: () => ({ok: false}),
  refresh_sessions: () => ({ok: true}),
  mood_review: () => (@@MOOD@@),
  mood_help: () => (@@HELP@@),
};
const __API__ = {
  get_info: () => Promise.resolve(window.__INFO__),
  open_url: () => Promise.resolve({ok: true}),
  send_task: () => Promise.resolve({ok: true, session: "s1"}),
  set_notice_watch: () => Promise.resolve({ok: true}),
  rate_message: () => Promise.resolve({ok: true}),
  compact_now: () => Promise.resolve({ok: true}),
};
// 没点名的方法一律记一笔再给 {ok:true}:桥面长了新方法不该把冒烟测试弄红,
// 但"被调过"这件事必须留在记录里,否则断言不到初始化走到了哪一步。
window.pywebview = {api: new Proxy(__API__, {
  get(target, name) {
    if (typeof name !== "string" || name === "then") return target[name];
    if (!(name in target)) {
      target[name] = (...args) => {
        window.__AFTER__.push(name);
        const r = (name in __RET__) ? __RET__[name](...args) : {ok: true};
        return Promise.resolve(r);
      };
    }
    return target[name];
  }
})};
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    port = None
    for candidate in range(18620, 18700):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError:
            continue
    assert port is not None, "18620-18699 里找不到可绑定的端口"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/index.html", httpd.shutdown


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

    stub = (STUB_JS.replace("@@INFO@@", json.dumps(INFO, ensure_ascii=False))
            .replace("@@MOOD@@", json.dumps(MOOD, ensure_ascii=False))
            .replace("@@HELP@@", json.dumps(HELP, ensure_ascii=False))
            .replace("@@PLAN@@", json.dumps(PLAN, ensure_ascii=False)))
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ✓ " if cond else "  ✗ ") + message)

    try:
        with sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(stub)
            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.getElementById('ver').textContent.length > 0")

            hint = page.locator("#hint").inner_text()
            check("界面初始化失败" not in hint,
                  f"boot() 没被异常打断(hint 现在写着:{hint[:60] or '空'})")
            check("当前模型服务不可用" not in hint, "Key 已配置时不报模型不可用")

            # ---- 任务面板:渲染器本身要出得来(它就是当年抛错的那一处) ----
            check(page.locator("#tmName").inner_text().strip() == "test-model",
                  "任务面板模型名来自真 session_metrics")
            check(page.locator("#tmCaps .cap").count() >= 1,
                  "能力徽章(多模态/纯文本 + 上下文窗口)渲染出来了")

            # ---- 抛错点之后的每一步都得跑到,不然只是"看起来没崩" ----
            called = page.evaluate("() => window.__AFTER__")
            missing = [m for m in AFTER_METRICS if m not in called]
            check(not missing,
                  f"初始化后半段全部执行(缺:{missing}) —— 地图就是这一段的受害者")

            # ---- 地图:数据真进了组件、画得出来、点顶栏能开(p3 的现场) ----
            check(page.locator("#mapStage svg").count() == 1,
                  "CampusMap.setData 收到了后端的 campus_map 并画出底图 SVG")
            cats = {it["category"] for it in INFO["campus_map"]["items"]}
            chips = page.locator("#mapLegend .lg-chip").count()
            check(chips == len(cats),
                  f"图例类别徽章 {chips} 个 = 后端数据里的 {len(cats)} 类建筑")
            page.click("#btnMap")
            page.wait_for_timeout(400)
            check(page.locator("#mapPanel.open").count() == 1,
                  "点顶栏「🗺 地图」面板滑出来了")
            check(page.locator("#mapTitle").inner_text().strip()
                  == "🗺 " + INFO["campus_map"]["title"],
                  "面板标题用的是后端给的校区名(换数据集时面板不会继续挂旧校区)")
            img = page.locator("#mapStage image.cm-map-img")
            check(img.count() == 1
                  and img.first.get_attribute("href").endswith(
                      INFO["campus_map"]["base_map"]),
                  "底图 <image> 指向后端算好的 base_map 路径")
            loaded = page.evaluate(
                "() => { const i = document.querySelector('#mapStage image.cm-map-img');"
                " return i ? (i.getBBox().width > 0) : false; }")
            check(loaded, "底图真的画进了 SVG 里(不是一片空白面板)")
            page.fill("#mapSearch", "图书馆")
            page.wait_for_timeout(250)
            check(page.locator("#mapList .ml-row").count() > 0
                  and "命中" in page.locator("#mapSearchInfo").inner_text(),
                  "地图里搜「图书馆」出得来候选列表(数据真的可用,不是空壳)")

            # ---- 情绪面板(v0.9.32):界面摆的必须是后端算出来的那几行 ----
            page.click("#btnMood")
            page.wait_for_timeout(350)
            check(page.locator("#moodOverlay.show").count() == 1,
                  "点顶栏「🌱 心情」面板打开了")
            chips = page.locator("#moScores .mo-sc")
            check(chips.count() == 6,
                  f"分数格子 {chips.count()} 个(五档 + 「只写一句话」)")
            label4 = str(MOOD["labels"]["4"])
            check(label4 in chips.nth(3).inner_text()
                  and chips.nth(3).get_attribute("title") == label4,
                  f"「4 分」这格的文案「{label4}」来自后端 SCORE_LABELS,不是 HTML 写死的")
            check(page.locator("#moBody").inner_text().strip() == MOOD["text"].strip(),
                  "回顾区显示的是后端 weekly_review 现算的那段文字")
            check("记了 3 天" in page.locator("#moMeta").inner_text(),
                  "#moMeta 的回读天数来自后端 count(3)")
            check("没睡够" in page.locator("#moBody").inner_text()
                  and "答辩稿" in page.locator("#moBody").inner_text(),
                  "你自己记的那几句话原样回读得出来(不是只报个平均分)")
            chips.nth(3).click()
            check(page.locator("#moScores .mo-sc.on").count() == 1,
                  "点一格分数会选中(再点取消,不会一屏全是选中态)")
            page.fill("#moNote", "e2e 冒烟:今晚睡得还行")
            page.click("#btnMoSave")
            page.wait_for_timeout(350)
            after2 = page.evaluate("() => window.__AFTER__")
            check("mood_log" in after2, "保存走的是桥上的 mood_log(不是前端自己拼个假成功)")
            check(page.locator("#moNote").input_value() == "",
                  "记完清空输入框,不把你刚写的那句留在原地")
            page.click("#btnMoHelp")
            page.wait_for_timeout(350)
            help_txt = page.locator("#moHelp").inner_text()
            check(page.locator("#moHelp").is_visible() and "博雅楼" in help_txt,
                  "求助去处显示的是后端 HELP_FACTS(地址那条来自学校公众号)")
            check("转述上面这段" not in help_txt,
                  "给模型看的转述规矩没有漏进给人看的界面")
            import re as _re
            shown = set(_re.findall(r"\d{7,}", help_txt))
            check(shown <= {"2610529", "2211222", "81899120"} and "0758-2610529" in help_txt,
                  "求助文本里的号码只可能是学校原文那几个(面板不显示没核实过的号)")
            before = page.evaluate("() => window.__AFTER__.filter(x => x === 'mood_clear').length")
            page.click("#btnMoClear")
            page.wait_for_timeout(250)
            check("再点一次" in page.locator("#btnMoClear").inner_text(),
                  "「删除全部」第一次点只是问一遍(按钮自己变成确认态)")
            check(page.evaluate("() => window.__AFTER__.filter(x => x === 'mood_clear').length")
                  == before, "第一次点确实没删:桥没被叫过")
            page.click("#btnMoClose")
            page.wait_for_timeout(250)
            check(page.locator("#moodOverlay.show").count() == 0,
                  "关闭面板收起弹窗(并清掉草稿与确认态)")

            # ---- 计划内联卡片(v0.9.33):对话流里那张清单是真在跟着状态动 ----
            check(page.locator(".tk").count() == 0,
                  "开机时不补画卡片:上一轮挂着的「进行中」不会在新窗口里转圈")

            def fire(ev):
                page.evaluate("(e) => window.__gcodeEvent(e)", ev)

            fire(plan_event())
            card = page.locator(".tk")
            check(card.count() == 1, "收到一次 plan_changed → 对话里出现一张计划卡片")
            check(page.locator(".tk-row").count() == 3,
                  "卡片里的行数 = 后端计划里的步数(3 步)")
            check("把答辩 PPT 做完" in page.locator(".tk-goal").inner_text(),
                  "卡片标题用的是后端给的计划目标")
            check(page.locator(".tk-row").nth(0).evaluate("el => el.className")
                  == "tk-row run", "第 1 步在数据里是 in_progress → 界面上那一行是「进行中」")
            spin = page.locator(".tk-row.run .tk-g").evaluate(
                "el => getComputedStyle(el).animationName")
            check(spin == "tkSpin", f"进行中的那一圈真在转(animation-name={spin})")
            foot = page.locator(".tk-foot").inner_text()
            check("步骤 0/3" in foot and "正在做第 1 步" in foot,
                  f"脚注报得清进度与正在做的步骤:{foot}")

            fire(plan_event(**{"1": "done", "2": "in_progress"}))
            check(page.locator(".tk").count() == 1,
                  "同一个计划原地更新,不会每更新一次就多贴一张")
            check(page.locator(".tk-row.done").count() == 1
                  and page.locator(".tk-row.run").count() == 1,
                  "第 1 步变成完成、第 2 步接上进行中")
            check("步骤 1/3" in page.locator(".tk-foot").inner_text(),
                  "脚注的已完成后跟着状态走")
            check(page.locator(".tk-bar i").evaluate(
                      "el => el.style.width") == "33%",
                  "进度条宽度按 1/3 算(不是画出来的假动画)")

            fire({**plan_event(**{"2": "failed"}), "session": "别的会话"})
            check(page.locator(".tk").count() == 1
                  and "步骤 1/3" in page.locator(".tk-foot").inner_text(),
                  "别的会话推的计划不往这段对话里插卡片(角标照常刷新,因为计划文件是工作区的)")

            fire(plan_event())          # 上一步已 failed:卡片要说明卡住了
            foot = page.locator(".tk-foot").inner_text()
            check(page.locator(".tk.broken").count() == 1
                  and "失败" in foot and "已被挡住" in foot,
                  f"上游失败:卡片变红并且说后面几步被挡住:{foot}")
            check(page.locator(".tk-row.blocked").count() == 1,
                  "被上游卡住的那一步在卡片里单独标出来(不与'待办'混成一类)")

            fire(plan_event(**{"2": "in_progress"}))
            page.evaluate("() => window.setBusy(false)")   # 这一轮结束了
            check(page.locator(".tk.idle").count() == 1,
                  "回合结束还有步骤挂着「进行中」→ 卡片进入'停下'态,不再假装在跑")
            check(page.locator(".tk-row.run .tk-g").evaluate(
                      "el => getComputedStyle(el).animationName") == "none",
                  "转圈真的停了(animation-name: none)")
            check("还挂着「进行中」" in page.locator(".tk-foot").inner_text(),
                  "脚注点名了是哪一步停在这儿")

            page.click(".tk-head")
            page.wait_for_timeout(350)
            check(page.locator("#planOverlay.show").count() == 1,
                  "点卡片标题打开顶栏那个完整计划面板(卡片是入口,不是又一份状态)")
            check(page.locator("#planOverlay .pl-step.s-run").count() == 1,
                  "面板与卡片口径一致:同一步在两边都显示为「进行中」")
            page.click("#btnPlanClose")
            page.wait_for_timeout(200)

            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"共 {len(failures)} 项失败:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("开机冒烟:初始化跑完整、地图拿得到数据、情绪面板摆的是后端算的那几行、"
          "计划卡片跟着真状态走 —— 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
