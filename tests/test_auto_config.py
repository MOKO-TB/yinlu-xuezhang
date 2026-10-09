# -*- coding: utf-8 -*-
"""「智能配置」开关(v0.9.142):一份声明要么整体让位,要么整体生效。

为什么是**整份**而不是逐键:混着读会造出一个没人能解释的模型 —— 窗口按内置表、
`max_tokens` 按半年前手填的 384000、推理档位按抄来的一串模板。用户点开关时想要的是
"别让我管这些",不是"帮我挑一半"。

这一族钉的是四个真消费者(与 `tests/test_model_cfg.py` 同一套理由:界面上编辑出来
的东西必须真的走到请求里,否则这个开关就是个样子货)。
"""
from __future__ import annotations

import copy
import types

import pytest

from minicode import model_catalog
from minicode.config import (Config, Provider, model_cfg_from_raw)

DECL = {"context_window": 1_000_000, "max_output_tokens": 524288, "vision": True,
        "reasoning_levels": ["low", "high", "max"],
        "reasoning_param": '{ "reasoning_effort": reasoningLevel }'}


def _config(extra=None, model="deepseek-v4-flash", window=32768) -> Config:
    cfg = copy.deepcopy(DECL)
    cfg.update(extra or {})
    return Config(
        providers=[Provider(id="t", name="T", base_url="https://x.invalid/v1",
                            api_key="k", model=model,
                            models=[{"name": model, "enabled": True, "cfg": cfg}])],
        active_provider_id="t", context_window=window)


# ---------------------------------------------------------------- 数据通路

def test_the_flag_survives_parsing_and_the_round_trip():
    got = model_cfg_from_raw(dict(DECL, auto_config=True))
    assert got["auto_config"] is True
    entry = _config({"auto_config": True}).provider.models[0]
    assert entry["cfg"]["auto_config"] is True
    again = Provider(id="p", name="P", base_url="u", api_key="k", model="a",
                     models=[copy.deepcopy(entry)]).to_dict()["models"]
    assert again[0]["cfg"]["auto_config"] is True


@pytest.mark.parametrize("bad", ["yes", 1, 0, None, []])
def test_a_non_bool_flag_is_dropped_not_guessed(bad):
    """`"auto_config": "yes"` 不该被读成开 —— 认不准就当没写,与整份清洗同口径。"""
    assert "auto_config" not in model_cfg_from_raw({"auto_config": bad})


# ---------------------------------------------------------------- 让位的是整份

def test_on_empties_the_declaration_but_keeps_it():
    """开着 → 运行时读到空;关掉 → 逐字回到用户填的那一份。"""
    cfg = _config({"auto_config": True})
    assert cfg.effective_model_cfg() == {}
    assert cfg.model_cfg()["context_window"] == 1_000_000   # 声明没被删
    cfg.provider.models[0]["cfg"]["auto_config"] = False
    assert cfg.effective_model_cfg()["max_output_tokens"] == 524288


def test_off_is_byte_for_byte_today():
    plain = _config()
    assert plain.effective_model_cfg() == plain.model_cfg()


# ---------------------------------------------------------------- 四个真消费者

def test_window_ruler_follows_the_switch(monkeypatch):
    """压缩那把尺子:开着时声明的 100 万不算数,退回探测/目录/配置默认。"""
    monkeypatch.setattr(model_catalog, "_DETECTED", {})
    on = _config({"auto_config": True})
    off = _config()
    assert model_catalog.window_for(off, "deepseek-v4-flash") == 1_000_000
    assert model_catalog.window_for(on, "deepseek-v4-flash") != 1_000_000


def test_vision_follows_the_switch(monkeypatch):
    monkeypatch.setattr(model_catalog, "_DETECTED", {})
    assert model_catalog.vision_capable(_config(), "deepseek-v4-flash") is True
    on = _config({"auto_config": True})
    assert model_catalog.vision_capable(on, "deepseek-v4-flash") is False


def test_reasoning_levels_follow_the_switch():
    """开着 → 内置表说了算;关着 → 用户登记的那三档说了算。

    挑 `kimi-k3` 是因为它内置表里是 `hidden`(一个档位都不给):这样"声明里那三档
    还在不在"就只有一个解释 —— 换成内置表本来就有的模型,这条针红了也说不清为什么。
    """
    from minicode.reasoning import get_capability
    off = _config(model="kimi-k3")
    assert "max" in get_capability("kimi-k3", off.effective_model_cfg()).options
    on = _config({"auto_config": True}, model="kimi-k3")
    cap = get_capability("kimi-k3", on.effective_model_cfg())
    assert "max" not in cap.options and cap.kind == "hidden"


def test_request_body_loses_max_tokens_when_it_is_on(monkeypatch):
    """这一条钉的是"开关不是样子货":开着就不该往请求里塞 max_tokens。

    只测配置层会漏掉这一路 —— 界面上灰掉了、`to_dict` 里带着、请求照发旧数,
    三处都"看起来对"。
    """
    from minicode.llm import LLMClient
    seen = {}

    def fake_create(**kwargs):
        seen.update(kwargs)

        def gen():
            return iter(())
        return gen()

    for cfg, expect in ((_config({"auto_config": True}), None), (_config(), 524288)):
        seen.clear()
        client = LLMClient(cfg)
        monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
        monkeypatch.setattr("minicode.llm.time",
                            types.SimpleNamespace(perf_counter=__import__("time").perf_counter,
                                                  sleep=lambda _s: None))
        client.stream_chat([{"role": "user", "content": "hi"}], [])
        assert seen.get("max_tokens") == expect, seen.keys()


# ---------------------------------------------------------------- 那一屏的数

def test_bridge_reports_what_it_actually_knows(tmp_path):
    """已知模型给数,未知模型给"不知道" —— 不许凭空编一个窗口。"""
    from minicode.gui_bridge import Bridge
    from minicode.ui import TerminalUI
    cfg = _config({"auto_config": True})
    bridge = Bridge(cfg, tmp_path, TerminalUI(cfg, tmp_path))
    known = bridge.model_auto_info("kimi-k3")
    assert known["ok"] and known["known"] and known["window"] > 0
    assert known["max_out"] == 0, "输出上限没有可解析的来源:那是『交给服务端默认』"
    unknown = bridge.model_auto_info("not-a-real-model-xyz")
    assert unknown["known"] is False and unknown["window"] == 0


def test_the_facade_exposes_it():
    """前端只经门面拿方法:漏了这一条,那一屏永远停在「正在问这台机器知道些什么…」。"""
    from minicode.gui_bridge import PublicApi
    assert callable(getattr(PublicApi, "model_auto_info", None))
