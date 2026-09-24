"""`minicode/llm.py` 的两条边界:代理绕行,以及**吐过字就不再重试**(#85 撞出来的真 bug)。"""
import time as _time
import types

import httpx
import pytest
from openai import APIConnectionError

from minicode.config import Config, Provider
from minicode.llm import MAX_RETRIES, LLMClient, _make_http_client


def _cfg(base_url="https://api.example.com/v1"):
    return Config(providers=[Provider(id="t", name="T", base_url=base_url,
                                      api_key="k", model="m")],
                  active_provider_id="t")


def test_client_constructs():
    assert LLMClient(_cfg()) is not None


def test_loopback_bypasses_proxy():
    # 本地/内网服务必须直连,不能交给系统代理
    assert _make_http_client("http://127.0.0.1:1234/v1", 30) is not None
    assert _make_http_client("http://localhost:1234/v1", 30) is not None
    assert _make_http_client("http://10.223.24.16:8080/v1", 30) is not None
    assert _make_http_client("https://api.deepseek.com/v1", 30) is None


# ---------------- 重试:已经吐给用户的字不能再来一遍 ----------------

def _chunk(text: str):
    delta = types.SimpleNamespace(content=text, reasoning_content=None,
                                  reasoning=None, tool_calls=None)
    return types.SimpleNamespace(usage=None,
                                 choices=[types.SimpleNamespace(delta=delta)])


def _client_that_dies_after(monkeypatch, chunks: list):
    """假客户端:先吐 `chunks` 里那几个分片,然后抛一个**可重试**的网络错误。"""
    client = LLMClient(_cfg())
    calls = {"n": 0}

    def fake_create(**_kwargs):
        calls["n"] += 1

        def gen():
            yield from chunks
            raise APIConnectionError(
                request=httpx.Request("POST", "https://api.example.com/v1/chat"))
        return gen()

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    # 退避是 2**attempt 秒,测试里不真睡(只留 perf_counter 给计时那两行)
    monkeypatch.setattr("minicode.llm.time",
                        types.SimpleNamespace(perf_counter=_time.perf_counter,
                                              sleep=lambda _s: None))
    return client, calls


def test_no_retry_once_a_chunk_reached_the_user(monkeypatch):
    """半截答案已经流到界面上了,再重试一次用户会看到两份答案叠着。

    这条口径原来只写在一行注释里(`received_any` 记了却谁都没读),ruff 的 F841
    把它撞出来 —— 注释承诺的行为没实现,比没写注释更坏。
    """
    client, calls = _client_that_dies_after(monkeypatch, [_chunk("半截答")])
    seen = []
    with pytest.raises(APIConnectionError):
        client.stream_chat([{"role": "user", "content": "hi"}], [],
                           on_text=seen.append)
    assert calls["n"] == 1, "吐过字还重试 = 用户看到重复输出"
    assert seen == ["半截答"]


def test_retry_still_happens_when_nothing_was_emitted(monkeypatch):
    """一个字都没吐出来时,重试照旧(否则网络抖一下整轮就废了)。"""
    client, calls = _client_that_dies_after(monkeypatch, [])
    retries = []
    with pytest.raises(APIConnectionError):
        client.stream_chat([{"role": "user", "content": "hi"}], [],
                           on_retry=lambda e, n: retries.append(n))
    assert calls["n"] == MAX_RETRIES + 1
    assert retries == list(range(1, MAX_RETRIES + 1))
