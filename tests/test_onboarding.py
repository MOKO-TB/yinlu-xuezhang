"""首次运行向导与数据目录实况(minicode/onboarding.py)。

这里最要紧的不是"弹不弹",而是**写不写得住**:v0.9.22 那轮"配了不保存"
就是数据目录只读时静默吞掉 OSError。所以每条写路径都断言它带回了真实结论。
"""
import pytest

from minicode import onboarding, paths


@pytest.fixture(autouse=True)
def isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


def test_payload_shows_only_when_key_missing():
    p = onboarding.payload(key_configured=False, paths={"app_dir": "D:/x"})
    assert p["show"] is True and len(p["steps"]) == 3
    assert p["data_dir"] == "D:/x"
    assert onboarding.payload(key_configured=True)["show"] is False


def test_dismiss_is_remembered_and_idempotent():
    assert onboarding.payload(key_configured=False)["show"] is True
    assert onboarding.dismiss()["ok"] is True
    assert onboarding.payload(key_configured=False)["show"] is False
    assert onboarding.dismiss()["ok"] is True          # 再点一次不炸、不重复记
    assert onboarding.read()["dismissed"] == [onboarding.WIZARD_KEY]


def test_reset_brings_the_wizard_back():
    onboarding.dismiss()
    assert onboarding.reset()["ok"] is True
    assert onboarding.payload(key_configured=False)["show"] is True


def test_dismiss_reports_writable_failure_instead_of_lying():
    """写不进去必须如实说:否则用户勾了「不再显示」下次还是被弹,却没人知道为什么。"""
    onboarding.state_path().mkdir(parents=True)     # 拿目录占住偏好文件的位置 = 只读盘的效果
    r = onboarding.dismiss()
    assert r["ok"] is False and "写不进去" in r["msg"]
    assert onboarding.payload(key_configured=False)["show"] is True   # 没记住就还会弹


def test_corrupt_preference_is_treated_as_never_dismissed():
    onboarding.state_path().parent.mkdir(parents=True, exist_ok=True)
    onboarding.state_path().write_text("{ not json", encoding="utf-8")
    assert onboarding.read() == {"dismissed": []}
    assert onboarding.payload(key_configured=False)["show"] is True


def test_steps_wording_says_who_does_what():
    titles = [s["title"] for s in onboarding.STEPS]
    assert len(titles) == 3 and titles[0].startswith("①")
    body = " ".join(s["body"] for s in onboarding.STEPS)
    assert "API Key" in body and "工作区" in body
    assert "密码" not in body, "向导不该向人要密码"
    assert onboarding.payload(key_configured=False, paths={"app_dir": "D:/x"})["data_dir"] == "D:/x"


def test_bridge_get_onboarding_uses_config_and_paths(config, tmp_path, monkeypatch):
    """桥上的两个委托:一个组装载荷,一个落"不再显示"。"""
    from types import SimpleNamespace

    from minicode.gui_bridge import Bridge
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    b = SimpleNamespace(config=SimpleNamespace(api_key_configured=False))
    r = Bridge.get_onboarding(b)
    assert r["show"] is True and r["paths"]["app_dir"].endswith("app")
    assert Bridge.dismiss_onboarding(b, "first_run")["ok"] is True
    assert Bridge.get_onboarding(b)["show"] is False

    class Boom:
        @property
        def api_key_configured(self):
            raise RuntimeError("配置读不出来")
    b.config = Boom()
    r2 = Bridge.get_onboarding(b)
    assert r2["show"] is False and "出错" in r2["msg"]   # 读不到也不能把启动卡住
