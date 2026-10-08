# -*- coding: utf-8 -*-
"""`--output-schema`:向模型要一份结构化结果,不合就明说(v0.9.136)。

这一条最容易骗人的地方在于"看起来验过了"。所以针分三族:

**A. schema 本身**:`load` 只收这一版真验得了的关键字 —— **不影响合法性判定的元数据
可以忽略(`description`/`title`/`default`…),影响的一定要不认并当场拒**。忽略一个
`anyOf` 或 `$ref` 的后果是"我按 schema 验过了"这句成了假话,而假绿比没验更难查。

**B. 验回来的值**:类型、必填、多出来的字段、enum、嵌套 items、数组里的第几项 ——
每条问题都要带路径,否则"不合 schema"这句话没法修。`true` 不许被当成 `integer`
(Python 里 bool 是 int 的子类,不专门拆就会)。

**C. 形状与退出码**:`structured_output` / `schema_violations` **永远在场**(字段集合
固定,调用方才不会在"这次没这个键"上写错分支);不合 schema 时前者是 null 而不是
半对的值;退出码 6 排在最后 —— 这一轮要是被中断/报错/撞上限收尾的,"形状不对"只是
后果,拿它当结论会让人去改 schema 而不是查真因。
"""
import json
from pathlib import Path

import pytest

from minicode import headless, schema as sm

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "score": {"type": "integer", "description": "0 到 100"},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "score"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- A. 读 schema

def test_inline_json_and_file_paths_both_load(tmp_path):
    assert sm.load(json.dumps(SCHEMA)) == SCHEMA
    p = tmp_path / "s.json"
    p.write_text(json.dumps(SCHEMA), encoding="utf-8")
    assert sm.load(str(p)) == SCHEMA


def test_metadata_only_keywords_are_ignored_but_structural_ones_are_not():
    """`description` 忽略无害,`pattern` 忽略就是假绿。"""
    ok = dict(SCHEMA)
    ok["properties"]["score"]["minimum"] = 0          # 这条是**结构型**
    with pytest.raises(sm.SchemaError) as e:
        sm.load(json.dumps(ok))
    assert "minimum" in str(e.value)
    assert "anyOf" in str(e.value) or "认的只" in str(e.value)


@pytest.mark.parametrize("bad", [
    '{"type": "object", "properties": {"a": {"$ref": "#/x"}}}',
    '{"type": "object", "patternProperties": {".*": {"type": "string"}}}',
    '{"not": {"type": "string"}}',
])
def test_unverifiable_constructs_refuse_instead_of_passing(bad):
    """三种最容易"默默放过"的形状:任何一条被忽略,`validate` 就会说"合"。"""
    with pytest.raises(sm.SchemaError):
        sm.load(bad)


@pytest.mark.parametrize("raw,why", [
    ("", "空的"),
    ("[]", "顶层要是一个对象"),
    ("{not json", "不是合法 JSON"),
    ("definitely/missing/file.json", "读不到这个文件"),
])
def test_bad_schemas_say_what_is_wrong(raw, why):
    with pytest.raises(sm.SchemaError) as e:
        sm.load(raw)
    assert why in str(e.value)


def test_instruction_is_turn_scoped_and_marks_itself_as_machine_added():
    text = sm.instruction(SCHEMA)
    assert "不是用户原话" in text          # 它冒充人话就会进留档、被"编辑重发"带回去
    assert '"verdict"' in text


# ---------------------------------------------------------------- B. 取值与验值

@pytest.mark.parametrize("answer", [
    '{"verdict":"pass","score":88,"notes":[]}',
    '```json\n{"verdict":"pass","score":88,"notes":[]}\n```',
    '好的,结果如下:\n{"verdict":"pass","score":88,"notes":["a"]}\n希望有帮助。',
    '{"verdict":"pass","score":88,"notes":["有嵌套 { 的字符串"]}',
])
def test_the_json_is_found_through_the_noise(answer):
    """模型会加前言、会包代码块、字符串里会有花括号 —— 取值不能只会被考第一种。"""
    got, err = sm.extract(answer)
    assert err is None, err
    assert got["verdict"] == "pass"
    assert sm.validate(got, SCHEMA) == []


def test_no_json_at_all_is_reported_not_silently_empty():
    got, err = sm.extract("这次没能完成,原因是网络断了。")
    assert got is None and "找不到" in err


def test_truncated_huge_answer_does_not_try_to_parse():
    got, err = sm.extract("x" * 300000)
    assert got is None and "太长" in err


def test_violations_carry_the_path_so_a_human_can_fix_them():
    bad = {"verdict": "maybe", "notes": ["ok", 7]}      # 缺 score、enum 不对、数组里一项错
    problems = sm.validate(bad, SCHEMA)
    assert any("required" in p or "必填" in p for p in problems)
    assert any("verdict" in p for p in problems)
    assert any("notes[1]" in p for p in problems), problems


def test_booleans_are_not_integers():
    """Python 里 `isinstance(True, int)` 为真 —— 不专门拆就会把 true 判成合法 integer。"""
    assert sm.validate({"verdict": "pass", "score": True}, SCHEMA)
    assert sm.validate({"verdict": "pass", "score": 88}, SCHEMA) == []


def test_extra_fields_are_flagged_only_when_the_schema_closes_them():
    got = {"verdict": "pass", "score": 1, "extra": 2}
    assert any("extra" in p for p in sm.validate(got, SCHEMA))
    open_schema = {"type": "object", "properties": {"verdict": {"type": "string"}}}
    assert sm.validate(got, open_schema) == []      # 没写 additionalProperties 就不加严


def test_anyof_branches_are_checked():
    s = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert sm.validate("x", s) == []
    assert sm.validate(None, s)                    # null 两个分支都不合


# ---------------------------------------------------------------- C. 形状与退出码

def test_flag_parsing():
    assert headless.pop_output_schema(["a"]) == (None, ["a"])
    raw, rest = headless.pop_output_schema(["--output-schema", "s.json", "跑个任务"])
    assert raw == "s.json" and rest == ["跑个任务"]
    assert headless.pop_output_schema(["--output-schema={}", "任务"])[0] == "{}"
    with pytest.raises(headless.UsageError):
        headless.pop_output_schema(["--output-schema"])
    with pytest.raises(headless.UsageError):
        headless.pop_output_schema(["--output-schema", "--json", "任务"])


def test_violation_is_the_last_exit_code_in_priority():
    """被中断/报错/撞上限收尾时,"形状不对"只是后果,不该顶掉真因。

    三种真结局经**回调**置起(`u.llm_error(...)` 而不是往同名字段上 setattr):
    v0.9.137 那个「模型一报错,无头进程当场崩」就是被 setattr 版的针洗绿的。
    """
    for fire, want in (
        (lambda u: u.turn_interrupted(), headless.EXIT_INTERRUPTED),
        (lambda u: u.llm_error(RuntimeError("APITimeoutError")), headless.EXIT_RUNTIME),
        (lambda u: u.max_steps_reached(40), headless.EXIT_LIMITED),
        (lambda u: u.confirm_tool(type("T", (), {"name": "bash"})(),
                                  {"command": "ls"}, "要点头"),
         headless.EXIT_ASK_REFUSED),
    ):
        ui = _ui()
        fire(ui)
        assert headless.exit_code_for(ui, ["不合"]) == want
        assert headless.status_for(ui, ["不合"]) != "schema_violation"
    ui = _ui()
    assert headless.exit_code_for(ui, ["不合"]) == headless.EXIT_SCHEMA_VIOLATION
    assert headless.status_for(ui, ["不合"]) == "schema_violation"
    assert headless.exit_code_for(_ui(), []) == headless.EXIT_OK


def _ui():
    from minicode.headless import HeadlessUI

    class Inner:
        def __getattr__(self, name):
            return lambda *a, **k: None

    return HeadlessUI(Inner())


def test_result_always_carries_both_schema_fields():
    """字段集合固定:调用方不该要处理"这次恰好没有这个键"。"""
    from minicode.config import Config
    ui = _ui()
    r = headless.build_result(ui, object(), type("S", (), {"messages": []})(),
                              Config(), task="t", elapsed_ms=1, fmt="json")
    assert set(r) >= {"structured_output", "schema_violations"}
    assert r["structured_output"] is None and r["schema_violations"] is None
    assert r["exit_code"] == headless.EXIT_OK


def test_a_violating_answer_yields_no_half_right_value():
    from minicode.config import Config

    class S:
        messages = [{"role": "assistant", "content": '{"verdict":"maybe","score":"高"}'}]

    r = headless.build_result(_ui(), object(), S(), Config(), task="t",
                              elapsed_ms=1, fmt="json", schema=SCHEMA)
    assert r["structured_output"] is None, "半对的值一旦给出去,调用方会当真实数据用"
    assert r["schema_violations"] and r["exit_code"] == headless.EXIT_SCHEMA_VIOLATION
    assert r["status"] == "schema_violation" and r["ok"] is False


def test_a_conforming_answer_lands_in_structured_output():
    from minicode.config import Config

    class S:
        messages = [{"role": "assistant",
                     "content": '{"verdict":"pass","score":90,"notes":["n"]}'}]

    r = headless.build_result(_ui(), object(), S(), Config(), task="t",
                              elapsed_ms=1, fmt="json", schema=SCHEMA)
    assert r["structured_output"] == {"verdict": "pass", "score": 90, "notes": ["n"]}
    assert r["schema_violations"] is None and r["exit_code"] == headless.EXIT_OK


def test_the_directive_lives_one_turn_and_reaches_every_step():
    """附加句真跑到请求里、且下一问就没了 —— 这条要的是"每次都说到 + 不越界"。

    只看 `_messages_for_request` 会漏掉两件事:一是不是**每一步**都带(模型常常先调
    几轮工具才给最终答案),二是回合结束后有没有清干净(不清就等于下一问还带着上一轮
    机器的要求)。所以这里看的是发出去的 messages 本体。
    """
    from minicode.agent import Agent
    from minicode.config import Config
    from minicode.llm import AssistantTurn
    from minicode.permission import PermissionGate
    from minicode.session import Session
    from minicode.tools.base import ToolContext
    from minicode.tools.registry import build_default_registry

    from test_agent import FakeLLM, FakeUI
    cfg = Config()
    ctx = ToolContext(workspace=Path.cwd(), config=cfg)
    llm = FakeLLM([AssistantTurn(text="第一问答完"), AssistantTurn(text="第二问答完")])
    agent = Agent(llm=llm, registry=build_default_registry(),
                  session=Session(system_prompt="sys"),
                  gate=PermissionGate(cfg), ui=FakeUI(), ctx=ctx)
    directive = sm.instruction(SCHEMA)
    agent.run_turn("第一问", output_directive=directive)
    first_turn_msgs = llm.calls[0]
    assert "不是用户原话" in first_turn_msgs[0]["content"]
    assert '"verdict"' in first_turn_msgs[0]["content"]
    agent.run_turn("第二问")
    assert "不是用户原话" not in llm.calls[-1][0]["content"], "上一问的机器要求串到下一问了"
    # 用户原话一个字都没被改:附加句走系统侧,不冒充人话
    assert [m["content"] for m in agent.session.messages
            if m["role"] == "user"] == ["第一问", "第二问"]
