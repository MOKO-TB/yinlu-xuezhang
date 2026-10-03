# -*- coding: utf-8 -*-
"""按档位分模型(v0.9.95):`config.subagent_models` = {"explore": 便宜档, "code": 硬档}。

要钉住的假做法有三条,逐条对着写:
1. **换不出去必须说出来**。名字不在当前供应商的启用模型里就退回主模型,并把"为什么退"
   写进报告抬头/合并报告 —— 静默换个模型跑,表现是"这一路结论变粗了",没人查得动。
2. **只换模型不换供应商**。`LLMClient` 认的是 `config.model` 加活动供应商那一把 Key,
   构造规则收在 `LLMClient.scoped` 一处;两处各抄一遍,总有一天漏带 `active_provider_id`
   (表现是 401,看着像 Key 坏了)。
3. **断言要看子代理真用的是哪个客户端**。抬头写"模型 X"而实际跑在主模型上,这条就废了;
   所以每一路给一个自带身份的 spy,报告正文出自 spy 才算数。
"""
from __future__ import annotations

import json

import pytest

from minicode.config import Config, Provider, _config_from_raw, save_config
from minicode.llm import AssistantTurn, LLMClient, ToolCall
from minicode.subagents import _merge_reports, model_choice, type_spec
from test_gui_bridge import make, wait_until
from test_subagent import (RoutingLLM, _accept_all, _final, _is_subagent,
                           _tool_result)


def _cfg(models: list[dict] | None = None, table: dict | None = None,
         base: str = "test-model") -> Config:
    """一条供应商:主模型 `base`,可选模型清单 `models`,档位覆盖表 `table`。"""
    return Config(
        providers=[Provider(id="test", name="Test",
                            base_url="https://example.invalid/v1",
                            api_key="test-key", model=base,
                            models=models if models is not None else
                            [{"name": base, "enabled": True}])],
        active_provider_id="test",
        subagent_models=table or {},
    )


# ---------------------------------------------------------------- 该用哪个模型


def test_no_override_is_not_a_change():
    assert model_choice("explore", _cfg()) == ("", "")


def test_override_equal_to_main_model_is_not_a_change():
    """写来写去是同一个模型就别在报告里喊"本档模型" —— 那是一条没信息的噪音。"""
    assert model_choice("explore", _cfg(table={"explore": "test-model"})) == ("", "")


def test_valid_override_returns_the_model_and_a_note():
    cfg = _cfg(models=[{"name": "test-model", "enabled": True},
                        {"name": "cheap-fast", "enabled": True}],
              table={"explore": "cheap-fast"})
    assert model_choice("explore", cfg) == ("cheap-fast", "本档模型 cheap-fast")


def test_unknown_model_falls_back_and_says_why():
    """别的供应商的模型名 / 打错的字:退回主模型,并把原因交出去。"""
    model, note = model_choice("code", _cfg(table={"code": "gpt-9-turbo"}))
    assert model == ""
    assert "gpt-9-turbo" in note and "不在当前供应商的启用模型里" in note
    assert "test-model" in note                      # 说清这一档实际仍用什么在跑


def test_disabled_model_counts_as_unknown():
    """界面上勾掉了的模型不算"能用" —— 拿它跑必然 401,不如退回并说明。"""
    cfg = _cfg(models=[{"name": "test-model", "enabled": True},
                       {"name": "cheap-fast", "enabled": False}],
              table={"review": "cheap-fast"})
    model, note = model_choice("review", cfg)
    assert model == "" and "cheap-fast" in note


def test_blank_table_entry_is_ignored():
    assert model_choice("explore", _cfg(table={"explore": "   "})) == ("", "")


# ---------------------------------------------------------------- 配置读得进来


def test_config_parses_subagent_models():
    cfg = _config_from_raw({"subagent_models": {"explore": " cheap-fast ",
                                                "code": "", 7: "x"}})
    assert cfg.subagent_models == {"explore": "cheap-fast", "7": "x"}


def test_config_survives_garbage_subagent_models():
    """这份要拿去查表:写成列表/字符串/数字都不能崩,大不了不生效。"""
    for junk in (["explore"], "explore", 42, None):
        assert _config_from_raw({"subagent_models": junk}).subagent_models == {}


def test_saved_config_keeps_the_table_visible(tmp_path):
    """表要能在 config.json 里看得见:看不见就会被当成"写了没生效"。"""
    save_config(tmp_path / "config.json", _cfg(table={"explore": "cheap-fast"}))
    raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert raw["subagent_models"] == {"explore": "cheap-fast"}


# ---------------------------------------------------------------- 客户端怎么建


def test_scoped_replaces_only_the_model():
    """Key/域名还是活动供应商那一套:换模型不换供应商,跨供应商走界面那条路。"""
    cfg = _cfg()
    client = LLMClient.scoped(cfg, model="cheap-fast")
    assert client.config.model == "cheap-fast"
    assert client.config.api_key == "test-key"
    assert client.config.base_url == cfg.base_url
    assert client.config.active_provider_id == "test"
    assert client.reasoning_choice == "default"      # 档位能力按模型算,不继承
    assert cfg.provider.model == "test-model"        # 传进来的配置没被改脏


def test_scoped_switches_provider_without_a_model():
    cfg = _cfg()
    cfg.providers.append(Provider(id="b", name="B", api_key="key-b",
                                  base_url="https://other.invalid/v1",
                                  model="b-model"))
    client = LLMClient.scoped(cfg, provider_id="b")
    assert client.config.api_key == "key-b"
    assert client.config.model == "b-model"


def test_scoped_replaces_model_on_the_target_provider_only():
    cfg = _cfg()
    cfg.providers.append(Provider(id="b", name="B", api_key="key-b",
                                  base_url="https://other.invalid/v1",
                                  model="b-model"))
    client = LLMClient.scoped(cfg, provider_id="b", model="b-strong")
    assert client.config.model == "b-strong"
    mine = next(p for p in client.config.providers if p.id == "test")
    assert mine.model == "test-model"               # 另一家不该被顺手改名


# ---------------------------------------------------------------- 派活真用了它


class SpyLLM:
    """带身份的假客户端:报告正文署自己的名,被调用就记一笔。"""

    def __init__(self, tag: str):
        self.tag = tag
        self.calls = 0

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None, **kwargs):
        self.calls += 1
        turn = AssistantTurn(text=f"{self.tag}这一路的报告")
        if on_text and turn.text:
            on_text(turn.text)
        return turn


def _call(name, args, call_id="c1"):
    return ToolCall(id=call_id, name=name, arguments=json.dumps(args))


def _patch_scoped(monkeypatch, created: list, spy: SpyLLM | None = None):
    """让 `LLMClient.scoped` 造 spy 并登记(模型名, 客户端),不发真请求。"""
    def fake_scoped(cls, config, provider_id="", model=""):
        got = spy or SpyLLM(model or "没带模型名")
        created.append((model, got))
        return got

    monkeypatch.setattr(LLMClient, "scoped", classmethod(fake_scoped))


def _enable(config, *names: str) -> None:
    """把这几个模型名加成"当前供应商的启用模型"。"""
    base = config.model
    config.provider.models = [{"name": base, "enabled": True}] + [
        {"name": n, "enabled": True} for n in names]


def _turn_for(task_turn, sub_reply: str):
    """按会话身份分派:主代理给脚本,子代理(共享主模型时)给一句收尾。

    并行批次跑真线程,共享队列会把两路脚本串台(test_subagent.py 的规矩),
    所以这里按 system prompt 认人 —— 只有真跑在主模型上的那一路会走到 sub_reply。
    """
    def route(messages):
        if _is_subagent(messages):
            return _final(sub_reply)
        if any(m.get("role") == "tool" for m in messages):
            return _final("主代理收尾")
        return task_turn
    return route


def _run(config, tmp_path, task_turn, sub_reply="主模型跑的报告"):
    """跑一轮真派发,返回(工具结果文本, 推给界面的事件)。"""
    main = RoutingLLM(_turn_for(task_turn, sub_reply))
    bridge, window, session = make(config, tmp_path, main)
    bridge.send_task("派活")
    assert wait_until(lambda: not bridge._busy, timeout=25)
    return _tool_result(session), window.events


_SINGLE = AssistantTurn(tool_calls=[
    _call("task", {"type": "explore", "task": "看看工作区有什么"}, "t1")])
_BATCH = AssistantTurn(tool_calls=[
    _call("task", {"type": ["explore", "review"],
                   "task": ["找登录代码", "审权限模块"]}, "b1")])


@pytest.mark.slow
def test_single_dispatch_runs_on_the_bucket_model(config, tmp_path, monkeypatch):
    _accept_all(config)
    _enable(config, "cheap-fast")
    config.subagent_models = {"explore": "cheap-fast"}
    created: list = []
    _patch_scoped(monkeypatch, created)

    result, _events = _run(config, tmp_path, _SINGLE, sub_reply="这路跑错了客户端")

    assert "cheap-fast这一路的报告" in result          # 正文出自 spy,不是主模型跑的
    assert "本档模型 cheap-fast" in result             # 抬头说了这一档用哪个在跑
    assert "这路跑错了客户端" not in result
    assert [m for m, _s in created] == ["cheap-fast"]
    assert created[0][1].calls >= 1


@pytest.mark.slow
def test_single_dispatch_without_override_builds_no_extra_client(config, tmp_path,
                                                                 monkeypatch):
    """没配档位就一个客户端都不许多建 —— 否则每次派活都新开一条连接。"""
    _accept_all(config)
    created: list = []
    _patch_scoped(monkeypatch, created)

    result, _events = _run(config, tmp_path, _SINGLE, sub_reply="侦察报告:1 个文件")

    assert created == []
    assert "侦察报告:1 个文件" in result               # 仍跑在共享的主模型上
    assert "模型" not in result                        # 没换就不喊


@pytest.mark.slow
def test_unknown_model_is_not_a_silent_fallback(config, tmp_path, monkeypatch):
    """名字不在启用模型里:照旧用主模型跑,但原因必须出现在抬头。"""
    _accept_all(config)
    config.subagent_models = {"explore": "别的供应商的模型"}
    created: list = []
    _patch_scoped(monkeypatch, created)

    result, _events = _run(config, tmp_path, _SINGLE, sub_reply="侦察报告:1 个文件")

    assert "别的供应商的模型" in result
    assert "不在当前供应商的启用模型里" in result
    assert created == []                               # 退回就是真的没另建客户端


@pytest.mark.slow
def test_batch_routes_use_their_own_models(config, tmp_path, monkeypatch):
    """两路两个模型:合并报告每路小标题带上自己的模型,正文各自出自自己的 spy。"""
    _accept_all(config)
    _enable(config, "cheap-fast", "strong-slow")
    config.subagent_models = {"explore": "cheap-fast", "review": "strong-slow"}
    spies = {"cheap-fast": SpyLLM("cheap-fast"), "strong-slow": SpyLLM("strong-slow")}
    created: list = []

    def fake_scoped(cls, config, provider_id="", model=""):
        got = spies[model]
        created.append((model, got))
        return got

    monkeypatch.setattr(LLMClient, "scoped", classmethod(fake_scoped))

    result, _events = _run(config, tmp_path, _BATCH, sub_reply="这路不该由主模型跑")

    assert "cheap-fast这一路的报告" in result
    assert "strong-slow这一路的报告" in result
    assert "## 1. explore · 模型 cheap-fast" in result
    assert "## 2. review · 模型 strong-slow" in result
    assert sorted(m for m, _s in created) == ["cheap-fast", "strong-slow"]
    assert "这路不该由主模型跑" not in result


@pytest.mark.slow
def test_batch_clients_are_reused_per_model(config, tmp_path, monkeypatch):
    """两个档位同一个模型只建一个客户端:一个模型名一条连接,不该每路新建。"""
    _accept_all(config)
    _enable(config, "cheap-fast")
    config.subagent_models = {"explore": "cheap-fast", "review": "cheap-fast"}
    created: list = []
    _patch_scoped(monkeypatch, created)

    _run(config, tmp_path, _BATCH, sub_reply="这路不该由主模型跑")

    assert len(created) == 1                           # 两路同一个模型 → 只建一次
    assert created[0][0] == "cheap-fast"


@pytest.mark.slow
def test_batch_says_which_route_could_not_switch(config, tmp_path, monkeypatch):
    """一路换成功、一路换不出去:退掉那一路要单独说,不能只夸成功的那路。"""
    _accept_all(config)
    _enable(config, "cheap-fast")
    config.subagent_models = {"explore": "cheap-fast", "review": "不存在的模型"}
    spy = SpyLLM("cheap-fast")
    created: list = []
    _patch_scoped(monkeypatch, created, spy=spy)

    result, _events = _run(config, tmp_path, _BATCH, sub_reply="审查报告:没问题")

    assert "第2路[review]仍用当前模型" in result
    assert "不存在的模型" in result
    assert "## 1. explore · 模型 cheap-fast" in result
    assert "## 2. review ·" in result                  # 退回那一路不硬贴模型标签
    assert "审查报告:没问题" in result                 # 它确实跑在主模型上
    assert "cheap-fast这一路的报告" in result
    assert spy.calls >= 1


@pytest.mark.slow
def test_batch_notice_reaches_the_ui_with_the_models_it_claims(config, tmp_path,
                                                              monkeypatch):
    """「已并行派出 N 路」这条提示要真到界面,并且上面写的模型就是这一路跑的。

    本轮第一版一度把拼这段字符串写在 `try: … except Exception: pass` 里,一处写错
    (`specs` 的元素是三元组不是 spec)就被那句"CLI 没有 push 也不碍事"的兜底吞掉,
    于是**所有并行批次**的开始提示一起消失,界面上什么都没发生而代码看着正常。
    """
    _accept_all(config)
    _enable(config, "cheap-fast")
    config.subagent_models = {"explore": "cheap-fast"}
    _patch_scoped(monkeypatch, [])

    _result, events = _run(config, tmp_path, _BATCH, sub_reply="审查报告:没问题")

    notices = [e["text"] for e in events
               if e.get("type") == "info" and "已并行派出" in str(e.get("text"))]
    assert len(notices) == 1, f"开始提示没到界面:{events}"
    assert "2 路" in notices[0]
    assert "explore→cheap-fast" in notices[0]           # 换成功的那一路带模型名
    assert "review→" not in notices[0]                   # 没换的不能带上模型名


@pytest.mark.slow
def test_plan_note_records_the_model(config, tmp_path, monkeypatch):
    """计划备注里要留得下"这一路是哪个模型跑的",否则事后无从解释结论粗细。"""
    from minicode import planning
    from minicode.tools.base import ToolContext
    _accept_all(config)
    _enable(config, "cheap-fast")
    config.subagent_models = {"explore": "cheap-fast"}
    planning.plan(ToolContext(workspace=tmp_path, config=None), "write",
                  goal="把登录做完", steps=[{"title": "看现有代码"},
                                            {"title": "改后端"}])
    created: list = []
    _patch_scoped(monkeypatch, created)

    _run(config, tmp_path, AssistantTurn(tool_calls=[
        _call("task", {"type": "explore", "task": "看看登录相关代码", "step": 2},
              "t1")]))

    text = (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8")
    assert "子代理已回:explore 报告" in text
    assert "(模型 cheap-fast)" in text


# ---------------------------------------------------------------- 合并报告的口径


def _spec(key):
    spec = type_spec(key, None)
    assert spec is not None
    return spec


def test_merge_reports_tags_only_the_routes_that_switched():
    pairs = [(_spec("explore"), "看代码", "A"), (_spec("review"), "审代码", "B")]
    out = _merge_reports(pairs, ["模型 cheap-fast", ""])
    assert "## 1. explore · 模型 cheap-fast · 看代码" in out
    assert "## 2. review · 审代码" in out               # 没换的那路不硬凑一句
    assert out.startswith("[并行子代理完成 · 2 个]")


def test_merge_reports_without_models_is_unchanged():
    out = _merge_reports([(_spec("explore"), "看代码", "A")])
    assert "## 1. explore · 看代码" in out
    assert "模型" not in out


@pytest.mark.parametrize("junk", [None, [], ["x"], ["a", "b"]])
def test_merge_reports_tolerates_a_mismatched_model_list(junk):
    """`models` 与 `pairs` 不同序或缺失都不许 IndexError:合并是最后一步,
    在这里崩掉就把已经跑完的几路全丢了。"""
    out = _merge_reports([(_spec("explore"), "看代码", "A")], junk)
    assert "## 1. explore" in out
