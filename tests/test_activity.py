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
                              "workspace": str(tmp_path).replace("\\", "/")}


def test_read_only_tools_do_not_appear(tmp_path):
    """读文件/看计划不是"动作":面板上出现它们就纯属噪声,一类都不许沾。"""
    s = _session(tmp_path, [
        [{"id": "a", "name": "read_file", "args": {"path": "a.py"}, "result": "x"}],
        [{"id": "b", "name": "plan", "args": {"action": "read"}, "result": "计划…"}],
        [{"id": "c", "name": "glob", "args": {"pattern": "*.md"}, "result": "a.md"}],
    ])
    act = activity.session_activity(s.messages, workspace=tmp_path)
    assert act["empty"] is True and act["summary"]["calls"] == 3


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
