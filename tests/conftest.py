import pytest

from minicode import config as config_mod
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
