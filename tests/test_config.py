import json

import pytest

from minicode.config import (Config, Provider, ensure_config, load_config,
                             save_config)


def _write(tmp_path, data, name="config.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_legacy_flat_format(tmp_path):
    p = _write(tmp_path, {"base_url": "https://api.deepseek.com/v1",
                          "api_key": "sk-1", "model": "deepseek-chat"})
    cfg = load_config(p)
    assert len(cfg.providers) == 1
    assert cfg.providers[0].id == "default"
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.model == "deepseek-chat"
    assert cfg.api_key_configured


def test_providers_format_and_switch(tmp_path):
    p = _write(tmp_path, {
        "providers": [
            {"id": "a", "name": "A", "base_url": "https://a/v1", "api_key": "k1", "model": "m1"},
            {"id": "b", "name": "B", "base_url": "https://b/v1", "api_key": "", "model": "m2"},
        ],
        "active_provider": "b",
    })
    cfg = load_config(p)
    assert cfg.provider.id == "b"
    assert cfg.model == "m2" and not cfg.api_key_configured
    assert cfg.set_active_provider("a")
    assert cfg.model == "m1" and cfg.api_key_configured
    assert not cfg.set_active_provider("nope")


def test_provider_id_slug_and_dedup(tmp_path):
    p = _write(tmp_path, {"providers": [
        {"id": "My Provider!", "name": "A", "base_url": "https://a/v1"},
        {"id": "my-provider", "name": "B", "base_url": "https://b/v1"},
    ]})
    cfg = load_config(p)
    assert [x.id for x in cfg.providers] == ["my-provider", "my-provider_2"]


def test_save_roundtrip(tmp_path):
    p = _write(tmp_path, {"providers": [
        {"id": "a", "name": "A", "base_url": "https://a/v1", "api_key": "k", "model": "m"},
    ], "active_provider": "a", "permission_mode": "accept_all"})
    cfg = load_config(p)
    cfg.max_output_chars = 123
    cfg.compact_at_ratio = 0.5
    p2 = tmp_path / "out.json"
    save_config(p2, cfg)

    cfg2 = load_config(p2)
    assert [x.id for x in cfg2.providers] == ["a"]
    assert cfg2.max_output_chars == 123 and cfg2.compact_at_ratio == 0.5
    assert cfg2.permission_mode == "accept_all"


def test_load_config_empty_providers_exits(tmp_path):
    p = _write(tmp_path, {"permission_mode": "confirm"})
    with pytest.raises(SystemExit):
        load_config(p)


def test_active_provider_missing_falls_back(tmp_path):
    p = _write(tmp_path, {"providers": [
        {"id": "a", "name": "A", "base_url": "https://a/v1"},
    ], "active_provider": "ghost"})
    cfg = load_config(p)
    assert cfg.provider.id == "a"


def test_ensure_config_creates_default(tmp_path):
    cfg = ensure_config(path=tmp_path / "config.json",
                        example_path=tmp_path / "no-example.json")
    assert [x.id for x in cfg.providers] == ["zhipu"]
    assert (tmp_path / "config.json").exists()


def test_ensure_config_migrates_legacy_to_providers(tmp_path):
    p = _write(tmp_path, {"base_url": "https://x/v1", "api_key": "k", "model": "m"})
    cfg = ensure_config(path=p, example_path=tmp_path / "no-example.json")
    assert [x.id for x in cfg.providers] == ["default"]
    data = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(data.get("providers"), list)   # 已迁移为新格式


def test_config_direct_construction():
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://t/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t")
    assert cfg.model == "m" and cfg.base_url == "https://t/v1"
