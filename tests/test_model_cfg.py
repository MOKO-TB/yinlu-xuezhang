"""模型自带配置(`cfg`):数据通路 + 四个真消费者。

这一族测试的存在理由是 §6 47 那条雷 —— 界面上编辑出来的东西会在
`config` 的归一化里被**静默抹平**(条目被重铸成 `{name, enabled}`)。
所以先钉"存进去还活着",再钉"运行时真的按它走"。
"""
from __future__ import annotations

import copy


from minicode import model_catalog, reasoning
from minicode.config import (Config, Provider, _dedupe_models, _models_from_raw,
                             model_cfg_from_raw)

CFG = {"context_window": 1_000_000, "max_output_tokens": 524288, "vision": False,
       "reasoning_levels": ["low", "high", "max"],
       "reasoning_param": '{ "reasoning_effort": reasoningLevel }'}


def _config(cfg=None, model="mini-max-m3", window=32768) -> Config:
    entry = {"name": model, "enabled": True}
    if cfg:
        entry["cfg"] = copy.deepcopy(cfg)
    return Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                      api_key="k", model=model, models=[entry])],
                  active_provider_id="t", context_window=window)


# --------------------------------------------------------------- 数据通路

def test_cfg_survives_the_whole_normalization_chain():
    """§6 47 那条雷的针:解析 → 去重 → 取回,每一跳都不许把配置吃掉。"""
    raw = [{"name": "a", "cfg": CFG}]
    entries = _dedupe_models(_models_from_raw(raw))
    assert entries[0]["cfg"]["context_window"] == 1_000_000
    assert entries[0]["cfg"]["max_output_tokens"] == 524288
    assert Provider(id="p", name="P", base_url="u", api_key="k", model="a",
                    models=entries).model_entry("A")["cfg"]["vision"] is False


def test_cfg_survives_to_dict_round_trip():
    p = Provider(id="p", name="P", base_url="u", api_key="k", model="a",
                 models=_models_from_raw([{"name": "a", "cfg": CFG}]))
    again = _models_from_raw(p.to_dict()["models"])
    assert again[0]["cfg"] == p.model_entry("a")["cfg"]


def test_unknown_and_bad_values_are_dropped_not_guessed():
    cfg = model_cfg_from_raw({"context_window": "abc", "max_output_tokens": -5,
                              "vision": "yes", "nonsense": 1,
                              "reasoning_levels": ["  ", "low", ""],
                              "reasoning_param": "{}"})
    assert cfg == {"reasoning_levels": ["low"], "reasoning_param": "{}"}


def test_string_levels_are_split_and_int_like_values_accepted():
    cfg = model_cfg_from_raw({"context_window": "1000000",
                              "reasoning_levels": "low, high max"})
    assert cfg["context_window"] == 1000000
    assert cfg["reasoning_levels"] == ["low", "high", "max"]


def test_no_cfg_means_no_change():
    """没声明过的模型必须一个键都不多 —— 否则"配置编辑器"就成了行为改动器。"""
    assert _config(cfg=None).model_cfg() == {}
    entries = _models_from_raw([{"name": "a", "enabled": True}])
    assert "cfg" not in entries[0]


def test_model_entry_matches_case_insensitively():
    c = _config(cfg=CFG)
    assert c.model_cfg("MINI-MAX-M3")["vision"] is False
    assert c.model_cfg("别的模型") == {}


# ------------------------------------------------------------- 消费者一:窗口

def test_declared_window_beats_detection_and_catalog(monkeypatch):
    monkeypatch.setitem(model_catalog._DETECTED, "mini-max-m3",
                        {"context_window": 8192, "supports_vision": True})
    c = _config(cfg=CFG)
    assert model_catalog.window_for(c) == 1_000_000
    assert model_catalog.window_for(_config(cfg=None)) == 8192   # 探测仍兜底


def test_window_falls_back_to_config_default_without_declaration():
    assert model_catalog.window_for(_config(cfg=None, window=4096)) == 4096


# --------------------------------------------------------- 消费者二:视觉闸门

def test_declared_vision_false_hides_a_catalog_multimodal_model(monkeypatch):
    """声明为 False 要能**关掉**目录里说支持的 —— 只许往严不许往松就不是声明。"""
    monkeypatch.setitem(model_catalog.MODEL_CATALOG, "kimi-k3",
                        {"context_window": 1_000_000, "supports_vision": True})
    c = _config(cfg={"vision": False}, model="kimi-k3")
    assert model_catalog.vision_capable(c) is False
    assert model_catalog.vision_capable(_config(cfg=None, model="kimi-k3")) is True


# ----------------------------------------------------- 消费者三:最大输出/档位

def test_reasoning_levels_come_from_the_declaration():
    c = _config(cfg=CFG)
    cap = reasoning.get_capability(c.model, cfg=c.model_cfg())
    assert cap.options == ("default", "low", "high", "max")
    assert cap.strategy == "template"


def test_snapshot_labels_unknown_levels_instead_of_raising():
    """内置那张中文标签表里没有的档位(用户自己写的)要显示原值,不能 KeyError。"""
    c = _config(cfg={"reasoning_levels": ["turbo"]})
    snap = reasoning.reasoning_snapshot(c.model, "turbo", cfg=c.model_cfg())
    assert snap["options"] == [{"value": "default", "label": "默认"},
                               {"value": "turbo", "label": "turbo"}]
    assert snap["disabled"] is False


def test_template_renders_into_extra_body():
    c = _config(cfg=CFG)
    kwargs = {"model": c.model, "messages": []}
    reasoning.apply_reasoning_choice(kwargs, c.model, "high", cfg=c.model_cfg())
    assert kwargs["extra_body"] == {"reasoning_effort": "high"}


def test_default_choice_injects_nothing():
    c = _config(cfg=CFG)
    kwargs = {"model": c.model}
    reasoning.apply_reasoning_choice(kwargs, c.model, "default", cfg=c.model_cfg())
    assert "extra_body" not in kwargs


def test_a_broken_template_sends_nothing_rather_than_a_guess():
    """写错模板的后果应该是"这一档没生效",不是给网关送一个畸形请求体。"""
    c = _config(cfg={"reasoning_levels": ["high"], "reasoning_param": "{oops"})
    kwargs = {}
    reasoning.apply_reasoning_choice(kwargs, c.model, "high", cfg=c.model_cfg())
    assert kwargs == {}


def test_undeclared_model_still_uses_the_builtin_table():
    """没声明的模型:内置表说了算,这一版改动对它是零影响。"""
    cap = reasoning.get_capability("deepseek-v4-flash")
    assert cap.strategy == "thinking_object_effort"
    kwargs = {}
    reasoning.apply_reasoning_choice(kwargs, "deepseek-v4-flash", "off")
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}


def test_stale_choice_falls_back_to_default_with_a_declaration():
    c = _config(cfg=CFG)
    assert reasoning.normalize_choice(c.model, "max", cfg=c.model_cfg()) == "max"
    assert reasoning.normalize_choice(c.model, "ultra", cfg=c.model_cfg()) == "default"


# ---------------------------------------------------------- 桥的载荷那一跳

def test_bridge_save_keeps_cfg(tmp_path, monkeypatch):
    """前端提交 → 落内存这条跳也要保住配置(它走的是同一个 _models_from_raw)。"""
    from minicode.config import Provider as P
    entries = _dedupe_models(_models_from_raw([{"name": "a", "cfg": CFG}]))
    p = P(id="t", name="T", base_url="u", api_key="k", model="a")
    p.models = entries
    p.sync_model()
    assert p.model_entry("a")["cfg"]["reasoning_levels"] == ["low", "high", "max"]
