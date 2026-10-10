"""指令文件第三级:子目录就近 `AGENTS.md`(v0.9.149)。

这一层的全部价值在一句话上 —— **改哪个目录的文件、就按哪个目录的规矩来** ——
所以针分四族,每一族都对着一个"不说清就会做错"的地方:

- **取层**:从根到最近(顺序就是优先级)、工作区外不算、读不动的不算;
- **按层记账**:一层一次点头,采纳一层**不许**顺带采纳另一层(这一层在安全上
  唯一比两级更严的地方 —— 别人 clone 一个仓库等于把它自带的每一份都递到你面前);
- **注入**:采纳过的那层落进**那一次工具结果**,而且只落一次;没采纳的不注入,
  但要说一次"这里有一层、你还没采纳"(不说就等于"我写了它没读");
- **点头卡**:碰到过的目录层在**下一问**才问,而且**一次只问一层**。
"""
from __future__ import annotations

import json

from minicode import app_setup, permspec
from minicode.llm import AssistantTurn, ToolCall

from test_agent import (FakeLLM, FakeUI, _NodUI, _YES, _NO, _agents_ws, make_agent)

# 提问卡的回传形状(与 `test_agent.py` 那几支同源):只有 `mode=answer` 且
# `choice` 精确等于第一个选项,才算点头 —— 这里就是把那个形状写出来。
_YES_REPLY = {"mode": "answer", "answers": [{"id": 0, "choice": _YES}]}
_NO_REPLY = {"mode": "answer", "answers": [{"id": 0, "choice": _NO}]}


# ------------------------------------------------------------------ 取层
def _tree(tmp_path, layers: dict):
    """按 {相对目录: 正文} 摆一棵树,回"最深那个文件"当锚点(第三级就是按它取层)。"""
    for rel, text in layers.items():
        d = (tmp_path / rel) if rel else tmp_path
        d.mkdir(parents=True, exist_ok=True)
        (d / "AGENTS.md").write_text(text, encoding="utf-8")
    deep = tmp_path / "sub" / "deep"
    deep.mkdir(parents=True, exist_ok=True)
    target = deep / "x.py"
    target.write_text("x = 1\n", encoding="utf-8")
    return target


def test_layers_run_from_the_root_to_the_nearest(tmp_path):
    """顺序 = 根 → 最近。**这个顺序本身就是优先级**,排反了越具体的规矩越先被读。"""
    target = _tree(tmp_path, {"": "根", "sub": "中层", "sub/deep": "最近"})
    rels = [l["rel"] for l in app_setup.subdir_agents_layers(tmp_path, target)]
    assert rels == ["", "sub", "sub/deep"]


def test_a_directory_without_a_file_is_skipped(tmp_path):
    target = _tree(tmp_path, {"": "根", "sub/deep": "最近"})
    rels = [l["rel"] for l in app_setup.subdir_agents_layers(tmp_path, target)]
    assert rels == ["", "sub/deep"]


def test_a_target_outside_the_workspace_yields_nothing(tmp_path):
    """工作区外那份不算 —— 与 `resolve_path`/敏感路径那套围栏同一条口径:
    不能因为"这是配置文件"就读到工作区外面去。"""
    outside = tmp_path.parent / "outside-layers"
    outside.mkdir(exist_ok=True)
    (outside / "AGENTS.md").write_text("外面的", encoding="utf-8")
    assert app_setup.subdir_agents_layers(tmp_path, outside / "x.py") == []


def test_an_empty_file_is_not_a_layer(tmp_path):
    """读不动/空白一律不算:宁可少报一层,也不报一层"读到了"其实没读到的。"""
    target = _tree(tmp_path, {"": "根"})
    (tmp_path / "sub" / "AGENTS.md").write_text("   \n\n", encoding="utf-8")
    rels = [l["rel"] for l in app_setup.subdir_agents_layers(tmp_path, target)]
    assert rels == [""]


def test_a_new_file_that_does_not_exist_yet_still_anchors(tmp_path):
    """`write_file` 要建的那份文件还不存在 —— 取层必须照样走它父目录那条链,
    否则"第一次在这个目录里建文件"这一下永远读不到那个目录的规矩。"""
    _tree(tmp_path, {"": "根", "sub": "中层"})
    rels = [l["rel"] for l in app_setup.subdir_agents_layers(
        tmp_path, tmp_path / "sub" / "brand-new.py")]
    assert rels == ["", "sub"]


# ------------------------------------------------------------- 按层记账
def test_adopting_one_layer_does_not_adopt_another(tmp_path, monkeypatch):
    monkeypatch.setattr(permspec, "trust_path",
                        lambda: tmp_path / "app" / "perms-trust.json")
    target = _tree(tmp_path, {"": "根", "sub": "中层"})
    root, sub = app_setup.subdir_agents_layers(tmp_path, target)
    assert not app_setup.layer_adopted(tmp_path, root)
    assert not app_setup.layer_adopted(tmp_path, sub)
    app_setup.adopt_layer(tmp_path, sub)
    assert app_setup.layer_adopted(tmp_path, sub)
    assert not app_setup.layer_adopted(tmp_path, root), \
        "采纳一层不许顺带采纳另一层(一层一次点头是这一层唯一的严格之处)"


def test_only_the_changed_layer_goes_back_to_unadopted(tmp_path, monkeypatch):
    """改一个字只把**那一层**打回没采纳:另一层的点头不该被牵连。"""
    monkeypatch.setattr(permspec, "trust_path",
                        lambda: tmp_path / "app" / "perms-trust.json")
    target = _tree(tmp_path, {"": "根", "sub": "中层"})
    root, sub = app_setup.subdir_agents_layers(tmp_path, target)
    app_setup.adopt_layer(tmp_path, root)
    app_setup.adopt_layer(tmp_path, sub)
    (tmp_path / "sub" / "AGENTS.md").write_text("中层改了一个字", encoding="utf-8")
    target = _tree(tmp_path, {"sub": "中层改了一个字"})
    root2, sub2 = app_setup.subdir_agents_layers(tmp_path, target)
    assert app_setup.layer_adopted(tmp_path, root2), "根那一层不该被牵连"
    assert not app_setup.layer_adopted(tmp_path, sub2)


def test_the_root_layer_keeps_its_old_home(tmp_path, monkeypatch):
    """根那一层照旧住在 `agents_sha256` 那几个键上 —— 老留档里已经点过头的项目
    不该因为这一版又被问一遍(所以这里钉的是"老键仍然生效")。"""
    monkeypatch.setattr(permspec, "trust_path",
                        lambda: tmp_path / "app" / "perms-trust.json")
    _tree(tmp_path, {"": "根"})
    app_setup.adopt_agents(tmp_path)                     # 走的是 v0.9.127 那条老路
    assert app_setup.agents_adoption(tmp_path)["adopted"] is True
    assert permspec.agents_adopted_layers(tmp_path) == {}, "根那一层不该顺手写进新表"


# --------------------------------------------------------------- 注入块
def test_the_layer_block_names_its_directory_and_keeps_the_guard(tmp_path):
    target = _tree(tmp_path, {"sub": "SUB-MARKER"})
    layer = app_setup.subdir_agents_layers(tmp_path, target)[-1]
    out = app_setup.layer_prompt_section(tmp_path, layer)
    assert "SUB-MARKER" in out
    assert "`sub/`" in out, "不写清是哪一层的,模型没法判断它管哪些文件"
    assert app_setup.INSTRUCTION_GUARD in out, "红线那一段必须在每一层里都在场"


def test_the_layer_block_is_truncated_and_says_so(tmp_path):
    target = _tree(tmp_path, {"sub": "长" * (app_setup.AGENTS_CHAR_LIMIT + 50)})
    layer = app_setup.subdir_agents_layers(tmp_path, target)[-1]
    out = app_setup.layer_prompt_section(tmp_path, layer)
    assert "已截断" in out, "静默吃掉半句规矩比不注入更坏"


# ----------------------------------------------------------------- 报表
def test_the_line_reports_directory_layers_without_duplicating_the_root(tmp_path):
    _tree(tmp_path, {"": "根", "sub": "中层"})
    line = app_setup.instructions_line(tmp_path, ["", "sub"])
    assert line.count("项目级") == 1, f"根那一层被报了两遍:{line}"
    assert "目录级 sub/" in line
    assert "目录级 /" not in line


def test_an_unadopted_directory_layer_is_marked_in_the_line(tmp_path, monkeypatch):
    monkeypatch.setattr(permspec, "trust_path",
                        lambda: tmp_path / "app" / "perms-trust.json")
    _tree(tmp_path, {"sub": "中层"})
    line = app_setup.instructions_line(tmp_path, ["sub"])
    assert "未采纳" in line, f"没采纳这件事必须说得出口:{line}"


def test_layers_are_reported_in_the_order_they_were_given(tmp_path):
    """去重且保序:同一层被两个锚点碰到时不该报两遍。"""
    _tree(tmp_path, {"": "根", "sub": "中层"})
    layers = app_setup.instruction_layers(tmp_path, ["sub", "sub"])
    assert [x["scope"] for x in layers] == ["项目级", "目录级"]


# ------------------------------------------------------- 注入进工具结果
def _read(path: str) -> AssistantTurn:
    return AssistantTurn(tool_calls=[ToolCall(
        id="c" + path, name="read_file",
        arguments=json.dumps({"path": path}))])


def _tool_results(agent) -> list[str]:
    return [m["content"] for m in agent.session.messages if m.get("role") == "tool"]


def _sub_ws(tmp_path, monkeypatch, body="SUB-MARKER"):
    ws = _agents_ws(tmp_path, monkeypatch, text="ROOT-MARKER")
    (tmp_path / "sub").mkdir(exist_ok=True)
    (tmp_path / "sub" / "AGENTS.md").write_text(body, encoding="utf-8")
    (tmp_path / "sub" / "x.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "sub" / "y.py").write_text("y = 1\n", encoding="utf-8")
    return ws


def test_an_adopted_layer_lands_in_the_tool_result(config, ctx, tmp_path, monkeypatch):
    _sub_ws(tmp_path, monkeypatch)
    app_setup.adopt_layer(tmp_path, app_setup.layer_at(tmp_path, "sub"))
    agent = make_agent(config, ctx,
                       FakeLLM([_read("sub/x.py"), AssistantTurn(text="好")]), FakeUI())
    agent.run_turn("看一眼")
    joined = "".join(_tool_results(agent))
    assert "SUB-MARKER" in joined
    assert "目录级指令" in joined, "注入的那一段必须自报是哪一层"


def test_the_same_layer_is_injected_only_once(config, ctx, tmp_path, monkeypatch):
    """只落一次:它进了留档就跟着这一路对话走,每一步重贴既是白花 token,
    也会让模型以为"这条规矩刚刚又强调了一次"。"""
    _sub_ws(tmp_path, monkeypatch)
    app_setup.adopt_layer(tmp_path, app_setup.layer_at(tmp_path, "sub"))
    agent = make_agent(config, ctx, FakeLLM([
        _read("sub/x.py"), _read("sub/y.py"), AssistantTurn(text="好")]), FakeUI())
    agent.run_turn("看两个")
    hits = sum("SUB-MARKER" in r for r in _tool_results(agent))
    assert hits == 1, f"同一层注入了 {hits} 次"


def test_an_unadopted_layer_is_not_injected_but_is_noted_once(config, ctx, tmp_path,
                                                              monkeypatch):
    """没采纳就不注入,但**要说一次** —— 不说的话,用户会以为"我写了它没读",
    而那正是这一整层要消灭的那种扯皮。"""
    _sub_ws(tmp_path, monkeypatch)
    agent = make_agent(config, ctx, FakeLLM([
        _read("sub/x.py"), _read("sub/y.py"), AssistantTurn(text="好")]), FakeUI())
    agent.run_turn("看两个")
    results = _tool_results(agent)
    assert not any("SUB-MARKER" in r for r in results), "没采纳的层一个字都不该注入"
    noted = sum("还没采纳" in r for r in results)
    assert noted == 1, f"那句提醒该只说一次,实际 {noted} 次"


def test_the_root_layer_never_goes_through_the_tool_result(config, ctx, tmp_path,
                                                           monkeypatch):
    """根那一层走系统提示词。两边都注入等于同一份规矩在上下文里出现两遍。"""
    _agents_ws(tmp_path, monkeypatch, text="ROOT-MARKER")
    app_setup.adopt_agents(tmp_path)
    agent = make_agent(config, ctx,
                       FakeLLM([_read("a.py"), AssistantTurn(text="好")]), FakeUI())
    agent.run_turn("看一眼")
    assert not any("ROOT-MARKER" in r for r in _tool_results(agent))


def test_a_directory_layer_far_above_the_file_still_applies(config, ctx, tmp_path,
                                                            monkeypatch):
    """根与最近之间的那一层也算 —— 只算"离文件最近的一份"会把中间层丢掉。"""
    _sub_ws(tmp_path, monkeypatch)
    (tmp_path / "sub" / "deep").mkdir()
    (tmp_path / "sub" / "deep" / "z.py").write_text("z = 1\n", encoding="utf-8")
    app_setup.adopt_layer(tmp_path, app_setup.layer_at(tmp_path, "sub"))
    agent = make_agent(config, ctx,
                       FakeLLM([_read("sub/deep/z.py"), AssistantTurn(text="好")]),
                       FakeUI())
    agent.run_turn("看一眼深的")
    assert "SUB-MARKER" in "".join(_tool_results(agent))


# ------------------------------------------------------------- 取层锚点
def test_apply_patch_anchors_cover_every_file_in_the_patch(config, ctx, tmp_path):
    """`apply_patch` 一次碰好几个文件,参数里没有单个 `path` —— 漏了它,
    用补丁改的那几个目录的规矩就永远不生效。"""
    from minicode.tools.base import Tool
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    tool = Tool(name="apply_patch", description="d", read_only=False,
                parameters={"type": "object", "properties": {}},
                handler=lambda ctx, **kw: "")
    patch = ("--- a/sub/x.py\n+++ b/sub/x.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
             "--- a/other/y.py\n+++ b/other/y.py\n@@ -1 +1 @@\n-y = 1\n+y = 2\n")
    anchors = [a.replace("\\", "/") for a in agent._layer_anchors(tool, {"patch_text": patch})]
    assert any(a.endswith("sub/x.py") for a in anchors), anchors
    assert any(a.endswith("other/y.py") for a in anchors), anchors


def test_a_broken_patch_yields_no_anchors_instead_of_raising(config, ctx, tmp_path):
    """补丁解析不出来时静默退回"没碰到文件" —— 那条路会由 `apply_patch` 自己报错,
    在这里抢着说只会把真因埋掉。"""
    from minicode.tools.base import Tool
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    tool = Tool(name="apply_patch", description="d", read_only=False,
                parameters={"type": "object", "properties": {}},
                handler=lambda ctx, **kw: "")
    assert agent._layer_anchors(tool, {"patch_text": "这不是补丁"}) == []


def test_tools_without_a_path_yield_no_anchors(config, ctx, tmp_path):
    from minicode.tools.base import Tool
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    tool = Tool(name="bash", description="d", read_only=False,
                parameters={"type": "object", "properties": {}},
                handler=lambda ctx, **kw: "")
    assert agent._layer_anchors(tool, {"command": "ls"}) == []


# --------------------------------------------------------------- 点头卡
def test_a_touched_layer_is_asked_about_on_the_next_turn(config, ctx, tmp_path,
                                                         monkeypatch):
    """碰到的那一问**不问**(卡已经在开头过去了),下一问开头才问。"""
    _sub_ws(tmp_path, monkeypatch)
    app_setup.adopt_agents(tmp_path)          # 根那一层先采纳掉,好让卡是给目录层的
    ui = _NodUI(_YES_REPLY)
    agent = make_agent(config, ctx, FakeLLM([
        _read("sub/x.py"), AssistantTurn(text="好"), AssistantTurn(text="好")]), ui)
    agent.run_turn("第一问")
    assert ui.cards == [], "碰到的那一问不该当场弹卡"
    agent.run_turn("第二问")
    assert len(ui.cards) == 1, f"下一问该问一层,实际 {len(ui.cards)} 张"
    qs, _note = ui.cards[0]
    assert "sub/" in qs[0]["q"]
    assert qs[0]["header"] == "目录指令"


def test_answering_yes_adopts_that_layer_and_only_that_layer(config, ctx, tmp_path,
                                                             monkeypatch):
    _sub_ws(tmp_path, monkeypatch)
    app_setup.adopt_agents(tmp_path)
    ui = _NodUI(_YES_REPLY)
    agent = make_agent(config, ctx, FakeLLM([
        _read("sub/x.py"), AssistantTurn(text="好"), AssistantTurn(text="好")]), ui)
    agent.run_turn("第一问")
    agent.run_turn("第二问")
    assert app_setup.layer_adopted(tmp_path, app_setup.layer_at(tmp_path, "sub"))


def test_a_refusal_does_not_adopt_but_stops_asking(config, ctx, tmp_path, monkeypatch):
    """不采纳就是"这一会话里也不再问" —— 追着问第二遍是这一层最招人烦的坏法。"""
    _sub_ws(tmp_path, monkeypatch)
    app_setup.adopt_agents(tmp_path)
    ui = _NodUI(_NO_REPLY)
    agent = make_agent(config, ctx, FakeLLM([
        _read("sub/x.py"), AssistantTurn(text="好"), AssistantTurn(text="好"),
        AssistantTurn(text="好")]), ui)
    agent.run_turn("第一问")
    agent.run_turn("第二问")
    agent.run_turn("第三问")
    assert len(ui.cards) == 1, f"拒过之后不该再问,实际 {len(ui.cards)} 张"
    assert not app_setup.layer_adopted(tmp_path, app_setup.layer_at(tmp_path, "sub"))


def test_only_one_layer_is_asked_per_turn(config, ctx, tmp_path, monkeypatch):
    """一次只问一层:一张卡里列 N 层要用户逐条对齐,而对齐错了的代价是**替他点了头**。"""
    _sub_ws(tmp_path, monkeypatch)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "AGENTS.md").write_text("OTHER-MARKER", encoding="utf-8")
    (tmp_path / "other" / "w.py").write_text("w = 1\n", encoding="utf-8")
    app_setup.adopt_agents(tmp_path)
    ui = _NodUI(_YES_REPLY)
    agent = make_agent(config, ctx, FakeLLM([
        _read("sub/x.py"),
        AssistantTurn(tool_calls=[ToolCall(
            id="c2", name="read_file", arguments=json.dumps({"path": "other/w.py"}))]),
        AssistantTurn(text="好"), AssistantTurn(text="好"), AssistantTurn(text="好")]),
        ui)
    agent.run_turn("第一问")
    agent.run_turn("第二问")
    assert len(ui.cards) == 1, f"一问只该问一层,实际 {len(ui.cards)} 张"


def test_the_ui_line_lists_the_touched_layers(config, ctx, tmp_path, monkeypatch):
    """侧栏那一行要看得见目录层 —— 它存在的全部意义就是"它到底读了没有"。"""
    _sub_ws(tmp_path, monkeypatch)
    app_setup.adopt_layer(tmp_path, app_setup.layer_at(tmp_path, "sub"))
    agent = make_agent(config, ctx,
                       FakeLLM([_read("sub/x.py"), AssistantTurn(text="好")]), FakeUI())
    agent.run_turn("看一眼")
    line = app_setup.instructions_line(tmp_path, sorted(agent._agents_layers))
    assert "目录级 sub/" in line
