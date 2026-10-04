# -*- coding: utf-8 -*-
"""并行子代理内部的每一步要看得见(v0.9.92,§6 #20 那条"静默但记账"的欠账)。

批量模式当年为了不让几路 `text_delta` 在同一个聊天区里穿插成乱码,把每路的
`tool_start`/`tool_done` 整个吞了 —— 副作用是"分别处理问题"变成了黑箱:用户只看到
一条 `task`,以及最后那份合并报告,中间谁先查了什么、哪一路挂了都不知道。

这一层最容易被做假的地方,下面逐条钉住:
1. 记的必须是**真实时刻**(与父调用同一张实时表),不能拿刷新那一刻凑;
2. 子步骤**不许掺进「执行工具 N 次」** —— 那个数的定义是"会话消息里的调用数",
   掺进来就等于两处真相(聊天说 1 次 task,栏上写着 7 次);
3. 挂不上的(父行都没开)就**什么都不记**,不凭空造一行;
4. 溢出只丢最老的并**如实报丢了几个**,不静默少列;
5. 子行**不可点**:那一步的原文在主会话里根本没有,给一个点开是空的行比不给更坏。
"""
from __future__ import annotations

import json
from pathlib import Path

from minicode import activity, gui_ui, toolsum
from minicode.subagents import _QuietUI, _TaggedUI
from minicode.tools.base import Tool

ROOT = Path(__file__).resolve().parent.parent


def _tool(name="grep", read_only=True):
    return Tool(name=name, description="", parameters={}, handler=lambda **k: "",
                read_only=read_only)


# ---------------------------------------------------------------- 实时表那侧


def test_children_hang_under_the_open_parent_row_with_real_times():
    ui = gui_ui.GuiUI()
    task = Tool(name="task", description="", parameters={}, handler=lambda **k: "")
    ui.tool_start(task, {"type": ["explore"], "task": ["看两处"]})
    ui.live_child_begin("第1路[explore]", "explore·grep", "上限")
    ui.live_child_end("第1路[explore]", ok=True)
    ui.tool_done(task, "报告")

    kids = ui.live_rows()[0]["children"]
    assert len(kids) == 1
    kid = kids[0]
    assert kid["name"] == "explore·grep" and kid["route"] == "第1路[explore]"
    assert kid["start"] and kid["end"] and kid["ok"] is True
    assert float(kid["end"]) >= float(kid["start"])       # 时刻是真的先后,不是凑的


def test_no_open_parent_means_nothing_recorded():
    """父行都没开着就没处挂 —— 凭空造一行"某步在跑"就是假数据。"""
    ui = gui_ui.GuiUI()
    ui.live_child_begin("第1路[explore]", "explore·grep", "x")
    assert ui.live_rows() == []


def test_child_cap_drops_oldest_and_says_how_many():
    ui = gui_ui.GuiUI()
    task = Tool(name="task", description="", parameters={}, handler=lambda **k: "")
    ui.tool_start(task, {"type": ["explore"], "task": ["看两处"]})
    for i in range(gui_ui.CHILD_CAP + 5):
        ui.live_child_begin("第1路[explore]", f"explore·grep{i}", "x")
        ui.live_child_end("第1路[explore]", ok=True)
    row = ui.live_rows()[0]
    assert len(row["children"]) == gui_ui.CHILD_CAP
    assert row["children_dropped"] == 5
    assert row["children"][0]["name"] == "explore·grep5"   # 丢的是最老的


def test_unknown_route_end_does_not_close_someone_elses_step():
    ui = gui_ui.GuiUI()
    task = Tool(name="task", description="", parameters={}, handler=lambda **k: "")
    ui.tool_start(task, {"type": ["explore"], "task": ["看两处"]})
    ui.live_child_begin("第1路[explore]", "explore·grep", "x")
    ui.live_child_end("第2路[implement]", ok=True)          # 没这一路开着
    assert ui.live_rows()[0]["children"][0]["end"] is None  # 不许把别人的行收掉


def test_live_now_drills_into_the_running_child():
    """「此刻」要写真正在跑的那一步,而不是"task 在跑"这句没用的话。"""
    ui = gui_ui.GuiUI()
    task = Tool(name="task", description="", parameters={}, handler=lambda **k: "")
    ui.tool_start(task, {"type": ["explore"], "task": ["看两处"]})
    ui.live_child_begin("第3路[review]", "review·read_file", "notes/a.md")
    now = ui.live_now()
    assert now["name"] == "review·read_file" and now["route"] == "第3路[review]"
    assert now["parent_tool"] == "task" and now["elapsed"] >= 0
    ui.live_child_end("第3路[review]", ok=True)
    assert ui.live_now()["name"] == "task"                  # 子步收完回到父步


def test_abandon_closes_open_children_too():
    """按了停止之后,挂着的子步不能还转圈(那说的是"还在跑"而它已经不跑了)。"""
    ui = gui_ui.GuiUI()
    task = Tool(name="task", description="", parameters={}, handler=lambda **k: "")
    ui.tool_start(task, {"type": ["explore"], "task": ["看两处"]})
    ui.live_child_begin("第1路[explore]", "explore·grep", "x")
    assert ui.live_abandon() == 2                          # 父行 + 那一个子步
    kid = ui.live_rows()[0]["children"][0]
    assert kid["ok"] is False and kid["abandoned"] is True


# ---------------------------------------------------------------- 静默 UI 的记账


def test_quiet_ui_accounts_without_writing_to_the_chat():
    recorded = []

    class Recorder:
        def live_child_begin(self, route, name, summary):
            recorded.append(("b", route, name, summary))

        def live_child_end(self, route, ok):
            recorded.append(("e", route, ok))

        def live_child_progress(self, route, text):
            recorded.append(("p", route, text))

    class Inner:                          # 聊天区那一侧:一个字都不许到
        def __init__(self):
            self.hits = []

        def __getattr__(self, name):
            self.hits.append(name)
            return lambda *a, **k: None

    inner = Inner()
    quiet = _QuietUI(inner, route="第2路[implement]", account=Recorder())
    quiet.tool_start(_tool("grep"), {"pattern": "上限"})
    quiet.tool_progress("已跑 20 秒")
    quiet.tool_done(_tool("grep"), "错误: 没找到")
    assert [r[0] for r in recorded] == ["b", "p", "e"]
    assert recorded[0][1] == "第2路[implement]" and recorded[0][2] == "grep"
    assert recorded[2] == ("e", "第2路[implement]", False)   # 失败如实收成 False
    assert inner.hits == [], "静默那一路不该再往聊天区写任何东西"


def test_quiet_ui_without_a_recorder_is_still_quiet_and_does_not_crash():
    """终端形态没有实时表:没有通道就什么都不做,不硬造一份没人看的账。"""
    quiet = _QuietUI(object(), route="第1路[explore]", account=None)
    assert quiet.tool_start(_tool("grep"), {}) is None
    assert quiet.tool_done(_tool("grep"), "ok") is None


def test_tagged_ui_still_labels_confirms_and_passes_accounting_through():
    class Inner:
        def confirm_tool(self, tool, args, reason="", note="", label=""):
            Inner.label = label
            return "yes"

    q = _QuietUI(Inner(), route="第1路[implement]", account=None)
    tagged = _TaggedUI(q, "第1路[implement] 改两个文件")
    assert tagged.confirm_tool(_tool("edit_file"), {}, label="写 notes/a.md") == "yes"
    assert Inner.label.startswith("第1路[implement] 改两个文件")


# ---------------------------------------------------------------- activity 那侧


def _live_task(args, kids, dropped=0):
    summary = toolsum.summarize("task", args)
    row = {"name": "task", "summary": summary, "start": 100.0, "end": 103.0,
           "ok": True, "children": kids, "route": ""}
    if dropped:
        row["children_dropped"] = dropped
    return [row]


def _messages_with_task(args):
    return [
        {"role": "user", "content": "并行看两处"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "task",
                                      "arguments": json.dumps(args, ensure_ascii=False)}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "[explore子代理完成]\n报告正文"},
    ]


ARGS = {"type": ["explore", "review"], "task": ["看 a", "看 b"]}


def test_timeline_attaches_children_under_the_task_call():
    kids = [
        {"name": "explore·grep", "summary": "上限", "route": "第1路[explore]",
         "start": 100.4, "end": 101.4, "ok": True, "progress": ""},
        {"name": "review·read_file", "summary": "notes/a.md", "route": "第2路[review]",
         "start": 101.0, "end": 102.0, "ok": False, "progress": ""},
        {"name": "explore·bash", "summary": "pytest -q", "route": "第1路[explore]",
         "start": 102.0, "end": None, "ok": None, "progress": "已跑 20 秒"},
    ]
    tl = activity.timeline(_messages_with_task(ARGS),
                           live=_live_task(ARGS, kids), busy=True)
    task_row = tl["turns"][0]["items"][0]
    assert task_row["tool"] == "task" and task_row["start"] == 100.0
    got = task_row["children"]
    assert [c["title"] for c in got] == ["上限", "notes/a.md", "pytest -q"]
    assert [c["status"] for c in got] == ["ok", "failed", "running"]
    assert got[0]["route"] == "第1路[explore]"
    assert got[0]["dur_text"] == "1s" and got[2]["dur_text"] == ""
    assert got[2]["progress"] == "已跑 20 秒"
    assert got[1]["badge"] == "失败"


def test_children_never_inflate_the_call_counts():
    """「执行工具 N 次」的定义是会话消息里的调用数 —— 掺进子步就成两个真相。"""
    kids = [{"name": "explore·grep", "summary": "x", "route": "第1路[explore]",
             "start": 100.4, "end": 101.4, "ok": True, "progress": ""}]
    tl = activity.timeline(_messages_with_task(ARGS), live=_live_task(ARGS, kids),
                           busy=False)
    turn = tl["turns"][0]
    assert turn["calls"] == 1 and tl["calls"] == 1
    assert turn["errors"] == 0                  # 子步失败不算父调用的失败
    assert turn["items"][0]["children"][0]["status"] == "ok"


def test_unpaired_rows_get_an_empty_children_list():
    """没配到实时表(重启后的旧留档)也要有 children 这个键:形状稳定,前端不判空。"""
    tl = activity.timeline(_messages_with_task(ARGS), live=None, busy=False)
    row = tl["turns"][0]["items"][0]
    assert row["children"] == [] and row["children_dropped"] == 0


def test_dropped_children_are_reported_not_swallowed():
    kids = [{"name": "explore·grep", "summary": "x", "route": "第1路[explore]",
             "start": 1.0, "end": 2.0, "ok": True, "progress": ""}]
    tl = activity.timeline(_messages_with_task(ARGS),
                           live=_live_task(ARGS, kids, dropped=7), busy=False)
    row = tl["turns"][0]["items"][0]
    assert row["children_dropped"] == 7 and len(row["children"]) == 1


def test_now_line_carries_the_route_and_the_parent_step():
    got = activity.now_line({"name": "explore·bash", "summary": "pytest -q",
                             "start": 5, "elapsed": 20, "route": "第1路[explore]",
                             "parent_tool": "task", "progress": "已跑 20 秒"})
    assert got["route"] == "第1路[explore]" and got["parent_tool"] == "task"
    assert got["progress"] == "已跑 20 秒" and got["agent_type"] == "explore"
    plain = activity.now_line({"name": "read_file", "summary": "a.md", "start": 5})
    assert plain["route"] == "" and plain["parent_tool"] == ""


def test_session_activity_end_to_end_shows_children():
    """整条链(实时表 → session_activity → 面板)一起走一遍,不是只测零件。"""
    ui = gui_ui.GuiUI()
    task = Tool(name="task", description="", parameters={}, handler=lambda **k: "")
    args = {"type": ["explore"], "task": ["看一处"]}
    ui.tool_start(task, args)
    ui.live_child_begin("第1路[explore]", "explore·grep", "上限")
    data = activity.session_activity(_messages_with_task(args), workspace=ROOT,
                                     live=ui.live_rows(), now=ui.live_now(), busy=True)
    row = data["timeline"]["turns"][0]["items"][0]
    assert row["children"][0]["status"] == "running"
    assert data["now"]["route"] == "第1路[explore]"


# ---------------------------------------------------------------- 界面那侧


def _hd_js(marker: str, end_marker: str) -> str:
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    start = html.index(marker)
    return html[start:html.index(end_marker, start)]


def test_frontend_renders_children_and_never_makes_them_clickable():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "function actKidsEl(item)" in html
    assert "actKidsEl(item)" in _hd_js("for (const item of t.items || [])", "act-more")
    assert ".act-kids{" in html
    block = _hd_js("function actKidsEl(item)", "function actTlRowEl")
    assert 'classList.add("plain")' in block or 'add("plain")' in block
    # 子行**没有可点的原文**:不进 actRows 索引、不挂点击 —— 子代理内部
    # 步骤的老规矩。W8 起归类子行要带自己的 key(dataset.k):定位现场按
    # key 逐行点亮被归并走的原始调用 —— 带 key ≠ 可点,可点看的是 action。
    assert "actRows[" not in block, "子行不该进 actRows 索引(它没有可点的原文)"
    assert "addEventListener" not in block, "子行不挂点击:没有可点的原文"
    assert "dataset.k" in block, "W8 归类子行带 key,定位现场要点得亮"


def test_child_step_event_drives_a_refresh():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'e.type === "child_step"' in html
    assert 'if (now.route) bits.push' in html, "「此刻」没写这一步属于哪一路"


def test_frozen_needles_cover_the_accounting_channel():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_frozen_version import TARGETS
    for name in ("live_child_begin", "live_child_end", "live_child_progress", "CHILD_CAP"):
        assert name in TARGETS["minicode.gui_ui"], f"gui_ui 的 {name} 没进冻结针"
    assert "_children" in TARGETS["minicode.activity"]
