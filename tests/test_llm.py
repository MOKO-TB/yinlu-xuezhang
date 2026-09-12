from minicode.config import Config, Provider
from minicode.llm import LLMClient, _make_http_client


def _cfg(base_url="https://api.example.com/v1"):
    return Config(providers=[Provider(id="t", name="T", base_url=base_url,
                                      api_key="k", model="m")],
                  active_provider_id="t")


def test_client_constructs():
    assert LLMClient(_cfg()) is not None


def test_loopback_bypasses_proxy():
    # 本地服务必须直连,不能交给系统代理
    assert _make_http_client("http://127.0.0.1:1234/v1", 30) is not None
    assert _make_http_client("http://localhost:1234/v1", 30) is not None
    assert _make_http_client("https://api.deepseek.com/v1", 30) is None
