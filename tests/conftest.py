import pytest

from minicode.config import Config, Provider
from minicode.tools.base import ToolContext


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
