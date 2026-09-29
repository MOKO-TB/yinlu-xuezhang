# -*- coding: utf-8 -*-
"""v0.9.34 右侧「本次会话」活动面板:每一行都必须由一条真工具调用撑起来。

用户那条规矩(「是要真正能使用的不能只用于装饰」)在这一版里被翻成一个可测的口径:
**面板里出现的一行,会话消息里就得有一条对应的 tool_call;那一行的状态,得能从对应的
tool 结果里读出来**。所以这些测试全是照 `session.add_assistant` / `add_tool_result`
真实写出来的消息形状喂(assistant.tool_calls[i].function.arguments 是 **JSON 字符串**,
tool 消息只带 tool_call_id 不带工具名),而不是我凭空手写一份"字典长这样"的夹具。

四件最容易被画成装饰的事,各有一支测试钉住:
- **在磁盘上核一遍**:模型说写了 report.md 而文件其实不存在 → 必须报「已不在磁盘」,
  不许跟着显示"写入完成"(这是面板唯一能自证清白的地方);
- **失败就是失败**:判失败的前缀口径与 `gui_ui.GuiUI.tool_done` 必须一致,
  否则聊天里红着、面板上却绿着;
- **没做过就不许出现**:只读工具(read_file / plan)一行都不该进面板;
- **并行派发要出多行**:一次 `task` 带 type/task 数组 = N 个子代理,合成一行等于
  用户问"这四个各查到什么"时面板答不上来。
"""
from __future__ import annotations

import json
from pathlib import Path

from minicode import activity, provenance
from minicode.session import Session
from minicode.llm import AssistantTurn, ToolCall


def _assistant(calls):
    return AssistantTurn(text="", tool_calls=[
        ToolCall(id=c["id"], name=c["name"], arguments=json.dumps(c["args"]))
        for c in calls])


def _session(tmp_path, steps):
    """steps 里一项 = 一次(或一批)调用 + 它们的回话。None 表示还没回(在跑)。"""
    s = Session("sys", log_dir=None, workspace=Path(tmp_path))
    for calls in steps:
        s.add_user("做点事")
        s.add_assistant(_assistant(calls))
        for c in calls:
            if c.get("result") is not None:
                s.add_tool_result(c["id"], c["name"], c["result"])
    return s


def _by_key(act, key):
    return next(g for g in act["groups"] if g["key"] == key)


def _items(act, key):
    return _by_key(act, key)["items"]


def _one(act, key):
    rows = _items(act, key)
    assert len(rows) == 1, f"{key} 应该只有一行,实际 {[r['title'] for r in rows]}"
    return rows[0]


# ------------------------------------------------------------------ 结构不变式


def test_empty_session_yields_an_all_empty_payload(tmp_path):
    """新会话:五类都在(前端按 count 决定显不显示),但都不该有行,也不能抛。"""
    act = activity.session_activity([], workspace=tmp_path)
    assert act["ok"] and act["empty"]
    assert [g["key"] for g in act["groups"]] == \
        ["subagents", "files", "web", "sources", "memory"]
    assert all(g["count"] == 0 for g in act["groups"])
    assert act["summary"] == {"calls": 0, "errors": 0, "running": 0,
                              "interrupted": 0, "busy": True,
                              "workspace": str(tmp_path).replace("\\", "/")}
    # 时间线也要有结构(前端照它建段,缺键就是一次 undefined)
    assert act["timeline"]["turns"] == [] and act["timeline"]["calls"] == 0
    assert act["now"] is None and act["updated_at"]


def test_read_only_tools_are_not_categories_but_are_on_the_timeline(tmp_path):
    """读文件/看计划不是"产出":五类里一行都不许沾(否则面板全是噪声);
    但时间线**必须**看得见它们 —— 用户问的是"它先做了什么后做了什么",
    把 read_file 藏起来就是少报,而且那一行本来就点得开(看这一步的原文)。"""
    s = _session(tmp_path, [[
        {"id": "a", "name": "read_file", "args": {"path": "a.py"}, "result": "x"},
        {"id": "b", "name": "plan", "args": {"action": "read"}, "result": "计划…"},
        {"id": "c", "name": "glob", "args": {"pattern": "*.md"}, "result": "a.md"},
    ]])
    act = activity.session_activity(s.messages, workspace=tmp_path)
    assert all(g["count"] == 0 for g in act["groups"])   # 五类全空
    assert act["empty"] is False                         # 但这一栏有话说
    assert act["summary"]["calls"] == 3
    rows = [r for t in act["timeline"]["turns"] for r in t["items"]]
    assert [r["verb"] for r in rows] == ["读文件", "计划", "找文件"]


# ------------------------------------------------------------------ 产出:去磁盘核


def test_file_row_verifies_against_the_disk(tmp_path):
    """真写了文件:标题是工作区相对路径,副标题带真实大小与改动时间,状态"写入完成"。"""
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "report.md").write_text("# 报告\n内容", encoding="utf-8")
    s = _session(tmp_path, [
        [{"id": "w1", "name": "write_file", "args": {"path": "notes/report.md",
                                                     "content": "# 报告"},
          "result": "已写入 notes/report.md(2 行)"}],
    ])
    act = activity.session_activity(s.messages, workspace=tmp_path)
    row = _one(act, "files")
    assert row["title"] == "notes/report.md"
    assert row["status"] == "ok" and row["badge"] == "写入完成"
    assert "B · 改于" in row["sub"], row["sub"]      # 大小与时间是磁盘上读来的
    assert row["action"]["type"] == "reveal"
    assert row["action"]["path"] == "notes/report.md"


def test_missing_file_is_called_out_instead_of_trusting_the_model(tmp_path):
    """工具回话说写成了,磁盘上却没有 → 必须显示「已不在磁盘」且不给"打开文件夹"。"""
    s = _session(tmp_path, [
        [{"id": "w1", "name": "write_file", "args": {"path": "ghost.docx"},
          "result": "已生成 ghost.docx"}],
    ])
    row = _one(activity.session_activity(s.messages, workspace=tmp_path), "files")
    assert row["status"] == "missing" and row["badge"] == "已不在磁盘"
    assert row["action"] is None, "文件都没有还留一个能点的按钮 = 装饰"


def test_rejected_write_is_failed_not_ok(tmp_path):
    """用户点了拒绝:结果以「用户拒绝」开头 —— 与 tool_done 判失败同一口径。"""
    s = _session(tmp_path, [
        [{"id": "e1", "name": "edit_file", "args": {"path": "a.txt"},
          "result": "用户拒绝:不允许改这个文件"}],
    ])
    row = _one(activity.session_activity(s.messages, workspace=tmp_path), "files")
    assert row["status"] == "failed" and row["badge"] == "编辑失败"


def test_still_running_call_shows_running(tmp_path):
    """没有对应 tool 结果 = 这条调用还挂着,面板上就该转着,而不是提前报完成。"""
    s = _session(tmp_path, [
        [{"id": "w1", "name": "write_file", "args": {"path": "big.pdf"}}],
    ])
    act = activity.session_activity(s.messages, workspace=tmp_path)
    assert _one(act, "files")["status"] == "running"
    assert act["summary"]["running"] == 1


def test_same_path_multiple_times_stays_one_row(tmp_path):
    """同一路径改了三次 → 一行「编辑 / 写入 3 次」,状态跟最新那次(不是三行噪声)。"""
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    s = _session(tmp_path, [
        [{"id": "w1", "name": "write_file", "args": {"path": "a.md"}, "result": "已写入"}],
        [{"id": "w2", "name": "edit_file", "args": {"path": "a.md"}, "result": "已编辑"}],
        [{"id": "w3", "name": "edit_file", "args": {"path": "a.md"}, "result": "已编辑"}],
    ])
    act = activity.session_activity(s.messages, workspace=tmp_path)
    rows = _items(act, "files")
    assert len(rows) == 1 and rows[0]["badge"] == "编辑完成"
    assert "写入 / 编辑 3 次" in rows[0]["sub"], rows[0]["sub"]


# ------------------------------------------------------------------ 子代理


def test_parallel_dispatch_produces_one_row_per_subagent(tmp_path):
    """一次 task 带数组 = 并行 N 个子代理:必须 N 行,用户问的就是"各自查到什么"。"""
    s = _session(tmp_path, [
        [{"id": "t1", "name": "task",
          "args": {"type": ["explore", "review"], "task": ["查清登录流程", "检查权限门"]},
          "result": "## explore\n报告一\n\n## review\n报告二"}],
    ])
    rows = _items(activity.session_activity(s.messages, workspace=tmp_path), "subagents")
    assert [r["title"] for r in reversed(rows)] == ["查清登录流程", "检查权限门"]
    assert all(r["status"] == "ok" for r in rows)
    assert rows[0]["action"]["type"] == "detail"
    assert "报告一" in rows[0]["action"]["text"]      # 点进去看的是真回文


def test_running_subagent_has_no_fake_report(tmp_path):
    s = _session(tmp_path, [[{"id": "t1", "name": "task",
                              "args": {"type": "implement", "task": "写导出模块"}}]])
    row = _one(activity.session_activity(s.messages, workspace=tmp_path), "subagents")
    assert row["status"] == "running" and row["badge"] == "运行中"
    assert row["action"] is None


# ------------------------------------------------------------------ 网页与来源


def test_web_rows_open_a_real_url_or_fill_the_box(tmp_path):
    """有网址的行点开的是那个网址;只有关键词的行点一下是把话填回输入框,不做假链接。"""
    s = _session(tmp_path, [
        [{"id": "u1", "name": "browser_open", "args": {"url": "https://jwc.gzasc.edu.cn/x"},
          "result": "标题:教务处"},
         {"id": "u2", "name": "web_search", "args": {"query": "国家励志奖学金 条件"},
          "result": "1. [结果](https://a.example/1)"}],
    ])
    rows = {r["key"]: r for r in
            _items(activity.session_activity(s.messages, workspace=tmp_path), "web")}
    page = rows["w:browser_open:jwc.gzasc.edu.cn/x"]
    assert page["action"] == {"type": "open",
                               "url": "https://jwc.gzasc.edu.cn/x"}
    search = rows["w:web_search:国家励志奖学金 条件"]
    assert search["action"]["type"] == "fill"
    assert "国家励志奖学金 条件" in search["action"]["text"]
    assert "url" not in search["action"], "搜索结果里的链接不能冒充用户点开过"


def test_repeated_search_of_the_same_query_counts_up(tmp_path):
    s = _session(tmp_path, [
        [{"id": "u1", "name": "web_search", "args": {"query": "宿舍电费"},
          "result": "ok"}],
        [{"id": "u2", "name": "web_search", "args": {"query": "宿舍电费"},
          "result": "ok"}],
    ])
    rows = _items(activity.session_activity(s.messages, workspace=tmp_path), "web")
    assert len(rows) == 1 and rows[0]["sub"].endswith("×2")


def test_sources_come_from_fences_and_kb_lines_not_from_page_noise(tmp_path):
    """来源 = 进了上下文的外部内容(围栏头里的网址 + 知识条目里的「来源网址」)。

    围栏正文里那些页内链接**不算来源**:一个页面有几十个 a 标签,列出来等于噪声,
    而且会把"模型确实引用了这条"说成"它打开过这个网址"。
    """
    body = ("正文里也有链接 https://ads.example/spam,但它只是页面的一部分;"
            "参考链接 https://jwc.gzasc.edu.cn/tzgg/123.html")
    fence = provenance.wrap("web_crawl", {"url": "https://news.gzasc.edu.cn/a"}, body)
    kb = ("1. 国家励志奖学金申请条件\n    来源网址: https://jwc.gzasc.edu.cn/jl\n"
          "    来源渠道: 教务处(无网址)")
    s = _session(tmp_path, [
        [{"id": "u1", "name": "web_crawl", "args": {"url": "https://news.gzasc.edu.cn/a"},
          "result": fence}],
        [{"id": "u2", "name": "search_knowledge", "args": {"query": "励志奖学金"},
          "result": kb}],
    ])
    act = activity.session_activity(s.messages, workspace=tmp_path)
    urls = {r["action"]["url"] for r in _items(act, "sources")}
    assert urls == {"https://news.gzasc.edu.cn/a", "https://jwc.gzasc.edu.cn/jl"}
    assert "https://ads.example/spam" not in json.dumps(act, ensure_ascii=False)
    assert "https://jwc.gzasc.edu.cn/tzgg" not in json.dumps(act, ensure_ascii=False)


# ------------------------------------------------------------------ 记忆


def test_only_memory_writes_count_as_changes(tmp_path):
    """list/search 是查看,不该出现在「记忆已更新」里;save/forget/dream 才算。"""
    s = _session(tmp_path, [
        [{"id": "m1", "name": "memory", "args": {"action": "list"}, "result": "…"}],
        [{"id": "m2", "name": "memory",
          "args": {"action": "save", "content": "用户是肇庆校区大二", "scope": "user"},
          "result": "已记住"}],
    ])
    rows = _items(activity.session_activity(s.messages, workspace=tmp_path), "memory")
    assert len(rows) == 1
    assert rows[0]["title"] == "用户是肇庆校区大二"
    assert rows[0]["badge"] == "记住" and "用户级" in rows[0]["sub"]
    assert "已记住" in rows[0]["action"]["text"]      # 全文里带工具的回话


# ------------------------------------------------------------------ 顺序与上限


def test_newest_call_is_listed_first(tmp_path):
    s = _session(tmp_path, [
        [{"id": "w1", "name": "write_file", "args": {"path": "old.md"}, "result": "已写入"}],
        [{"id": "w2", "name": "write_file", "args": {"path": "new.md"}, "result": "已写入"}],
    ])
    assert [r["title"] for r in
            _items(activity.session_activity(s.messages, workspace=tmp_path), "files")] \
        == ["new.md", "old.md"]


def test_long_groups_are_capped_and_tell_how_many_are_hidden(tmp_path):
    steps = [[{"id": f"w{i}", "name": "web_search", "args": {"query": f"词{i}"},
               "result": "ok"}] for i in range(20)]
    act = activity.session_activity(_session(tmp_path, steps).messages,
                                    workspace=tmp_path)
    group = _by_key(act, "web")
    assert group["count"] == 20 and len(group["items"]) == activity.GROUP_LIMIT
    assert group["more"] == 20 - activity.GROUP_LIMIT


def test_broken_arguments_json_does_not_break_the_panel(tmp_path):
    """模型偶尔会吐出半截 JSON 参数:那一行丢掉可以,整个面板不能打不开。"""
    s = _session(tmp_path, [[{"id": "w1", "name": "write_file", "args": None,
                              "result": "已写入"}]])
    msg = s.messages[-2]
    msg["tool_calls"][0]["function"]["arguments"] = '{"path": "a.md"'   # 截断
    act = activity.session_activity(s.messages, workspace=tmp_path)
    assert act["ok"] and _by_key(act, "files")["count"] == 0


# ------------------------------------------------------------- 桥与门面(v0.9.34)


def _bridge(tmp_path):
    from minicode.config import Config, Provider
    from minicode.gui_bridge import create_app
    from tests.test_gui_bridge import FakeLLM, FakeWindow

    cfg = Config(providers=[Provider(id="t", name="T",
                                      base_url="https://example.invalid/v1",
                                      api_key="k", model="m")],
                 active_provider_id="t")
    bridge, ui, _agent, _session = create_app(
        cfg, tmp_path, llm=FakeLLM([]), sessions_dir=tmp_path / "sessions",
        config_path=tmp_path / "config.json")
    ui.attach(FakeWindow())
    return bridge


def test_get_activity_is_empty_on_a_fresh_session(tmp_path):
    """新会话没有"暂无"可摆:空是常态,前端据此整块不显示,所以这里只要求结构齐。"""
    act = _bridge(tmp_path).get_activity()
    assert act["ok"] is True and act["empty"] is True
    assert [g["key"] for g in act["groups"]] == \
        ["subagents", "files", "web", "sources", "memory"]


def test_get_activity_reads_the_real_session_messages(tmp_path):
    """桥上的数据 = 当前会话真写过的那一次(文件真在磁盘上,所以能报出大小)。"""
    b = _bridge(tmp_path)
    (tmp_path / "out.md").write_text("内容", encoding="utf-8")
    s = b.session
    s.add_user("生成一份文件")
    s.add_assistant(_assistant([{"id": "w1", "name": "write_file",
                                "args": {"path": "out.md", "content": "内容"}}]))
    s.add_tool_result("w1", "write_file", "已写入 out.md(1 行)")
    act = b.get_activity()
    row = _one(act, "files")
    assert row["title"] == "out.md" and row["status"] == "ok"
    assert act["summary"]["calls"] == 1


def test_get_activity_never_raises_even_on_garbage(tmp_path, monkeypatch):
    """聚合逻辑再坏也只该让面板空着,不该把一次刷新变成界面报错。"""
    b = _bridge(tmp_path)
    monkeypatch.setattr(activity, "session_activity",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("炸了")))
    act = b.get_activity()
    assert act["ok"] is False and "RuntimeError" in act["msg"] and act["empty"]


def test_reveal_file_refuses_missing_secret_and_blocked(tmp_path):
    """三类拒绝:文件没了 / 工作区里的凭据 / 敏感目录。都不该走到 explorer。"""
    b = _bridge(tmp_path)
    assert b.reveal_file("gone.md")["ok"] is False
    (tmp_path / ".env").write_text("TOKEN=1", encoding="utf-8")
    secret = b.reveal_file(".env")
    assert secret["ok"] is False and "凭据" in secret["msg"]
    ssh = Path.home() / ".ssh" / "id_ed25519"
    if ssh.exists():
        assert b.reveal_file(str(ssh))["ok"] is False
    assert b.reveal_file("")["ok"] is False


def test_public_api_forwards_the_activity_names():
    """js_api 门面是逐条手写的:忘了转发,前端点面板就是一次 undefined。"""
    import inspect

    from minicode.gui_bridge import PublicApi
    for name in ("get_activity", "reveal_file"):
        assert hasattr(PublicApi, name), name
    assert list(inspect.signature(PublicApi.reveal_file).parameters) == \
        ["self", "path"]
    assert list(inspect.signature(PublicApi.get_activity).parameters) == ["self"]


# ------------------------------------------- 时间线与实时表(v0.9.37 任务监控)


def _live(name, args, start, end=None, ok=None):
    """按 `toolsum.summarize` 造一条实时表记录 —— 配对靠的就是这同一句话。"""
    from minicode import toolsum
    return {"name": name, "summary": toolsum.summarize(name, args),
            "start": start, "end": end, "ok": ok}


def _turns(act):
    return act["timeline"]["turns"]


def _rows(act, turn_index=0):
    return _turns(act)[turn_index]["items"]


def test_timeline_reads_oldest_to_newest_within_a_turn(tmp_path):
    """时间线存在的理由就是"先做了什么、后做了什么":轮内必须按真实先后排。"""
    s = _session(tmp_path, [[
        {"id": "1", "name": "read_file", "args": {"path": "walk.py"}, "result": "ok"},
        {"id": "2", "name": "edit_file", "args": {"path": "walk.py"}, "result": "ok"},
        {"id": "3", "name": "bash", "args": {"command": "pytest"}, "result": "ok"},
    ]])
    act = activity.session_activity(s.messages, workspace=tmp_path)
    assert [r["verb"] for r in _rows(act)] == ["读文件", "改文件", "跑命令"]
    assert [r["summary"] for r in _rows(act)] == ["walk.py", "walk.py", "pytest"]


def test_newest_turn_sits_on_top_and_carries_the_user_question(tmp_path):
    """轮与轮之间最新在上,而且每一轮要写清是**哪句话**引出来的。"""
    s = Session("sys", log_dir=None, workspace=Path(tmp_path))
    for i, (name, args) in enumerate([("read_file", {"path": "a.py"}),
                                      ("web_search", {"query": "课表"})]):
        s.add_user(f"第二个问题 {i}")
        s.add_assistant(_assistant([{"id": f"c{i}", "name": name, "args": args}]))
        s.add_tool_result(f"c{i}", name, "结果")
    act = activity.session_activity(s.messages, workspace=tmp_path)
    turns = _turns(act)
    assert [t["n"] for t in turns] == [2, 1]
    assert turns[0]["title"].startswith("第二个问题 1")
    assert turns[0]["items"][0]["verb"] == "搜网页"


def test_turn_count_is_the_real_total_even_when_rows_are_capped(tmp_path):
    """列出来最多 12 行是刻意的,但轮次上的次数**不许因为截断变小**。"""
    calls = [{"id": str(i), "name": "read_file",
              "args": {"path": f"f{i}.py"}, "result": "x"} for i in range(15)]
    s = _session(tmp_path, [calls])
    act = activity.session_activity(s.messages, workspace=tmp_path)
    t = _turns(act)[0]
    assert t["calls"] == 15 and len(t["items"]) == 12 and t["more"] == 3
    assert act["timeline"]["calls"] == 15


def test_failed_running_and_interrupted_are_three_different_counts(tmp_path):
    """失败、在跑、被中断是三件事,混成一个数就等于把风险说小或把死人说活。"""
    s = _session(tmp_path, [[
        {"id": "a", "name": "read_file", "args": {"path": "a"}, "result": "x"},
        {"id": "b", "name": "bash", "args": {"command": "boom"}, "result": "错误: 失败"},
        {"id": "c", "name": "bash", "args": {"command": "hang"}},
    ]])
    t = _turns(activity.session_activity(s.messages, workspace=tmp_path))[0]
    assert (t["calls"], t["errors"], t["running"], t["stale"]) == (3, 1, 1, 0)
    assert t["items"][2]["badge"] == "进行中"
    # 回合已经结束(按了停止):没有回话的那一步不能再写"进行中"
    t2 = _turns(activity.session_activity(s.messages, workspace=tmp_path,
                                          busy=False))[0]
    assert (t2["running"], t2["stale"]) == (0, 1)
    assert t2["items"][2]["badge"] == "已中断"
    assert t2["items"][2]["status"] == "stale"


def test_live_times_pair_onto_rows_and_unmatched_rows_stay_silent(tmp_path):
    """v0.9.66 口径:实时表优先;没配上的行用消息 ts 还原;无 ts 的旧留档留空。"""
    s = _session(tmp_path, [[
        {"id": "a", "name": "read_file", "args": {"path": "a.py"}, "result": "x"},
        {"id": "b", "name": "bash", "args": {"command": "pytest -q"}, "result": "y"},
    ]])
    live = [_live("read_file", {"path": "a.py"}, 1_700_000_000, 1_700_000_007, True)]
    act = activity.session_activity(s.messages, workspace=tmp_path, live=live)
    rows = _rows(act)
    assert rows[0]["time"] and rows[0]["dur_text"] == "7s"
    # 实时表只记了第一行:第二行由消息 ts 还原(不再是"宁空不编"的老规则)
    assert rows[1]["time"], "有 ts 的消息重启后也还原得出时刻"
    # 完全没实时表:新留档每行都有 ts 还原出的时刻
    plain = _rows(activity.session_activity(s.messages, workspace=tmp_path))
    assert all(r["time"] for r in plain)
    # **旧留档没有 ts**:照旧一个时间都不给 —— 宁可空着,不猜
    old_msgs = [{k: v for k, v in m.items() if k != "ts"} for m in s.messages]
    old = _rows(activity.session_activity(old_msgs, workspace=tmp_path))
    assert all(r["time"] == "" for r in old)


def test_ts_restored_duration_is_the_real_call_to_result_gap(tmp_path):
    """ts 还原的 dur = 发起调用 → 结果落档 的真实间隔(重启后也能看到耗时)。"""
    import time as _time
    s = _session(tmp_path, [[
        {"id": "a", "name": "bash", "args": {"command": "pytest -q"}, "result": "x"},
    ]])
    msgs = s.messages
    call_ts = next(m["ts"] for m in msgs if m.get("role") == "assistant")
    # 人为把结果的时间戳推后 12 秒:还原出的 dur 必须是 12s,不是"看起来像"的别的数
    for m in msgs:
        if m.get("role") == "tool":
            t = _time.mktime(_time.strptime(call_ts, "%Y-%m-%d %H:%M:%S")) + 12
            m["ts"] = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(t))
    row = _rows(activity.session_activity(msgs, workspace=tmp_path))[0]
    assert row["dur_text"] == "12s"


def test_same_call_twice_pairs_in_order_not_both_to_the_first(tmp_path):
    """同一个文件读两次:两次各有各的时间。都领第一次的 = 面板在编故事。"""
    s = _session(tmp_path, [[
        {"id": "a", "name": "read_file", "args": {"path": "a.py"}, "result": "x"},
        {"id": "b", "name": "read_file", "args": {"path": "a.py"}, "result": "x"},
    ]])
    live = [_live("read_file", {"path": "a.py"}, 100, 103, True),
            _live("read_file", {"path": "a.py"}, 500, 509, True)]
    rows = _rows(activity.session_activity(s.messages, workspace=tmp_path, live=live))
    assert [r["dur_text"] for r in rows] == ["3s", "9s"]


def test_now_line_only_exists_when_something_is_actually_running(tmp_path):
    """「此刻在跑」那一句:没有真在跑的东西就给 None,前端整段不显示。"""
    now = {"name": "explore·bash", "summary": "pytest", "start": 1_700_000_000,
           "end": None, "elapsed": 42}
    line = activity.now_line(now)
    assert line["verb"] == "跑命令" and line["elapsed"] == 42
    assert line["agent_type"] == "explore" and line["start"].startswith(("1", "0"))
    assert activity.now_line(None) is None
    assert activity.now_line({"name": "bash", "summary": "x", "start": None}) is None
    # 回合已经结束:再旧的"在跑"也不算在跑
    s = _session(tmp_path, [[{"id": "a", "name": "bash", "args": {"command": "ls"}}]])
    act = activity.session_activity(s.messages, workspace=tmp_path,
                                   live=[dict(now)], now=now, busy=False)
    assert act["now"] is None


def test_subagent_tools_are_named_with_their_type_visible(tmp_path):
    """子代理内部那些 `explore·read_file` 行:动作词要认得,类型要看得见。"""
    from minicode import toolsum
    assert toolsum.short_name("explore·read_file") == "read_file"
    assert toolsum.verb("explore·read_file") == "读文件"
    assert toolsum.agent_type("explore·read_file") == "explore"
    # 没收录的名字(MCP 那种)原样显示,**不猜**它是干什么的
    assert toolsum.verb("github·create_issue") == "github·create_issue"


def test_every_timeline_row_that_can_do_something_does(tmp_path):
    """一行点下去要有真去处:写过文件的去开文件夹、开过网页的去开网址、
    其余的看这一步的原文。什么也做不了的行不给"能点"的样子。"""
    (tmp_path / "out.md").write_text("内容", encoding="utf-8")
    s = _session(tmp_path, [[
        {"id": "a", "name": "write_file", "args": {"path": "out.md"}, "result": "写好了"},
        {"id": "b", "name": "browser_open",
         "args": {"url": "https://jwc.example.edu.cn/a"}, "result": "打开了"},
        {"id": "c", "name": "read_file", "args": {"path": "nope.md"}, "result": "文件内容"},
        {"id": "d", "name": "glob", "args": {"pattern": "*.md"}},
    ]])
    rows = _rows(activity.session_activity(s.messages, workspace=tmp_path))
    assert rows[0]["action"]["type"] == "reveal"
    assert rows[1]["action"]["url"] == "https://jwc.example.edu.cn/a"
    assert rows[2]["action"]["type"] == "detail" and "文件内容" in rows[2]["action"]["text"]
    assert rows[3]["action"] is None          # 还没回话:什么也点不动


# ---------------------------------------------- 子代理产出尾注(v0.9.76)

def test_task_files_note_becomes_file_rows(tmp_path):
    """[files|档位] 尾注 → 产出行:在盘的带磁盘信息,没写成的翻「已不在磁盘」。

    这是 v0.9.60 盲区的修复:并行写三个文件,监控栏产出行不再一片空 ——
    子代理的 tool_calls 留在它自己的 session,主会话只有 task 报告文本,
    不解析尾注的话「产出」永远看不到子代理写的东西。
    """
    ws = Path(tmp_path)
    (ws / "docs").mkdir()
    (ws / "docs" / "ok.md").write_text("x", encoding="utf-8")   # 真写成的
    s = _session(tmp_path, [[
        {"id": "t1", "name": "task",
         "args": {"type": "campus", "task": "整理材料"},
         "result": ("[并行子代理完成 · 1 个]\n\n## 1. campus · 整理材料\n"
                    "[files|campus] docs/ok.md | 丢了的.md\n"
                    "已把两份材料整理好了。")},
    ]])
    rows = {r["title"]: r for r in _by_key(activity.session_activity(
        s.messages, workspace=ws), "files")["items"]}
    assert set(rows) == {"docs/ok.md", "丢了的.md"}
    ok = rows["docs/ok.md"]
    assert ok["badge"] == "产出完成" and "campus 子代理" in ok["sub"]
    assert "改于" in ok["sub"]                       # 磁盘真核过
    gone = rows["丢了的.md"]
    assert gone["status"] == "missing" and gone["badge"] == "已不在磁盘"
    assert gone["action"] is None                    # 失去可点的样子


def test_failed_task_files_note_is_ignored(tmp_path):
    """失败的 task 不算产出 —— 报告本身就是错误信息,尾注不存在也不可信。"""
    s = _session(tmp_path, [[
        {"id": "t1", "name": "task",
         "args": {"type": "implement", "task": "写文件"},
         "result": "错误: 子代理执行出错: RuntimeError: boom\n[files|implement] x.md"},
    ]])
    assert _by_key(activity.session_activity(s.messages), "files")["items"] == []


def test_plain_task_without_note_yields_no_file_rows(tmp_path):
    """只读子代理(无尾注行)照旧不产生产出行 —— 没做过就不许出现。"""
    s = _session(tmp_path, [[
        {"id": "t1", "name": "task", "args": {"type": "explore", "task": "看看"},
         "result": "[并行子代理完成 · 1 个]\n\n## 1. explore · 看看\n报告正文"},
    ]])
    assert _by_key(activity.session_activity(s.messages), "files")["items"] == []


def test_direct_and_subagent_writes_merge_into_one_row(tmp_path):
    """同一路径主会话直调 + 子代理尾注:合成一行,次数累加、档位并列。"""
    ws = Path(tmp_path)
    (ws / "a.md").write_text("x", encoding="utf-8")
    s = _session(tmp_path, [
        [{"id": "w1", "name": "write_file", "args": {"path": "a.md", "content": "1"},
          "result": "已写入"}],
        [{"id": "t1", "name": "task", "args": {"type": "implement", "task": "再改"},
          "result": "[files|implement] a.md\n改好了"}],
    ])
    rows = _by_key(activity.session_activity(s.messages, workspace=ws), "files")["items"]
    assert len(rows) == 1
    # 展示行没有内部计数键,次数与档位都读 sub 文本
    assert "写入 2 次" in rows[0]["sub"] and "implement 子代理" in rows[0]["sub"]
