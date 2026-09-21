"""思考档位:能力表、请求参数编码与非法输入归档。"""
import json

from minicode import reasoning as rz


# ---------------------------------------------------------------- 能力表

def test_deepseek_models_have_effort_capability():
    for name in ("deepseek-v4-flash", "deepseek-v4-flash-0731",
                 "deepseek-v4-pro-0813", "deepseek-flash"):
        cap = rz.get_capability(name)
        assert cap.kind == "effort"
        assert cap.options[0] == "default"
        assert "off" in cap.options
        assert "low" in cap.options
        assert "high" in cap.options


def test_deepseek_v4_pro_has_no_low():
    # 星瑶 Y-Code 同款约定:v4-pro 只到 high,不提供 low 档
    cap = rz.get_capability("deepseek-v4-pro")
    assert "low" not in cap.options
    assert "high" in cap.options and "max" in cap.options


def test_deepseek_reasoner_is_fixed():
    cap = rz.get_capability("deepseek-reasoner")
    assert cap.kind == "fixed"


def test_unknown_model_is_hidden():
    assert rz.get_capability("gpt-4o").kind == "hidden"
    assert rz.get_capability("glm-5.2").kind == "hidden"
    assert rz.get_capability("").kind == "hidden"


def test_gateway_prefix_and_case_are_normalized():
    # 网关路径前缀与大小写都不影响识别(deepseek 前缀规则)
    cap = rz.get_capability("openai/DeepSeek-V4-Flash".lower())
    assert cap.kind == "effort"


# ---------------------------------------------------------------- 快照

def test_snapshot_shape_and_labels():
    snap = rz.reasoning_snapshot("deepseek-v4-flash")
    assert snap["visible"] is True and snap["disabled"] is False
    assert snap["options"][0] == {"value": "default", "label": "默认"}
    assert snap["choice"] == "default"


def test_snapshot_hidden_and_fixed():
    # v0.9.22:未登记的模型不再把按钮藏掉(用户会当成安装包缺功能),
    # 改成"看得见但点不动 + 一句为什么"。
    unknown = rz.reasoning_snapshot("glm-5.2")
    assert unknown["visible"] is True and unknown["disabled"] is True
    assert unknown["kind"] == "hidden" and "未登记" in unknown["hint"]
    assert unknown["options"] == []
    fixed = rz.reasoning_snapshot("deepseek-reasoner")
    assert fixed["visible"] is True and fixed["disabled"] is True
    assert "固定开启" in fixed["hint"]


def test_snapshot_keeps_valid_choice_only():
    assert rz.reasoning_snapshot("deepseek-v4-flash", "high")["choice"] == "high"
    # v4-pro 没有 low:过期值归回 default
    assert rz.reasoning_snapshot("deepseek-v4-pro", "low")["choice"] == "default"
    assert rz.reasoning_snapshot("deepseek-v4-flash", "bogus")["choice"] == "default"


# ---------------------------------------------------------------- 请求参数

def _kwargs_with(choice, model="deepseek-v4-flash"):
    kwargs: dict = {"model": model, "messages": []}
    rz.apply_reasoning_choice(kwargs, model, choice)
    return kwargs


def test_default_injects_nothing():
    kwargs = _kwargs_with("default")
    assert kwargs == {"model": "deepseek-v4-flash", "messages": []}


def test_off_writes_thinking_disabled_and_drops_effort():
    kwargs = _kwargs_with("off")
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in kwargs["extra_body"]


def test_high_writes_thinking_enabled_and_effort():
    kwargs = _kwargs_with("high")
    assert kwargs["extra_body"]["thinking"] == {"type": "enabled"}
    assert kwargs["extra_body"]["reasoning_effort"] == "high"


def test_hidden_model_injects_nothing_even_with_choice():
    kwargs = _kwargs_with("high", model="glm-5.2")
    assert "extra_body" not in kwargs


def test_fixed_model_injects_nothing():
    kwargs = _kwargs_with("high", model="deepseek-reasoner")
    assert "extra_body" not in kwargs


def test_off_after_high_does_not_leave_stale_effort():
    kwargs = {"model": "deepseek-v4-flash", "messages": [],
              "extra_body": {"reasoning_effort": "high"}}
    rz.apply_reasoning_choice(kwargs, "deepseek-v4-flash", "off")
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in kwargs["extra_body"]


# ---------------------------------------------------------------- LLM 集成

def test_llm_client_applies_choice(monkeypatch):
    from minicode.config import Config, Provider
    from minicode.llm import LLMClient
    cfg = Config(providers=[Provider(id="t", name="T",
                                     base_url="https://example.invalid/v1",
                                     api_key="k", model="deepseek-v4-flash")],
                 active_provider_id="t")
    client = LLMClient(cfg)
    assert client.reasoning_choice == "default"

    captured = {}

    class _Stream:
        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter([])

    def fake_create(**kwargs):
        captured.update(kwargs)

        class _R:
            def __iter__(self):
                return iter([])

        return _R()

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    client.reasoning_choice = "high"
    client._stream_once([{"role": "user", "content": "hi"}], [], None, None)
    assert captured["extra_body"]["thinking"] == {"type": "enabled"}
    assert captured["extra_body"]["reasoning_effort"] == "high"

    client.reasoning_choice = "default"
    captured.clear()
    client._stream_once([{"role": "user", "content": "hi"}], [], None, None)
    assert "extra_body" not in captured


# ---------------------------------------------------------------- 权限档位

def test_permission_modes_and_normalize():
    from minicode.permission import PERMISSION_MODES, normalize_mode
    assert PERMISSION_MODES == ("confirm", "accept_edits", "accept_all", "full")
    assert normalize_mode("full") == "full"
    assert normalize_mode("accept_all") == "accept_all"
    assert normalize_mode("bogus") == "confirm"
    assert normalize_mode("") == "confirm"


def _gate(mode, workspace, sandbox=None):
    from minicode.config import Config, Provider
    from minicode.permission import PermissionGate
    cfg = Config(providers=[Provider(id="t", name="T",
                                     base_url="https://example.invalid/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t", permission_mode=mode)
    return PermissionGate(cfg, sandbox=sandbox)


class _Tool:
    def __init__(self, name, read_only=False):
        self.name = name
        self.read_only = read_only


def test_accept_edits_allows_workspace_writes(tmp_path):
    gate = _gate("accept_edits", tmp_path)
    assert gate.check(_Tool("write_file"), {"path": "a.txt"}) == "allow"
    assert gate.check(_Tool("edit_file"), {"path": "b.txt"}) == "allow"


def test_accept_edits_still_asks_for_bash_rm(tmp_path):
    gate = _gate("accept_edits", tmp_path)
    assert gate.check(_Tool("bash"), {"command": "rm -rf docs"}) == "ask"


def test_accept_edits_allows_build_commands(tmp_path):
    gate = _gate("accept_edits", tmp_path)
    assert gate.check(_Tool("bash"),
                      {"command": "python -m pytest -q"}) == "allow"
    assert gate.check(_Tool("bash"),
                      {"command": ".venv/Scripts/python.exe -m pytest"}) == "allow"


def test_accept_edits_asks_for_other_tools(tmp_path):
    gate = _gate("accept_edits", tmp_path)
    assert gate.check(_Tool("web_crawl"), {"url": "https://x"}) == "ask"


def test_full_allows_everything_normal(tmp_path):
    gate = _gate("full", tmp_path)
    assert gate.check(_Tool("write_file"), {"path": "a.txt"}) == "allow"
    assert gate.check(_Tool("bash"), {"command": "rm -rf docs"}) == "allow"


def test_full_still_rejects_disaster(tmp_path):
    gate = _gate("full", tmp_path)
    assert gate.check(_Tool("bash"), {"command": "rm -rf /"}) == "reject"
    assert gate.check(_Tool("bash"), {"command": "format d:"}) == "reject"


def test_confirm_unchanged(tmp_path):
    gate = _gate("confirm", tmp_path)
    assert gate.check(_Tool("write_file"), {"path": "a.txt"}) == "ask"
    assert gate.check(_Tool("read_file", read_only=True),
                      {"path": "a.txt"}) == "allow"


def test_config_loads_new_modes(tmp_path):
    import json as _json
    from minicode.config import _config_from_raw
    raw = {"providers": [], "permission_mode": "accept_edits"}
    assert _config_from_raw(raw).permission_mode == "accept_edits"
    assert _config_from_raw({"permission_mode": "junk"}).permission_mode == "confirm"


# ---------------------------------------------------------------- GUI 桥

def test_bridge_set_permission_mode_and_reasoning(config, tmp_path, monkeypatch):
    import json as _json
    from tests.test_gui_bridge import FakeLLM, make
    llm = FakeLLM([])
    bridge, window, session = make(config, tmp_path, llm)

    r = bridge.set_permission_mode("accept_edits")
    assert r["ok"] and r["permission_mode"] == "accept_edits"
    assert bridge.config.permission_mode == "accept_edits"
    saved = (tmp_path / "config.json").read_text(encoding="utf-8")
    assert _json.loads(saved)["permission_mode"] == "accept_edits"

    assert bridge.set_permission_mode("bogus")["ok"] is False

    # 思考档位:桥上读取快照与切换
    snap = bridge.get_reasoning()
    assert snap["ok"] and snap["visible"] and snap["disabled"] is True   # 测试模型不在能力表
    bad = bridge.set_reasoning_choice("high")
    assert bad["ok"] is False                        # hidden 模型拒绝设置
