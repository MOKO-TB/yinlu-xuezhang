import json

import pytest

from minicode import config as config_mod
from minicode.config import (BUILTIN_PROVIDER, Config, Provider, ensure_config,
                             load_config, save_config)


@pytest.fixture
def cred(tmp_path, monkeypatch):
    """把凭据文件指到临时目录,避免测试写真实用户主目录。"""
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(config_mod, "CREDENTIALS_PATH", path)
    return path


@pytest.fixture(autouse=True)
def builtin_key(monkeypatch):
    """内置模型 Key 由本地文件注入,仓库里是空值。测试钉成固定值,
    既不依赖开发机上的 config.local.json,也不碰真实 Key。"""
    monkeypatch.setenv("YINLUXS_BUILTIN_API_KEY", "sk-builtin-test")


def _write(tmp_path, data, name="config.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_legacy_flat_format(tmp_path, cred):
    p = _write(tmp_path, {"base_url": "https://api.deepseek.com/v1",
                          "api_key": "sk-1", "model": "deepseek-chat"})
    cfg = load_config(p)
    assert [x.id for x in cfg.providers] == ["gzasc", "default"]   # 旧配置仍被解析保留
    # v0.9.03 起:旧配置里填过 key 的供应商保持激活,不再被静默换成内置模型
    assert cfg.provider.id == "default"
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.model == "deepseek-chat"
    assert cfg.api_key_configured
    # 迁移:内嵌 key 搬进凭据文件,config.json 重写为无密钥
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["providers"][0]["api_key"] == ""
    assert json.loads(cred.read_text(encoding="utf-8"))["default"] == "sk-1"
    # 内置校园模型仍在列表里,可随时切回
    assert cfg.set_active_provider("gzasc")
    assert cfg.api_key == "sk-builtin-test" and cfg.model == BUILTIN_PROVIDER.model


def test_legacy_flat_format_without_key_uses_builtin(tmp_path, cred):
    """旧配置没填 key:仍是原来的默认体验——直接用内置校园模型。"""
    p = _write(tmp_path, {"base_url": "https://api.deepseek.com/v1",
                          "api_key": "", "model": "deepseek-chat"})
    cfg = load_config(p)
    assert cfg.provider.id == "gzasc"
    assert cfg.model == BUILTIN_PROVIDER.model
    assert cfg.api_key_configured            # 内置 key 来自本地注入


def test_providers_format_and_switch(tmp_path, cred):
    p = _write(tmp_path, {
        "providers": [
            {"id": "a", "name": "A", "base_url": "https://a/v1", "api_key": "k1", "model": "m1"},
            {"id": "b", "name": "B", "base_url": "https://b/v1", "api_key": "", "model": "m2"},
        ],
        "active_provider": "b",
    })
    cfg = load_config(p)
    # b 没有可用 key:自动切到内置 gzasc(b 保留在列表里,填 key 后可切回)
    assert cfg.provider.id == "gzasc"
    assert cfg.model == "qwen3.8-27b"
    assert cfg.set_active_provider("b")            # 仍可显式选择
    assert not cfg.api_key_configured
    assert cfg.set_active_provider("a")
    assert cfg.model == "m1" and cfg.api_key_configured
    assert not cfg.set_active_provider("nope")


def test_provider_id_slug_and_dedup(tmp_path, cred):
    p = _write(tmp_path, {"providers": [
        {"id": "My Provider!", "name": "A", "base_url": "https://a/v1"},
        {"id": "my-provider", "name": "B", "base_url": "https://b/v1"},
    ]})
    cfg = load_config(p)
    assert [x.id for x in cfg.providers] == ["gzasc", "my-provider", "my-provider_2"]


def test_save_roundtrip_keys_go_to_credentials(tmp_path, cred):
    p = _write(tmp_path, {"providers": [
        {"id": "a", "name": "A", "base_url": "https://a/v1", "api_key": "k", "model": "m"},
    ], "active_provider": "a", "permission_mode": "accept_all"})
    cfg = load_config(p)
    cfg.max_output_chars = 123
    cfg.compact_at_ratio = 0.5
    p2 = tmp_path / "out.json"
    save_config(p2, cfg)

    # config.json 不含密钥;密钥在凭据文件里
    on_disk = json.loads(p2.read_text(encoding="utf-8"))
    assert on_disk["providers"][0]["api_key"] == ""
    assert json.loads(cred.read_text(encoding="utf-8"))["a"] == "k"

    cfg2 = load_config(p2)
    assert [x.id for x in cfg2.providers] == ["gzasc", "a"]
    # v0.9.03 起:用户自己填过 key 的供应商可以保持激活(添加模型功能)
    assert cfg2.provider.id == "a"
    assert cfg2.api_key == "k"
    assert cfg2.api_key_configured
    assert json.loads(cred.read_text(encoding="utf-8"))["a"] == "k"
    assert "gzasc" not in json.loads(cred.read_text(encoding="utf-8"))  # 内置 key 不进凭据文件
    # 换回内置也是允许的(默认推荐)
    assert cfg2.set_active_provider("gzasc")
    assert cfg2.provider.id == "gzasc"
    assert cfg2.api_key == "sk-builtin-test"        # 内置 key 来自本地注入
    assert cfg2.max_output_chars == 123 and cfg2.compact_at_ratio == 0.5
    assert cfg2.permission_mode == "accept_all"


def test_load_config_empty_providers_falls_back_to_builtin(tmp_path):
    """providers 为空不再报错退出:内置校园模型先补上,应用照常可用。"""
    p = _write(tmp_path, {"permission_mode": "confirm"})
    cfg = load_config(p)
    assert [x.id for x in cfg.providers] == ["gzasc"]
    assert cfg.provider.id == "gzasc"
    assert cfg.model == BUILTIN_PROVIDER.model
    assert cfg.api_key_configured


def test_active_provider_missing_falls_back(tmp_path, cred):
    p = _write(tmp_path, {"providers": [
        {"id": "a", "name": "A", "base_url": "https://a/v1"},
    ], "active_provider": "ghost"})
    cfg = load_config(p)
    assert cfg.provider.id == "gzasc"   # active 缺失时回退到第一个(内置)


def test_ensure_config_creates_default(tmp_path):
    cfg = ensure_config(path=tmp_path / "config.json",
                        example_path=tmp_path / "no-example.json")
    assert [x.id for x in cfg.providers] == ["gzasc", "zhipu"]
    assert cfg.provider.id == "gzasc"   # 无人配置过 key:内置供应商自动启用
    assert (tmp_path / "config.json").exists()


def test_ensure_config_migrates_legacy_to_providers(tmp_path, cred):
    p = _write(tmp_path, {"base_url": "https://x/v1", "api_key": "k", "model": "m"})
    cfg = ensure_config(path=p, example_path=tmp_path / "no-example.json")
    assert [x.id for x in cfg.providers] == ["gzasc", "default"]
    data = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(data.get("providers"), list)
    assert all(x["api_key"] == "" for x in data["providers"])   # key 已迁出
    assert json.loads(cred.read_text(encoding="utf-8"))["default"] == "k"


def test_config_direct_construction():
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://t/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t")
    assert cfg.model == "m" and cfg.base_url == "https://t/v1"


def test_env_override_points_model_at_test_endpoint(tmp_path, cred, monkeypatch):
    """离线测试用的端点覆盖:只改内存,不写回文件、不污染内置模板。"""
    monkeypatch.setenv("YINLUXS_LLM_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("YINLUXS_LLM_MODEL", "fake-model")
    monkeypatch.setenv("YINLUXS_LLM_API_KEY", "test-key")
    p = _write(tmp_path, {"providers": []})

    cfg = load_config(p)
    assert cfg.provider.id == "gzasc"
    assert cfg.base_url == "http://127.0.0.1:9999/v1"
    assert cfg.model == "fake-model"
    assert cfg.api_key == "test-key"
    assert "9999" not in p.read_text(encoding="utf-8")          # 不落盘
    assert BUILTIN_PROVIDER.base_url == "http://10.223.24.16:8080/v1"  # 模板未被改脏

    monkeypatch.delenv("YINLUXS_LLM_BASE_URL")
    cfg2 = load_config(p)
    assert cfg2.base_url == "http://10.223.24.16:8080/v1"       # 不设置就回到内置端点


def test_builtin_provider_not_shared_between_configs(tmp_path, cred):
    """内置供应商以副本形式插入:改一个配置不会影响另一份/模板。"""
    p1 = _write(tmp_path, {"providers": []}, name="a.json")
    p2 = _write(tmp_path, {"providers": []}, name="b.json")
    c1, c2 = load_config(p1), load_config(p2)
    assert c1.provider is not c2.provider
    c1.provider.model = "mutated"
    assert c2.provider.model == BUILTIN_PROVIDER.model
    assert BUILTIN_PROVIDER.model == "qwen3.8-27b"


def test_builtin_provider_key_is_not_hardcoded():
    """内置校园模型的 Key 不得写回源码:仓库是公开的,只能靠本地注入。"""
    assert BUILTIN_PROVIDER.api_key == ""
