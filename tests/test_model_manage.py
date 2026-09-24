"""v0.9.13 模型管理:供应商里的模型清单 + 测试/拉取 + 会话导出与搜索。

关键回归点(都是"改数据模型"最容易踩的):
- 老配置(只有单个 model 字段)行为必须与改版前完全一致;
- model 字段必须始终等于"第一个启用模型",否则发给服务端的模型名会串味;
- 一个模型都没启用时要明确停用 + 回落内置,绝不能把空模型名发出去。
"""
import json
from pathlib import Path

import pytest

from minicode.config import (BUILTIN_PROVIDER_ID, Config, Provider,
                             _dedupe_models, _models_from_raw,
                             _providers_from_raw)
from minicode.gui_bridge import create_app
from minicode.llm import AssistantTurn
from minicode.session import Session, export_filename, search_logs, to_markdown


# ---------------------------------------------------------------- 夹具

# 整文件打 slow:实测 37 s(2026-09-25 --durations=0)—— 慢在真线程/
# 真 socket/真超时,不是算法。日常只跑 `pytest -m "not slow"` 时它会被跳过。
pytestmark = pytest.mark.slow


class FakeLLM:
    def __init__(self, turns=None):
        self.turns = list(turns or [])

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None):
        turn = self.turns.pop(0) if self.turns else AssistantTurn(text="ok")
        if on_text and turn.text:
            on_text(turn.text)
        return turn


class FakeWindow:
    """捕获 evaluate_js 推送的事件;可选地模拟"另存为"对话框。"""

    def __init__(self, save_to=None):
        self.events = []
        self.save_to = save_to

    def evaluate_js(self, code: str):
        prefix = "window.__gcodeEvent("
        assert code.startswith(prefix), code
        self.events.append(json.loads(code[len(prefix):-1]))

    def create_file_dialog(self, *a, **k):
        return [str(self.save_to)] if self.save_to else None

    def of_type(self, type_name):
        return [e for e in self.events if e["type"] == type_name]


def make(config, tmp_path, attach=True, save_to=None):
    """建一个 Bridge;attach=False 模拟"没有窗口"(网页版/无头)。"""
    bridge, ui, _agent, session = create_app(
        config, tmp_path, llm=FakeLLM([AssistantTurn(text="ok")]),
        sessions_dir=tmp_path / "sessions", config_path=tmp_path / "config.json")
    window = None
    if attach:
        window = FakeWindow(save_to=save_to)
        ui.attach(window)
    return bridge, window, session


def save(bridge, items, active="gzasc"):
    return bridge.save_providers(json.dumps(items), active)


def two_model_provider(pid="p", name="P", **kw):
    item = {"id": pid, "name": name, "base_url": f"https://{pid}/v1",
            "api_key": "k-" + pid,
            "models": [{"name": "m1", "enabled": True},
                       {"name": "m2", "enabled": True}]}
    item.update(kw)
    return item


# ---------------------------------------------------------------- 数据模型

def test_legacy_single_model_provider_behaves_as_before():
    """老配置只有 model 字段:按"单条启用项"呈现,一切照旧。"""
    p = Provider(id="p", name="P", base_url="https://p/v1", api_key="k", model="m")
    assert p.models == []
    assert p.model_entries() == [{"name": "m", "enabled": True}]
    assert p.enabled_models() == ["m"]
    assert p.sync_model() == "m"
    assert p.to_dict()["models"] == [{"name": "m", "enabled": True}]


def test_provider_without_model_has_empty_entries():
    p = Provider(id="p", name="P", base_url="https://p/v1", api_key="k")
    assert p.model_entries() == [] and p.enabled_models() == []


def test_sync_model_follows_first_enabled():
    p = Provider(id="p", name="P", base_url="", api_key="", model="a",
                 models=[{"name": "a", "enabled": False},
                         {"name": "b", "enabled": True},
                         {"name": "c", "enabled": True}])
    assert p.sync_model() == "b"


def test_sync_model_keeps_name_when_nothing_enabled():
    """全停用时保留原模型名:抹掉它只会让用户看不懂自己删了什么。"""
    p = Provider(id="p", name="P", base_url="", api_key="", model="a",
                 models=[{"name": "a", "enabled": False}])
    assert p.sync_model() == "a"
    assert p.enabled_models() == []


def test_has_key_ignores_placeholders():
    assert not Provider(id="p", name="P", base_url="", api_key="",
                        model="m").has_key
    assert not Provider(id="p", name="P", base_url="", api_key="YOUR_API_KEY",
                        model="m").has_key
    assert Provider(id="p", name="P", base_url="", api_key="sk-real",
                    model="m").has_key


def test_models_from_raw_is_tolerant():
    """字符串 / 字典 / 垃圾条目混着来都要能吃下去。"""
    entries = _models_from_raw(["m1", {"name": "m2", "enabled": False},
                                {"model": "m3"}, {"nope": 1}, 42, {"name": " "}])
    assert [e["name"] for e in entries] == ["m1", "m2", "m3"]
    assert entries[1]["enabled"] is False
    assert entries[2]["enabled"] is True
    assert _models_from_raw("not-a-list") == []


def test_dedupe_models_ignores_case():
    out = _dedupe_models([{"name": "DeepSeek-Chat", "enabled": True},
                          {"name": "deepseek-chat", "enabled": False},
                          {"name": "b", "enabled": True}])
    assert [m["name"] for m in out] == ["DeepSeek-Chat", "b"]


def test_providers_from_raw_reads_models_and_enabled():
    out = _providers_from_raw({"providers": [
        {"id": "p", "name": "P", "base_url": "https://p/v1", "model": "m1",
         "models": ["m1", {"name": "m2", "enabled": False}], "enabled": False}]})
    assert [m["name"] for m in out[0].models] == ["m1", "m2"]
    assert out[0].models[1]["enabled"] is False
    assert out[0].enabled is False


def test_config_roundtrip_keeps_model_lists(tmp_path, monkeypatch):
    from minicode.config import _config_from_raw, save_config
    monkeypatch.setattr("minicode.config.CREDENTIALS_PATH",
                        tmp_path / "cred.json")
    cfg = Config(providers=[Provider(
        id="p", name="P", base_url="https://p/v1", api_key="k", model="a",
        models=[{"name": "a", "enabled": True}, {"name": "b", "enabled": False}],
        enabled=False)], active_provider_id="p")
    path = tmp_path / "config.json"
    save_config(path, cfg)
    raw = json.loads(path.read_text(encoding="utf-8"))
    p = [x for x in raw["providers"] if x["id"] == "p"][0]
    assert [m["name"] for m in p["models"]] == ["a", "b"]
    assert p["models"][1]["enabled"] is False
    assert p["enabled"] is False
    assert p["api_key"] == ""              # 密钥只进凭据文件,不进 config.json

    back = _config_from_raw(raw)
    restored = [x for x in back.providers if x.id == "p"][0]
    assert [m["name"] for m in restored.models] == ["a", "b"]
    assert restored.model == "a"
    assert restored.enabled is False


# ---------------------------------------------------------------- 保存路径

def test_save_providers_keeps_models_and_syncs_model(config, tmp_path):
    bridge, _window, _ = make(config, tmp_path)
    r = save(bridge, [{"id": "deepseek", "name": "DeepSeek",
                       "base_url": "https://api.deepseek.com/v1",
                       "api_key": "sk-1",
                       "models": [{"name": "deepseek-chat", "enabled": True},
                                  {"name": "deepseek-reasoner", "enabled": False}]}],
             "deepseek")
    assert r["ok"] and r["auto_disabled"] == [] and r["fell_back"] == ""
    assert config.provider.id == "deepseek"
    assert config.model == "deepseek-chat"        # 归位到第一个启用模型
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    p = [x for x in saved["providers"] if x["id"] == "deepseek"][0]
    assert [m["name"] for m in p["models"]] == ["deepseek-chat",
                                                "deepseek-reasoner"]
    assert p["model"] == "deepseek-chat"
    assert p["api_key"] == ""


def test_save_providers_dedupes_same_model_name(config, tmp_path):
    bridge, _window, _ = make(config, tmp_path)
    r = save(bridge, [{"id": "p", "name": "P", "base_url": "https://p/v1",
                       "api_key": "k",
                       "models": [{"name": "m", "enabled": True},
                                  {"name": "M", "enabled": False}]}], "p")
    assert r["ok"]
    assert [m["name"] for m in config.provider.models] == ["m"]


def test_provider_with_no_enabled_model_is_auto_disabled(config, tmp_path):
    """一个模型都没勾:停用它,并回落内置 —— 绝不把空模型名发出去。"""
    bridge, _window, _ = make(config, tmp_path)
    r = save(bridge, [{"id": "p", "name": "P", "base_url": "https://p/v1",
                       "api_key": "k",
                       "models": [{"name": "m", "enabled": False}]}], "p")
    assert r["ok"]
    assert r["auto_disabled"] == ["P"] and r["fell_back"] == "P"
    assert config.provider.id == BUILTIN_PROVIDER_ID


def test_disabled_provider_is_not_offered_or_activatable(config, tmp_path):
    bridge, _window, _ = make(config, tmp_path)
    item = two_model_provider(enabled=False)
    assert save(bridge, [item], "gzasc")["ok"]
    listed = {p["id"]: p for p in bridge.get_providers()["providers"]}
    assert listed["p"]["enabled"] is False

    r = bridge.set_provider("p")
    assert not r["ok"] and "停用" in r["msg"]
    r = bridge.set_session_model("p", "m1")
    assert not r["ok"] and "停用" in r["msg"]
    # 停用的供应商不进任务面板的可切列表
    assert not [x for x in bridge.get_session_metrics()["selectable"]
                if x["id"] == "p"]


def test_save_providers_rejects_bad_input(config, tmp_path):
    bridge, _window, _ = make(config, tmp_path)
    assert not bridge.save_providers("{bad", "x")["ok"]
    r = save(bridge, [{"name": "无地址"}])
    assert not r["ok"] and "base_url" in r["msg"]
    r = save(bridge, [two_model_provider("dup"), two_model_provider("dup")])
    assert not r["ok"] and "重复" in r["msg"]


def test_legacy_frontend_payload_without_models_still_saves(config, tmp_path):
    """老前端只传单个 model 字段:补成清单里的一条,行为与改版前一致。"""
    bridge, _window, _ = make(config, tmp_path)
    r = save(bridge, [{"id": "x", "name": "X", "base_url": "https://x/v1",
                       "api_key": "kx", "model": "mx"}], "x")
    assert r["ok"] and config.model == "mx"
    assert [m["name"] for m in config.provider.models] == ["mx"]


# ---------------------------------------------------------------- 会话级切模型

def test_set_session_model_picks_specific_model(config, tmp_path):
    bridge, _window, _ = make(config, tmp_path)
    assert save(bridge, [two_model_provider()])["ok"]

    assert bridge.set_session_model("p", "m2")["ok"]
    assert bridge.agent.llm.config.model == "m2"
    assert bridge._current.llm_override == {"provider_id": "p", "model": "m2"}

    assert bridge.set_session_model("p", "m1")["ok"]
    assert bridge.agent.llm.config.model == "m1"

    r = bridge.set_session_model("p", "ghost-model")
    assert not r["ok"] and "ghost-model" in r["msg"]


def test_set_session_model_defaults_to_first_enabled(config, tmp_path):
    """不传模型名(老前端):用该供应商的第一个启用模型。"""
    bridge, _window, _ = make(config, tmp_path)
    assert save(bridge, [{"id": "p", "name": "P", "base_url": "https://p/v1",
                          "api_key": "k",
                          "models": [{"name": "off", "enabled": False},
                                     {"name": "on", "enabled": True}]}])["ok"]
    assert bridge.set_session_model("p")["ok"]
    assert bridge.agent.llm.config.model == "on"


def test_session_model_switch_to_global_clears_override(config, tmp_path):
    """只有切回"全局当前的那一对(供应商 + 具体模型)"才算重新跟随全局。"""
    bridge, _window, _ = make(config, tmp_path)
    assert save(bridge, [two_model_provider()], "p")["ok"]
    assert config.model == "m1"
    assert bridge._current.llm_override is None

    # 同一供应商的另一个模型 → 会话级覆盖(不写回全局)
    assert bridge.set_session_model("p", "m2")["ok"]
    assert bridge._current.llm_override == {"provider_id": "p", "model": "m2"}
    assert config.model == "m1"

    # 切回全局那一对 → 覆盖清掉
    assert bridge.set_session_model("p", "m1")["ok"]
    assert bridge._current.llm_override is None

    # 换成另一个供应商 —— 仍是覆盖:全局默认还是 p
    assert bridge.set_session_model("gzasc")["ok"]
    builtin = next(p for p in config.providers if p.id == BUILTIN_PROVIDER_ID)
    assert bridge._current.llm_override == {"provider_id": BUILTIN_PROVIDER_ID,
                                            "model": builtin.model}
    assert config.provider.id == "p"


# ---------------------------------------------------------------- 任务面板

def test_session_metrics_expands_providers_into_models(config, tmp_path):
    bridge, _window, _ = make(config, tmp_path)
    assert save(bridge, [{"id": "p", "name": "P", "base_url": "https://p/v1",
                          "api_key": "k",
                          "models": [{"name": "m1", "enabled": True},
                                     {"name": "m2", "enabled": False},
                                     {"name": "m3", "enabled": True}]}])["ok"]
    m = bridge.get_session_metrics()
    keys = [x["key"] for x in m["selectable"]]
    assert "p::m1" in keys and "p::m3" in keys
    assert "p::m2" not in keys                      # 停用的模型不出现
    assert f"{BUILTIN_PROVIDER_ID}::{config.model}" in keys
    assert m["selected_key"] == f"{config.provider.id}::{config.model}"

    assert bridge.set_session_model("p", "m3")["ok"]
    assert bridge.get_session_metrics()["selected_key"] == "p::m3"


# ---------------------------------------------------------------- 测试 / 拉取

def test_test_model_reports_and_remembers(config, tmp_path, monkeypatch):
    bridge, _window, _ = make(config, tmp_path)
    seen = {}

    def fake_test(base, key, model, timeout=20.0):
        seen.update(base=base, key=key, model=model)
        return {"ok": True, "msg": "已连通 · 窗口 128,000 tokens · 纯文本",
                "status": 200, "context_window": 128000,
                "supports_vision": False}

    monkeypatch.setattr("minicode.model_probe.test_model", fake_test)
    monkeypatch.setattr("minicode.model_probe.remember",
                        lambda model, entry: seen.update(remembered=entry))

    r = bridge.test_model("p", "m1", "https://typed/v1", "typed-key")
    assert r["ok"] and r["model"] == "m1" and r["context_window"] == 128000
    assert seen["base"] == "https://typed/v1" and seen["key"] == "typed-key"
    assert seen["remembered"]["context_window"] == 128000


def test_test_model_surfaces_failure(config, tmp_path, monkeypatch):
    bridge, _window, _ = make(config, tmp_path)
    monkeypatch.setattr("minicode.model_probe.test_model",
                        lambda *a, **k: {"ok": False,
                                         "msg": "API Key 无效或没有权限(HTTP 401)",
                                         "status": 401, "context_window": None,
                                         "supports_vision": None})
    monkeypatch.setattr("minicode.model_probe.remember",
                        lambda *a, **k: pytest.fail("失败不该被记进能力表"))
    r = bridge.test_model("p", "m", "https://p/v1", "bad")
    assert not r["ok"] and "401" in r["msg"]


def test_test_model_needs_a_model_name(config, tmp_path):
    bridge, _window, _ = make(config, tmp_path)
    r = bridge.test_model("p", "  ", "https://p/v1", "k")
    assert not r["ok"] and "模型名" in r["msg"]


def test_test_model_falls_back_to_saved_config(config, tmp_path, monkeypatch):
    """表单没传地址/密钥时,用已保存的那份。"""
    bridge, _window, _ = make(config, tmp_path)
    assert save(bridge, [two_model_provider()])["ok"]
    seen = {}
    monkeypatch.setattr("minicode.model_probe.test_model",
                        lambda base, key, model, timeout=20.0: seen.update(
                            base=base, key=key) or {
                            "ok": True, "msg": "已连通", "status": 200,
                            "context_window": None, "supports_vision": None})
    monkeypatch.setattr("minicode.model_probe.remember", lambda *a, **k: None)
    bridge.test_model("p", "m1", "", "")
    assert seen["base"] == "https://p/v1" and seen["key"] == "k-p"


def test_builtin_credentials_never_take_frontend_values(config, tmp_path,
                                                        monkeypatch):
    """内置供应商的内网地址与密钥不下发,也不可能被前端塞的值顶替。"""
    bridge, _window, _ = make(config, tmp_path)
    seen = {}
    monkeypatch.setattr("minicode.model_probe.test_model",
                        lambda base, key, model, timeout=20.0: seen.update(
                            base=base, key=key) or {
                            "ok": True, "msg": "已连通", "status": 200,
                            "context_window": None, "supports_vision": None})
    monkeypatch.setattr("minicode.model_probe.remember", lambda *a, **k: None)
    bridge.test_model(BUILTIN_PROVIDER_ID, "deepseek-v4-flash",
                      "https://evil/v1", "evil-key")
    assert seen["base"] != "https://evil/v1"
    assert seen["key"] != "evil-key"


def test_fetch_models_lists_service_models(config, tmp_path, monkeypatch):
    bridge, _window, _ = make(config, tmp_path)
    monkeypatch.setattr("minicode.model_probe.list_models",
                        lambda base, key, timeout=15.0: (200, ["a", "b", "c"]))
    r = bridge.fetch_models("p", "https://p/v1", "k")
    assert r["ok"] and r["models"] == ["a", "b", "c"]


def test_fetch_models_needs_address(config, tmp_path):
    bridge, _window, _ = make(config, tmp_path)
    r = bridge.fetch_models("p", "", "")
    assert not r["ok"] and "接口地址" in r["msg"]


def test_fetch_models_explains_failures(config, tmp_path, monkeypatch):
    bridge, _window, _ = make(config, tmp_path)
    monkeypatch.setattr("minicode.model_probe.list_models",
                        lambda base, key, timeout=15.0: (401, []))
    r = bridge.fetch_models("p", "https://p/v1", "bad")
    assert not r["ok"] and "401" in r["msg"]

    monkeypatch.setattr("minicode.model_probe.list_models",
                        lambda base, key, timeout=15.0: (200, []))
    r = bridge.fetch_models("p", "https://p/v1", "k")
    assert not r["ok"] and "/models" in r["msg"]


def test_provider_presets_are_usable(config, tmp_path):
    bridge, _window, _ = make(config, tmp_path)
    r = bridge.get_provider_presets()
    assert r["ok"] and r["presets"]
    for preset in r["presets"]:
        assert preset["label"] and preset["name"]
        assert preset["base_url"].startswith("http")
        assert preset["models"]
    # 预设随 get_providers 一起下发,省一次往返
    assert bridge.get_providers()["presets"] == r["presets"]


def test_get_providers_ships_model_caps(config, tmp_path):
    bridge, _window, _ = make(config, tmp_path)
    assert save(bridge, [two_model_provider()])["ok"]
    item = [x for x in bridge.get_providers()["providers"] if x["id"] == "p"][0]
    assert item["has_key"] is True
    assert {m["name"] for m in item["models"]} == {"m1", "m2"}
    for m in item["models"]:
        assert m["enabled"] is True
        assert "context_window" in m and "supports_vision" in m and "known" in m


# ---------------------------------------------------------------- 会话导出

def test_markdown_export_keeps_dialogue_only():
    session = Session("系统提示词", workspace=Path("D:/ws"))
    session.add_user("三体在哪里")
    session.add_assistant(AssistantTurn(text="在图书馆三楼"))
    session.add_tool_result("c1", "library_search", '{"noise": true}')
    md = to_markdown(session, title="测试会话")
    assert md.startswith("# 测试会话")
    assert "## 我" in md and "三体在哪里" in md
    assert "## 引路学长" in md and "在图书馆三楼" in md
    assert "library_search" not in md      # 工具往返是过程噪音,不进文件
    assert "系统提示词" not in md


def test_export_filename_reuses_log_timestamp(tmp_path):
    session = Session("sys", log_dir=tmp_path, workspace=tmp_path)
    session.add_user("hi")
    name = export_filename(session)
    assert name.startswith("引路学长会话-") and name.endswith(".md")
    stamp = session.log_path.stem.split("_", 1)[1]      # session_YYYYMMDD_HHMM
    assert stamp[:8] in name and stamp[9:13] in name


def test_export_session_without_window_writes_to_workspace(config, tmp_path):
    bridge, _window, session = make(config, tmp_path, attach=False)
    session.add_user("问题")
    session.add_assistant(AssistantTurn(text="回答"))
    r = bridge.export_session()
    assert r["ok"], r
    out = Path(r["path"])
    assert out.parent.name == "exports" and out.parent.parent == tmp_path
    text = out.read_text(encoding="utf-8")
    assert "问题" in text and "回答" in text


def test_export_session_uses_save_dialog(config, tmp_path):
    target = tmp_path / "我的笔记.md"
    bridge, _window, session = make(config, tmp_path, save_to=target)
    session.add_user("问题")
    r = bridge.export_session()
    assert r["ok"] and Path(r["path"]) == target
    assert "问题" in target.read_text(encoding="utf-8")


def test_export_session_cancel_is_not_an_error_toast(config, tmp_path):
    bridge, _window, session = make(config, tmp_path)      # 对话框返回 None = 取消
    session.add_user("问题")
    r = bridge.export_session()
    assert not r["ok"] and r["msg"] == "已取消导出"


def test_export_session_needs_content(config, tmp_path):
    bridge, _window, _session = make(config, tmp_path, attach=False)
    r = bridge.export_session()
    assert not r["ok"] and "没有对话内容" in r["msg"]


# ---------------------------------------------------------------- 会话搜索

def _log(path: Path, entries) -> Path:
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False)
                              for e in entries) + "\n", encoding="utf-8")
    return path


def test_search_logs_counts_user_and_assistant_only(tmp_path):
    _log(tmp_path / "session_20260917_2130.jsonl", [
        {"type": "session_start", "time": "2026-09-17 21:30",
         "workspace": "D:/ws"},
        {"role": "user", "content": "三体在哪里"},
        {"role": "tool", "content": "三体 三体 三体"},
        {"role": "assistant", "content": "三体在图书馆三楼"},
    ])
    out = search_logs(tmp_path, "三体")
    assert len(out) == 1
    assert out[0]["hits"] == 2              # 工具结果不算
    assert "三体" in out[0]["snippet"]
    assert search_logs(tmp_path, "") == []
    assert search_logs(tmp_path, "不存在的词") == []


def test_search_logs_is_case_insensitive(tmp_path):
    _log(tmp_path / "session_20260917_2200.jsonl",
         [{"role": "user", "content": "DeepSeek 怎么样"}])
    assert search_logs(tmp_path, "deepseek")
    assert search_logs(tmp_path, "DEEPSEEK")


def test_search_sessions_maps_to_sidebar_index(config, tmp_path):
    bridge, _window, session = make(config, tmp_path)
    session.add_user("三体这本书在哪个楼层")
    r = bridge.search_sessions("三体")
    assert r["ok"] and r["total"] == 1
    hit = r["results"][0]
    assert hit["index"] == 1 and hit["hits"] == 1
    assert "三体" in hit["snippet"]
    assert bridge.resume_session(hit["index"])["ok"]        # 序号能直接切过去


def test_search_sessions_empty_query_returns_nothing(config, tmp_path):
    bridge, _window, session = make(config, tmp_path)
    session.add_user("三体")
    r = bridge.search_sessions("   ")
    assert r["ok"] and r["total"] == 0 and r["results"] == []


def test_search_sessions_reports_no_match(config, tmp_path):
    bridge, _window, session = make(config, tmp_path)
    session.add_user("三体")
    r = bridge.search_sessions("量子力学")
    assert r["ok"] and r["total"] == 0
