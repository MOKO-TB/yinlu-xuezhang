import pytest

from minicode import config as config_mod
from minicode import gui_bridge as gui_bridge_mod
from minicode import model_catalog as model_catalog_mod
from minicode import model_probe as model_probe_mod
from minicode.config import Config, Provider
from minicode.tools.base import ToolContext


@pytest.fixture(autouse=True)
def _isolated_credentials(tmp_path, monkeypatch):
    """所有测试一律不碰真实用户主目录的凭据文件。

    save_config/ensure_config 会把 provider 的 key 写进
    ~/.yinluxs/credentials.json,而 GUI 桥接层的用例(如
    test_set_sandbox_toggles_and_persists)会顺手触发它。做成 autouse,
    就不必指望每个用例自己记得 monkeypatch。
    """
    monkeypatch.setattr(config_mod, "CREDENTIALS_PATH",
                        tmp_path / "credentials.json")


@pytest.fixture(autouse=True)
def _isolated_workspace_state(tmp_path, monkeypatch):
    """工作区记忆(workspaces.json)默认落在程序目录 —— 与凭据文件同一个坑。

    Bridge 的 app_dir 默认取 APP_DIR,测试里若不重定向,切工作区的用例会把
    workspaces.json 写进仓库根。钉到临时目录后,测试怎么切都不污染工作区。
    """
    monkeypatch.setattr(gui_bridge_mod, "APP_DIR", tmp_path / "app-dir")


@pytest.fixture(autouse=True)
def _no_live_model_probe(tmp_path, monkeypatch):
    """不让测试去问真实的模型服务。

    Bridge/CLI 启动都会调 model_probe.ensure_async(),测试里那是个
    example.invalid 之类的假地址:真跑会起一堆后台线程去发 DNS/HTTP,既慢又
    可能把 model_caps.json 写进真实程序目录。这里钉住缓存路径,并把真正的
    网络入口 detect() 换成"什么也没问到" —— ensure_async 的调度逻辑照常
    执行(要测的就是它),但不会联网、也不会落盘。测探测本身的用例直接调
    probe_* / detect,它们只依赖传入参数。
    """
    monkeypatch.setattr(model_probe_mod, "_cache_path",
                        lambda: tmp_path / "model_caps.json")
    monkeypatch.setattr(model_probe_mod, "detect", lambda *a, **k: {
        "context_window": None, "supports_vision": None,
        "endpoint": "", "checked_at": 0.0})
    model_probe_mod.reset()


@pytest.fixture(autouse=True)
def _isolated_model_catalog():
    """探测结论是模块级全局状态:每个用例前后都清干净,免得互相串。"""
    model_catalog_mod.clear_detected()
    yield
    model_catalog_mod.clear_detected()


@pytest.fixture
def config():
    return Config(
        providers=[Provider(id="test", name="Test",
                            base_url="https://example.invalid/v1",
                            api_key="test-key", model="test-model")],
        active_provider_id="test",
    )


@pytest.fixture
def ctx(tmp_path, config):
    return ToolContext(workspace=tmp_path, config=config)
