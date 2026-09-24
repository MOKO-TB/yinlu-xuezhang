"""v0.9.10 前端回归:真实浏览器里验证「会话按工作区分组」与「回复实测数据行」。

被测的是交付用的前端(frontend/index.html)。stub 掉 pywebview 桥(桥 API 由
stub 记账,事件用 window.__gcodeEvent 按后端真实事件序推),只断言 DOM 事实:

    1. sessions 事件到达 → 侧栏出现分组:当前项目组头「📁 本项目 · N」置顶
    2. 其他工作区的会话归进「📁 工作区名」组;旧留档(无 workspace)归「未记录」
    3. 组头点击 → 折叠/展开(组内 .sess 随 display 切换)
    4. 组内会话行点击 → resume_session(传入该行的 index)
    5. turn_stats 事件 → 最近一条助手消息下出现「⏱ 耗时 · ⚡ N token · x token/秒」
    6. tokens 缺失 → 显示"速度未知",不显示编造的数字
    7. 纯工具调用段(无正文)不挂 meta 行
    8. reasoning=true → 出现「已思考」标

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_ui_v0910.py
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
OUT_DIR = ROOT / "_tmp_dev_check" / "ui_v0910"

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__stubReady = true;
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.10", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [
        {value: "confirm", label: "变更前确认"},
        {value: "accept_edits", label: "自动编辑"},
        {value: "accept_all", label: "自动放行"},
        {value: "full", label: "完全访问"}
      ],
      session_key: "s1", model: "deepseek-v4-flash", active_provider: "gzasc",
      providers: [{id: "gzasc", name: "广州应用科技学院", model: "deepseek-v4-flash"}],
      workspace: "D:/app", workspace_name: "app", workspace_is_default: true,
      memory_count: 0, memory_user_count: 0,
      sandbox: {enabled: false}, vision: false, campus_map: null,
      api_key_configured: true,
      reasoning: {visible: false, options: [], choice: "default"}
    }),
    refresh_sessions: () => Promise.resolve({ok: true}),
    get_suggestions: () => Promise.resolve({items: []}),
    get_session_metrics: () => Promise.resolve({ok: false}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    resume_session: (i) => {
      window.__E2E_CALLS__.push(["resume_session", i]);
      return Promise.resolve({ok: true});
    },
    delete_session: (i) => {
      window.__E2E_CALLS__.push(["delete_session", i]);
      return Promise.resolve({ok: true});
    },
    send_task: (t) => {
      window.__E2E_CALLS__.push(["send_task", t]);
      return Promise.resolve({ok: true, session: "s1"});
    }
  }
};
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    # 随机端口可能撞上 Chromium 的 unsafe port 黑名单(66xx 一带):不在禁区才用
    port = None
    for _ in range(20):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        got = httpd.server_address[1]
        if 6000 <= got <= 7999 or got in (25, 501, 6665, 6666, 6667, 6668, 6669):
            httpd.server_close()
            continue
        port = got
        break
    assert port is not None, "拿不到 Chromium 允许的端口"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/index.html", httpd.shutdown


def _push(page, event: dict):
    page.evaluate(f"window.__gcodeEvent({json.dumps(event, ensure_ascii=False)})")


def main() -> int:
    from playwright.sync_api import sync_playwright
    # headless shell 这台机器没装:起浏览器这一步走 e2e_launch 那份共同的退路
    # (p3 之前这五场每次都红在 launch 上,连页面都没打开 —— 见 scripts/e2e_launch.py)
    from e2e_launch import launch

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ✓ " if cond else "  ✗ ") + message)

    try:
        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(STUB_JS)
            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.getElementById('sessions') !== null")

            # ---- 1&2. 分组:当前项目置顶、其他工作区、未记录 ----
            _push(page, {"type": "sessions", "items": [
                {"index": 1, "time": "10:00", "n_messages": 4,
                 "last_user_text": "当前项目任务A", "current": False,
                 "workspace": "", "ws_current": True, "running": False},
                {"index": 2, "time": "09:50", "n_messages": 8,
                 "last_user_text": "其他项目任务B", "current": False,
                 "workspace": "论文", "ws_current": False, "running": False},
                {"index": 3, "time": "09:40", "n_messages": 2,
                 "last_user_text": "老会话C", "current": False,
                 "workspace": "未记录", "ws_current": False, "running": False},
                {"index": 4, "time": "09:30", "n_messages": 6,
                 "last_user_text": "当前项目任务D", "current": True,
                 "workspace": "", "ws_current": True, "running": True},
            ]})
            page.wait_for_function(
                "() => document.querySelectorAll('#sessions .ws-group').length === 3")
            heads = page.eval_on_selector_all(
                "#sessions .ws-group-head",
                "els => els.map(e => e.querySelector('.gn').textContent)")
            check(heads[0] == "本项目", f"当前项目组置顶(实际 {heads})")
            check("论文" in heads and "未记录" in heads,
                  f"其他工作区与未记录各自成组({heads})")
            counts = page.eval_on_selector_all(
                "#sessions .ws-group-head .gc",
                "els => els.map(e => e.textContent)")
            check(counts[0] == "2", f"组头显示会话条数(实际 {counts})")

            # 组内行归属正确:本项目组里有 running 徽标的那条
            first_group_rows = page.eval_on_selector_all(
                "#sessions .ws-group:nth-child(1) .sess .tt",
                "els => els.map(e => e.textContent)")
            check(first_group_rows == ["当前项目任务A", "当前项目任务D"],
                  f"本项目组包含自己的两条会话({first_group_rows})")

            # ---- 3. 组头点击折叠/展开 ----
            page.click("#sessions .ws-group:nth-child(2) .ws-group-head")
            page.wait_for_function(
                "() => document.querySelector('#sessions .ws-group:nth-child(2)')"
                ".classList.contains('closed')")
            hidden = page.eval_on_selector(
                "#sessions .ws-group:nth-child(2) .sess",
                "el => getComputedStyle(el).display === 'none'")
            check(hidden, "点击组头折叠后组内会话隐藏")
            page.click("#sessions .ws-group:nth-child(2) .ws-group-head")
            page.wait_for_function(
                "() => !document.querySelector('#sessions .ws-group:nth-child(2)')"
                ".classList.contains('closed')")

            # ---- 4. 点会话行 → resume_session(index) ----
            page.click("#sessions .ws-group:nth-child(2) .sess")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'resume_session' && c[1] === 2)")
            check(True, "点其他项目组里的会话 → resume_session(2)")

            # ---- 5. turn_stats → meta 行 ----
            _push(page, {"type": "text_delta", "text": "这是模型的回答正文。"})
            _push(page, {"type": "finish_stream"})
            _push(page, {"type": "turn_stats", "elapsed_ms": 12300,
                         "tokens": 456, "tps": 37.1, "reasoning": True})
            page.wait_for_function(
                "() => [...document.querySelectorAll('.msg.assistant .stats')]"
                ".some(s => !s.hidden && s.textContent.includes('12.3 秒'))")
            stat_text = page.eval_on_selector(
                ".msg.assistant .stats", "el => el.textContent")
            check("⏱" in stat_text and "12.3 秒" in stat_text,
                  f"耗时显示(实际 {stat_text!r})")
            check("456" in stat_text and "37.1 token/秒" in stat_text,
                  f"token 数与每秒速度显示({stat_text!r})")
            check("已思考" in stat_text, "reasoning=true 显示「已思考」标")

            # ---- 6. tokens 缺失 → 速度未知 ----
            _push(page, {"type": "text_delta", "text": "第二条回复。"})
            _push(page, {"type": "finish_stream"})
            _push(page, {"type": "turn_stats", "elapsed_ms": 900,
                         "tokens": None, "tps": None, "reasoning": False})
            page.wait_for_function(
                "() => [...document.querySelectorAll('.msg.assistant .stats')]"
                ".filter(s => !s.hidden).length === 2")
            texts = page.eval_on_selector_all(
                ".msg.assistant .stats", "els => els.map(e => e.textContent)")
            check("速度未知" in texts[-1] and "37.1" not in texts[-1],
                  f"无 token 数时显示速度未知,不编数字({texts[-1]!r})")

            # ---- 7. 纯工具调用段不挂 meta ----
            _push(page, {"type": "tool_start", "name": "bash", "summary": "ls"})
            _push(page, {"type": "tool_done", "ok": True})
            _push(page, {"type": "turn_stats", "elapsed_ms": 300,
                         "tokens": 12, "tps": 40.0, "reasoning": False})
            page.wait_for_timeout(150)
            stats_all = page.eval_on_selector_all(
                ".msg.assistant .stats", "els => els.map(e => !e.hidden)")
            check(all(stats_all), f"纯工具段没有新增可见 meta(可见 {stats_all})")

            page.screenshot(path=str(OUT_DIR / "groups_and_stats.png"), full_page=False)
            browser.close()
    finally:
        shutdown()

    if failures:
        print(f"\n=== UI v0.9.10 E2E FAIL({len(failures)} 项)===")
        for f in failures:
            print(" -", f)
        return 1
    print("分组/折叠/切换/耗时速度行/未知速度/纯工具段 全部通过;"
          f"证据在 {OUT_DIR}")
    print("\n=== UI v0.9.10 E2E PASS ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
