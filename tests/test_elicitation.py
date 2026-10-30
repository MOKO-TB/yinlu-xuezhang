"""MCP elicitation 表单(计划书阶段 7)的口径测试。

三组:①schema 编译的**安全子集**(支持什么、拒什么,一条条钉);
②前端交回来的字符串怎么收口成类型正确的 content;
③Bridge 的整条握手(推送卡片 → 回复 → 唤醒;校验不过卡不下;超时/不支持 = decline)。
Bridge 那几支要真线程,与 test_gui_bridge 同款打 `slow` 标。
"""
import threading

import pytest

from minicode import elicitation


# ---------------------------------------------------------------- 编译:支持面

def test_compile_accepts_the_supported_subset():
    fields, why = elicitation.compile_schema({
        "type": "object",
        "properties": {
            "path": {"type": "string", "title": "文件路径", "maxLength": 100,
                     "description": "要写哪个文件"},
            "times": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
            "loud": {"type": "boolean"},
            "mode": {"type": "string", "enum": ["fast", "slow"]},
        },
        "required": ["path"],
    })
    assert why == ""
    assert [f["name"] for f in fields] == ["path", "times", "loud", "mode"]
    assert fields[0]["required"] is True and fields[0]["max_len"] == 100
    assert fields[1]["minimum"] == 1 and fields[1]["default"] == 2
    assert fields[3]["enum"] == ["fast", "slow"]


def test_compile_rejects_nested_and_unknown_types():
    for bad in ({"type": "array"}, {"type": "object"}, {"type": "weird"},
                {}):
        fields, why = elicitation.compile_schema(
            {"type": "object", "properties": {"x": bad}})
        assert fields == [] and "本版不支持" in why, (bad, why)


def test_compile_rejects_non_object_roots_and_empty_forms():
    assert "根类型" in elicitation.compile_schema({"type": "string"})[1]
    assert "没有 properties" in elicitation.compile_schema(
        {"type": "object", "properties": {}})[1]
    assert "不是一份对象" in elicitation.compile_schema(None)[1]


def test_compile_rejects_too_many_fields_and_fancy_constraints():
    many = {"type": "object", "properties": {
        ("f%d" % i): {"type": "string"} for i in range(9)}}
    assert "上限" in elicitation.compile_schema(many)[1]
    fancy = {"type": "object", "properties": {
        "x": {"type": "string", "pattern": "^a"}}}
    assert "不认的约束" in elicitation.compile_schema(fancy)[1]
    bad_enum = {"type": "object", "properties": {
        "x": {"type": "string", "enum": []}}}
    assert "枚举" in elicitation.compile_schema(bad_enum)[1]


# ---------------------------------------------------------------- 校验

def _fields():
    fields, _ = elicitation.compile_schema({
        "type": "object",
        "properties": {
            "path": {"type": "string", "title": "文件路径", "maxLength": 10},
            "times": {"type": "integer", "minimum": 1, "maximum": 5},
            "loud": {"type": "boolean"},
            "mode": {"type": "string", "enum": ["fast", "slow"], "default": "fast"},
        },
        "required": ["path"],
    })
    return fields


def test_validate_coerces_strings_into_typed_content():
    content, errors = elicitation.validate(
        _fields(), {"path": "a/b.txt", "times": "3", "loud": "yes", "mode": "slow"})
    assert errors == []
    assert content == {"path": "a/b.txt", "times": 3, "loud": True, "mode": "slow"}


def test_validate_missing_required_names_the_field_and_uses_defaults():
    content, errors = elicitation.validate(_fields(), {"path": ""})
    assert any("文件路径" in e for e in errors)
    assert content.get("mode") == "fast", "有默认值的空字段补默认"


def test_validate_rejects_bad_numbers_ranges_enums_and_lengths():
    cases = [
        ({"path": "p", "times": "abc"}, "要填整数"),
        ({"path": "p", "times": "0"}, "不能小于"),
        ({"path": "p", "times": "9"}, "不能大于"),
        ({"path": "p", "mode": "turbo"}, "下拉里选"),
        ({"path": "p", "loud": "maybe"}, "只认 是/否"),
        ({"path": "a" * 11}, "最多 10 个字符"),
    ]
    for values, mark in cases:
        _c, errors = elicitation.validate(_fields(), values)
        assert any(mark in e for e in errors), (values, errors)


def test_validate_ignores_keys_that_are_not_on_the_form():
    content, errors = elicitation.validate(
        _fields(), {"path": "p", "extra": " injected"})
    assert errors == [] and "extra" not in content


# ---------------------------------------------------------------- Bridge 握手

@pytest.mark.slow
def test_elicit_round_trip_accept(tmp_path):
    from dataclasses import replace
    from minicode.config import BUILTIN_PROVIDER, BUILTIN_PROVIDER_ID, Config
    from minicode.llm import AssistantTurn
    from tests.test_gui_bridge import FakeLLM, make, wait_until

    cfg = Config(providers=[replace(BUILTIN_PROVIDER)],
                 active_provider_id=BUILTIN_PROVIDER_ID)
    bridge, window, _ = make(cfg, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    got = {}

    def run():
        got["r"] = bridge._elicit("填一下", {
            "type": "object",
            "properties": {
                "path": {"type": "string", "title": "文件路径", "maxLength": 100},
                "times": {"type": "integer", "title": "次数"},
            },
            "required": ["path"]})

    t = threading.Thread(target=run)
    t.start()
    assert wait_until(lambda: bridge._elicit_id), "卡片没推出来"
    ev = window.of_type("elicit_request")[-1]
    assert ev["message"] == "填一下"
    assert [f["name"] for f in ev["fields"]] == ["path", "times"]
    r = bridge.elicit_reply(ev["elicit_id"],
                            {"values": {"path": "a/b.txt", "times": "3"}})
    assert r == {"ok": True}
    t.join(5)
    assert got["r"] == {"action": "accept",
                        "content": {"path": "a/b.txt", "times": 3}}


@pytest.mark.slow
def test_bad_values_keep_the_card_up_and_good_ones_land(tmp_path):
    from dataclasses import replace
    from minicode.config import BUILTIN_PROVIDER, BUILTIN_PROVIDER_ID, Config
    from minicode.llm import AssistantTurn
    from tests.test_gui_bridge import FakeLLM, make, wait_until

    cfg = Config(providers=[replace(BUILTIN_PROVIDER)],
                 active_provider_id=BUILTIN_PROVIDER_ID)
    bridge, _window, _ = make(cfg, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    got = {}

    def run():
        got["r"] = bridge._elicit("填", {"type": "object", "properties": {
            "path": {"type": "string", "title": "文件路径"}}, "required": ["path"]})

    t = threading.Thread(target=run)
    t.start()
    assert wait_until(lambda: bridge._elicit_id)
    eid = bridge._elicit_id
    r1 = bridge.elicit_reply(eid, {"values": {"path": ""}})
    assert r1["ok"] is False and r1["errors"], "必填空了要打回重填"
    r2 = bridge.elicit_reply(eid, {"values": {"path": "ok.txt"}})
    assert r2 == {"ok": True}
    t.join(5)
    assert got["r"] == {"action": "accept", "content": {"path": "ok.txt"}}


@pytest.mark.slow
def test_decline_reply_and_stale_id_are_honest(tmp_path):
    from dataclasses import replace
    from minicode.config import BUILTIN_PROVIDER, BUILTIN_PROVIDER_ID, Config
    from minicode.llm import AssistantTurn
    from tests.test_gui_bridge import FakeLLM, make, wait_until

    cfg = Config(providers=[replace(BUILTIN_PROVIDER)],
                 active_provider_id=BUILTIN_PROVIDER_ID)
    bridge, _window, _ = make(cfg, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    got = {}

    def run():
        got["r"] = bridge._elicit("填", {"type": "object", "properties": {
            "path": {"type": "string", "title": "文件路径"}}})

    t = threading.Thread(target=run)
    t.start()
    assert wait_until(lambda: bridge._elicit_id)
    eid = bridge._elicit_id
    assert bridge.elicit_reply("stale-id", {"values": {"path": "x"}})["ok"] is False
    bridge.elicit_reply(eid, {"decline": True})
    t.join(5)
    assert got["r"] == {"action": "decline"}


@pytest.mark.slow
def test_unsupported_schema_declines_immediately_without_any_card(tmp_path):
    from dataclasses import replace
    from minicode.config import BUILTIN_PROVIDER, BUILTIN_PROVIDER_ID, Config
    from minicode.llm import AssistantTurn
    from tests.test_gui_bridge import FakeLLM, make

    cfg = Config(providers=[replace(BUILTIN_PROVIDER)],
                 active_provider_id=BUILTIN_PROVIDER_ID)
    bridge, window, _ = make(cfg, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    got = bridge._elicit("要个数组", {"type": "object", "properties": {
        "xs": {"type": "array", "items": {"type": "string"}}}})
    assert got == {"action": "decline"}
    assert bridge._elicit_id == "", "不支持的表单不该有卡片在等"
    infos = [e for e in window.events if e.get("type") == "info"]
    assert any("接不了" in str(e.get("text")) for e in infos)


@pytest.mark.slow
def test_wait_timeout_declines_instead_of_hanging(tmp_path, monkeypatch):
    from dataclasses import replace
    from minicode.config import BUILTIN_PROVIDER, BUILTIN_PROVIDER_ID, Config
    from minicode.llm import AssistantTurn
    from tests.test_gui_bridge import FakeLLM, make, wait_until

    monkeypatch.setattr(elicitation, "WAIT_SECONDS", 0.2)
    cfg = Config(providers=[replace(BUILTIN_PROVIDER)],
                 active_provider_id=BUILTIN_PROVIDER_ID)
    bridge, _window, _ = make(cfg, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    got = {}

    def run():
        got["r"] = bridge._elicit("填", {"type": "object", "properties": {
            "path": {"type": "string", "title": "文件路径"}}})

    t = threading.Thread(target=run)
    t.start()
    assert wait_until(lambda: bridge._elicit_id)
    t.join(10)
    assert got["r"] == {"action": "decline"}, "超时要收口,不能让服务端干等"


@pytest.mark.slow
def test_create_app_wires_the_manager_callback_to_the_bridge(tmp_path):
    """create_app 的 holder 必须真把管理器的 elicitation 转到 Bridge —— 没接上的
    表现是"界面好好的,服务器永远收到 decline"。零服务器时直接打管理器那个口。"""
    from dataclasses import replace
    from minicode.config import BUILTIN_PROVIDER, BUILTIN_PROVIDER_ID, Config
    from minicode.llm import AssistantTurn
    from tests.test_gui_bridge import FakeLLM, make, wait_until

    cfg = Config(providers=[replace(BUILTIN_PROVIDER)],
                 active_provider_id=BUILTIN_PROVIDER_ID)
    bridge, _window, _ = make(cfg, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    got = {}

    def run():
        got["r"] = bridge._mcp.on_elicit("填", {"type": "object", "properties": {
            "path": {"type": "string", "title": "文件路径"}}})

    t = threading.Thread(target=run)
    t.start()
    assert wait_until(lambda: bridge._elicit_id), "管理器的回调没转到 Bridge"
    bridge.elicit_reply(bridge._elicit_id, {"decline": True})
    t.join(5)
    assert got["r"] == {"action": "decline"}
