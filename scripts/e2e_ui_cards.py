"""v0.9.185 前端回归:流内提问卡 + 交互式可视化三张卡。

stub 掉 pywebview 桥,按后端真实事件形状推:
  ask_request  → 流内 .askc;点选项/翻页/发送 → ask_reply payload
  ask_trace    → 卡片收成"已结束",顶部 askbar 收起
  ui_widget    → .wcard(图表/表格/清单),各自交互
  renderHistory → 从 ui_calls 把提问卡和图表画回来

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_ui_cards.py
退出码 0 = 通过;1 = 有断言失败。
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
OUT_DIR = ROOT / "_tmp_dev_check" / "ui-cards"


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/index.html", httpd.shutdown


INFO = {
    "version": "0.9.185", "workspace": "D:/e2e", "permission_mode": "confirm",
    "session_key": "s1", "model": "qwen3.8-27b", "active_provider": "gzasc",
    "providers": [{"id": "gzasc", "name": "广州应用科技学院",
                   "model": "qwen3.8-27b"}],
    "campus_map": None, "memory_count": 0, "memory_user_count": 0,
    "sandbox": {"enabled": False}, "vision": False,
    "library_health": {"status": "ok", "notice": ""},
}
INFO["api_" + "key" + "_configured"] = True

METRICS = {
    "ok": True, "provider": "gzasc", "model": "qwen3.8-27b", "builtin": True,
    "capabilities": ["文本"], "context_window": 131072,
    "usage": {"prompt": 0, "completion": 0, "total": 0, "requests": 0},
    "context_estimate": 0, "selectable": [],
}


def _stub_js() -> str:
    return f"""
window.__E2E_CALLS__ = [];
window.pywebview = {{
  api: {{
    get_info: () => Promise.resolve({json.dumps(INFO, ensure_ascii=False)}),
    get_asset: () => Promise.resolve({{ok: false}}),
    refresh_sessions: () => Promise.resolve({json.dumps({"ok": True, "items": []})}),
    get_suggestions: () => Promise.resolve({json.dumps({"items": []})}),
    get_session_metrics: () => Promise.resolve({json.dumps(METRICS, ensure_ascii=False)}),
    set_session_model: () => Promise.resolve({json.dumps({"ok": True})}),
    confirm_reply: () => Promise.resolve({json.dumps({"ok": True})}),
    delete_session: () => Promise.resolve({json.dumps({"ok": True})}),
    pick_attachments: () => Promise.resolve({json.dumps({"ok": False})}),
    ask_reply: (payload) => {{
      window.__E2E_CALLS__.push(["ask_reply", String(payload)]);
      return Promise.resolve({json.dumps({"ok": True})});
    }},
    plan_review_reply: (payload) => {{
      window.__E2E_CALLS__.push(["plan_review_reply", String(payload)]);
      return Promise.resolve({json.dumps({"ok": True})});
    }},
    widget_action: (id, action, payload) => {{
      window.__E2E_CALLS__.push(["widget_action", String(id), String(action), String(payload)]);
      return Promise.resolve({{ok: true,
        text: "我在「本周待办」里勾选了：找张三谈心、交材料。请按这个继续。"}});
    }},
  }},
}};
"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.add_init_script(_stub_js())
            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.getElementById('modelPill') "
                "&& document.getElementById('modelPill').textContent.includes('广州应用科技学院')")

            def ev(**kwargs):
                page.evaluate("(e) => window.__gcodeEvent(e)", kwargs)

            # ------------------------------------------------ 提问卡
            questions = [
                {"id": 0, "header": "网站类型", "q": "你想做一个什么类型的网站？",
                 "why": "先定方向，后面才排步骤。", "multi": False, "recommend": True,
                 "options": [
                     {"label": "个人主页 (推荐)", "note": "最快能看效果"},
                     {"label": "产品落地页", "note": "适合介绍功能"}]},
                {"id": 1, "header": "风格", "q": "视觉风格偏好？",
                 "why": "风格定了一次做完，省得返工。", "multi": False, "recommend": True,
                 "options": [
                     {"label": "深色科技感 (推荐)", "note": "暗色背景、渐变高亮"},
                     {"label": "简洁浅色", "note": "白底、大留白"}]},
            ]
            ev(type="ask_request", ask_id="a1", session="s1",
               questions=questions, note="定了再排步骤", label="",
               session_title="")
            page.wait_for_selector(".askc")
            page.wait_for_timeout(300)      # 等入场动画与滚动落定再截图
            check(page.locator(".askc").count() == 1, "ask_request 后应有 1 张流内卡片")
            check("需要输入" in page.locator(".askc").inner_text(), "卡片缺少「需要输入」")
            check(page.locator(".askbar").is_visible(), "顶部「在等你」条没出现")
            ph = page.get_attribute("#input", "placeholder") or ""
            check("先回答" in ph, f"输入框提示没让位给提问卡:{ph}")
            page.screenshot(path=str(OUT_DIR / "ask-card.png"))

            page.locator(".askc .ask-opt").nth(0).click()
            page.wait_for_timeout(350)      # 单选题选完会自动翻到下一题
            check("2 / 2" in page.locator(".askc .askc-prog").inner_text(),
                  "单选题选完没有自动翻页")
            page.locator(".askc .ask-opt").nth(0).click()
            page.locator(".askc-foot button", has_text="发送回复").click()
            page.wait_for_timeout(200)
            calls = page.evaluate("() => window.__E2E_CALLS__")
            ask_calls = [c for c in calls if c[0] == "ask_reply"]
            check(len(ask_calls) == 1, f"ask_reply 调用次数不对:{ask_calls}")
            if ask_calls:
                got = json.loads(ask_calls[0][1])
                check(got.get("mode") == "answer", f"mode 不是 answer:{got}")
                check(got.get("ask_id") == "a1", f"ask_id 没带回来:{got}")
                check([a.get("choice") for a in got.get("answers", [])]
                      == [["个人主页 (推荐)"], ["深色科技感 (推荐)"]],
                      f"选项没按原 label 回传:{got}")
            ev(type="ask_trace", kind="ask", ask_id="a1", session="s1",
               text="学长问的 2 题,你答了:「网站类型」→ 个人主页 (推荐);"
                    "「风格」→ 深色科技感 (推荐)")
            page.wait_for_timeout(200)
            check(page.locator(".askc .askc-done").count() == 1, "答完后卡片没收成已结束")
            check(not page.locator(".askbar").is_visible(), "答完后顶部等待条没收起")
            ph = page.get_attribute("#input", "placeholder") or ""
            check("描述任务" in ph, f"答完后输入框提示没恢复:{ph}")
            page.screenshot(path=str(OUT_DIR / "ask-card-done.png"))

            # ------------------------------------------------ 计划核对（流内卡）
            plan_steps = [
                {"n": 1, "title": "定题", "verify": ""},
                {"n": 2, "title": "写开题报告", "verify": "pytest -q"},
            ]
            ev(type="plan_review", ask_id="p1", session="s1",
               goal="毕设排期", steps=plan_steps, session_title="")
            page.wait_for_selector(".planc")
            page.wait_for_timeout(350)
            check(page.locator(".planc").count() == 1, "plan_review 后应有 1 张流内核对卡")
            check(page.locator("#planReviewOverlay").count() == 0, "旧核对单浮层应已删除")
            check(page.locator(".planc .pr-step").count() == 2, "核对卡应列出 2 步")
            check(page.locator(".askbar").is_visible(), "顶部「在等你批计划」条没出现")
            ph = page.get_attribute("#input", "placeholder") or ""
            check("先批一下" in ph, f"输入框提示没让位给核对卡:{ph}")
            page.screenshot(path=str(OUT_DIR / "plan-card.png"))

            # 出口一:改一条(点开就自动聚焦;回车保存)
            page.locator(".planc .pr-edit").nth(0).click()
            page.wait_for_timeout(120)
            check(page.evaluate(
                "() => document.activeElement && document.activeElement.classList.contains('pr-input')"),
                "「改这条」没有自动聚焦到输入框")
            page.locator(".planc .pr-input").fill("定题：教师访谈")
            page.locator(".planc .pr-input").press("Enter")
            page.wait_for_timeout(200)
            calls = page.evaluate("() => window.__E2E_CALLS__")
            pr_calls = [c for c in calls if c[0] == "plan_review_reply"]
            check(len(pr_calls) == 1, f"改一条没有回话:{pr_calls}")
            if pr_calls:
                got = json.loads(pr_calls[-1][1])
                check(got.get("mode") == "edit", f"mode 不是 edit:{got}")
                check(got.get("step") == 1, f"没带第几步:{got}")
                check("教师访谈" in got.get("text", ""), f"改的内容没带上:{got}")
                check(got.get("ask_id") == "p1", f"没带核对单 id:{got}")
            ev(type="ask_trace", kind="plan", ask_id="p1", mode="edit", session="s1",
               text="计划核对:你改了第 1 步,学长按改后的这一版走。")
            page.wait_for_timeout(200)
            check("改了第 1 步" in page.locator(".planc .askc-done").inner_text(),
                  "核对卡没显示改单结果")
            check(not page.locator(".askbar").is_visible(), "批完后顶部等待条没收起")
            # 结束的卡默认收成摘要;展开后才看得到改后的步骤标题
            first_plan = page.locator(".planc").first
            check("展开步骤" in first_plan.inner_text(), "结束的核对卡没有展开入口")
            first_plan.locator("button", has_text="展开步骤").click()
            page.wait_for_timeout(150)
            check("定题：教师访谈" in first_plan.inner_text(),
                  "展开后没显示改后的步骤标题")

            # 出口二:批准并开工
            ev(type="plan_review", ask_id="p2", session="s1",
               goal="毕设排期", steps=plan_steps, session_title="")
            page.wait_for_timeout(200)
            page.locator(".planc").last.locator("button", has_text="批准并开工").click()
            page.wait_for_timeout(200)
            calls = page.evaluate("() => window.__E2E_CALLS__")
            pr_calls = [c for c in calls if c[0] == "plan_review_reply"]
            got = json.loads(pr_calls[-1][1])
            check(got.get("mode") == "approve", f"批准出口不对:{got}")
            check(got.get("ask_id") == "p2", f"批准没带核对单 id:{got}")
            ev(type="ask_trace", kind="plan", ask_id="p2", mode="approve", session="s1",
               text="计划核对:你点了「批准」,学长按这张单子开工。")
            page.wait_for_timeout(200)
            check("批准" in page.locator(".planc").last.locator(".askc-done").inner_text(),
                  "核对卡没显示批准结果")

            # 出口三:稍后再说
            ev(type="plan_review", ask_id="p3", session="s1",
               goal="毕设排期", steps=plan_steps, session_title="")
            page.wait_for_timeout(200)
            page.locator(".planc").last.locator("button", has_text="稍后再说").click()
            page.wait_for_timeout(200)
            calls = page.evaluate("() => window.__E2E_CALLS__")
            pr_calls = [c for c in calls if c[0] == "plan_review_reply"]
            got = json.loads(pr_calls[-1][1])
            check(got.get("mode") == "unanswered", f"稍后出口不对:{got}")
            ev(type="ask_trace", kind="plan", ask_id="p3", mode="unanswered", session="s1",
               text="计划核对:没等到你的回答,学长不会擅自开工。")
            page.wait_for_timeout(200)
            last_plan = page.locator(".planc").last
            check("未确认" in last_plan.locator(".ask-tag").inner_text(),
                  "核对卡没标未确认")
            check(not page.locator(".askbar").is_visible(), "未确认后等待条没收起")
            page.screenshot(path=str(OUT_DIR / "plan-card-done.png"))

            # ------------------------------------------------ 图表
            ev(type="ui_widget", widget_id="w-chart", session="s1", widget={
                "kind": "chart", "title": "近四周待办完成数", "chart_type": "bar",
                "labels": ["第1周", "第2周", "第3周", "第4周"],
                "series": [{"name": "完成", "values": [8, 12, 9, 15]},
                           {"name": "新增", "values": [10, 9, 14, 11]}],
                "note": "演示数据"})
            page.wait_for_selector(".wcard")
            chart = page.locator(".wcard", has_text="近四周待办完成数")
            check(chart.locator(".wc-svg").count() == 1, "图表没画出 SVG")
            check(chart.locator(".wc-lg").count() == 2, "图例数量不对")
            chart.locator(".wc-lg").nth(0).click()
            page.wait_for_timeout(150)
            check(chart.locator(".wc-lg.off").count() == 1, "点图例后系列没藏起来")
            chart.locator(".wc-btn", has_text="看数据").click()
            page.wait_for_timeout(150)
            check(chart.locator(".wc-data").is_visible(), "「看数据」没展开")
            check(chart.locator(".wc-data tbody tr").count() == 4, "数据表行数不对")
            page.screenshot(path=str(OUT_DIR / "chart.png"))

            # ------------------------------------------------ 表格
            ev(type="ui_widget", widget_id="w-table", session="s1", widget={
                "kind": "table", "title": "学生名单",
                "columns": ["姓名", "班级", "积分"],
                "rows": [["张三", "计科1班", 90], ["李四", "计科2班", 85],
                         ["王五", "计科1班", 95]],
                "note": "演示数据"})
            page.wait_for_timeout(200)
            table = page.locator(".wcard", has_text="学生名单")
            check(table.locator(".wc-table tbody tr").count() == 3, "表格初始行数不对")
            table.locator(".wc-search").fill("计科1班")
            page.wait_for_timeout(150)
            check(table.locator(".wc-table tbody tr").count() == 2, "搜索没过滤")
            table.locator(".wc-search").fill("")
            page.wait_for_timeout(150)
            table.locator("th", has_text="积分").click()
            page.wait_for_timeout(30)
            anims = page.evaluate(
                "() => Array.from(document.querySelectorAll('.wc-table tbody tr'))"
                ".reduce((n, tr) => n + (tr.getAnimations ? tr.getAnimations().length : 0), 0)")
            check(anims > 0, "表格排序没有行过渡动画")
            page.wait_for_timeout(250)
            table.locator("th", has_text="积分").click()
            page.wait_for_timeout(250)
            first = table.locator(".wc-table tbody tr").nth(0).locator("td").nth(0).inner_text()
            check(first == "王五", f"按积分降序后第一行应是王五,实际 {first}")
            page.screenshot(path=str(OUT_DIR / "table.png"))
            # 表格卡也能放大;不重建内容,排序/搜索状态都还在
            table.locator(".wc-fs").click()
            page.wait_for_timeout(200)
            check(page.locator(".wcard.fullscreen").count() == 1,
                  "表格卡没有全屏")
            check(page.locator(".gen-fs-backdrop").count() == 1,
                  "表格全屏没有遮罩")
            check(table.locator(".wc-fs").inner_text() == "还原",
                  "表格放大按钮没变还原")
            check(table.locator(".wc-table tbody tr").count() == 3,
                  "全屏后表格内容没了")
            page.screenshot(path=str(OUT_DIR / "table-fullscreen.png"))
            table.locator(".wc-fs").click()
            page.wait_for_timeout(200)
            check(page.locator(".wcard.fullscreen").count() == 0,
                  "表格全屏没退出")

            # ------------------------------------------------ 清单
            ev(type="ui_widget", widget_id="w-list", session="s1", widget={
                "kind": "checklist", "title": "本周待办",
                "items": [{"label": "找张三谈心", "note": "周三前"},
                          {"label": "交材料", "note": "周五前"},
                          {"label": "开班会", "note": "周四"}],
                "note": "从课表与提醒里整理"})
            page.wait_for_timeout(200)
            todo = page.locator(".wcard", has_text="本周待办")
            todo.locator(".wc-item").nth(0).click()
            todo.locator(".wc-item").nth(1).click()
            check(todo.locator(".wc-item.on").count() == 2, "清单勾选状态不对")
            todo.locator(".wc-btn", has_text="把选中项发给学长").click()
            page.wait_for_timeout(250)
            calls = page.evaluate("() => window.__E2E_CALLS__")
            wcalls = [c for c in calls if c[0] == "widget_action"]
            check(len(wcalls) == 1, f"widget_action 调用次数不对:{wcalls}")
            if wcalls:
                got = json.loads(wcalls[0][3])
                check(got.get("title") == "本周待办", f"清单标题没带回去:{got}")
                check(got.get("selected") == ["找张三谈心", "交材料"],
                      f"勾选项没按顺序发回:{got}")
            check("勾选了" in page.locator("#chat").inner_text(), "发回后没出现用户消息")
            check(todo.locator(".wc-btn", has_text="已发送").count() == 1,
                  "清单发完后按钮没变成已发送")
            page.screenshot(path=str(OUT_DIR / "checklist.png"))

            # ------------------------------------------------ 历史回放
            history = [
                {"role": "user", "text": "帮我做个网站"},
                {"role": "assistant", "text": "先问一句", "tools": "ask_user",
                 "ui_calls": [{
                     "id": "h1", "name": "ask_user", "note": "", "label": "",
                     "questions": [{
                         "id": 0, "header": "网站类型", "q": "你想做一个什么类型的网站？",
                         "why": "", "multi": False, "recommend": True,
                         "options": [{"label": "个人主页 (推荐)", "note": ""},
                                     {"label": "产品落地页", "note": ""}]}]}]},
                {"role": "tool", "tool_call_id": "h1", "name": "ask_user",
                 "text": "学长问的 1 题,你答了:「网站类型」→ 个人主页 (推荐)"},
                {"role": "assistant", "text": "", "tools": "show_chart",
                 "ui_calls": [{
                     "id": "h2", "name": "show_chart",
                     "widget": {"kind": "chart", "title": "历史图表",
                                "chart_type": "line", "labels": ["A", "B"],
                                "series": [{"name": "s", "values": [1, 2]}],
                                "note": ""}}]},
                {"role": "tool", "tool_call_id": "h2", "name": "show_chart",
                 "text": "已展示图表"},
                {"role": "assistant", "text": "", "tools": "plan",
                 "ui_calls": [{
                     "id": "h3", "name": "plan", "goal": "历史计划",
                     "steps": [{"n": 1, "title": "读代码", "verify": ""},
                               {"n": 2, "title": "改导入", "verify": "pytest -q"}]}]},
                {"role": "tool", "tool_call_id": "h3", "name": "plan",
                 "text": "计划已写入 …… 用户已在界面上**批准这张计划**。"},
            ]
            page.evaluate("(msgs) => renderHistory(msgs)", history)
            page.wait_for_timeout(300)
            check(page.locator(".askc:not(.planc)").count() == 1,
                  "历史回放没有提问卡")
            check("个人主页" in page.locator(".askc:not(.planc)").inner_text(),
                  "历史提问卡没显示当时的回答")
            check(page.locator(".wcard", has_text="历史图表").count() == 1,
                  "历史回放没有图表")
            check(page.locator(".planc").count() == 1, "历史回放没有计划核对卡")
            check("历史计划" in page.locator(".planc").inner_text(),
                  "历史核对卡没显示计划目标")
            check("已处理" in page.locator(".planc .ask-tag").inner_text(),
                  "历史核对卡没从工具结果认出已批准")
            check("**" not in page.locator(".planc .askc-done").inner_text(),
                  "历史核对卡把 Markdown 星号原样露出来了")
            check(page.locator(".toolcard", has_text="工具结果").count() == 0,
                  "历史回放给 UI 工具多画了通用工具卡")
            page.screenshot(path=str(OUT_DIR / "history.png"))

            browser.close()
    finally:
        shutdown()

    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n截图证据留在 {OUT_DIR}")
        return 1
    print(f"提问卡/计划核对/图表/表格/清单/历史回放 全部通过;证据在 {OUT_DIR}")
    print("\n=== UI CARDS E2E PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
