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


# ---------------- usage 里带缓存命中(v0.9.50) ----------------

def _usage_chunk(prompt=100, completion=20, cached=None, reasoning=None):
    """带 usage 的收尾分片;cached/reasoning 给了就带对应的 *_details。"""
    details = types.SimpleNamespace(cached_tokens=cached) if cached is not None else None
    cdetails = (types.SimpleNamespace(reasoning_tokens=reasoning)
                if reasoning is not None else None)
    usage = types.SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
                                  prompt_tokens_details=details,
                                  completion_tokens_details=cdetails)
    delta = types.SimpleNamespace(content=None, reasoning_content=None,
                                  reasoning=None, tool_calls=None)
    return types.SimpleNamespace(usage=usage,
                                 choices=[types.SimpleNamespace(delta=delta)])


def _client_streaming(monkeypatch, chunks: list):
    client = LLMClient(_cfg())
    monkeypatch.setattr(client._client.chat.completions, "create",
                        lambda **_kw: iter(chunks))
    monkeypatch.setattr("minicode.llm.time",
                        types.SimpleNamespace(perf_counter=_time.perf_counter,
                                              sleep=lambda _s: None))
    return client


def test_usage_carries_cached_tokens_when_reported(monkeypatch):
    """端点回报 prompt_tokens_details.cached_tokens 时透传 —— 提示词前缀
    稳定化(v0.9.49)之后,这是那件事收益的唯一可见证据。"""
    client = _client_streaming(monkeypatch, [_usage_chunk(cached=80)])
    turn = client.stream_chat([{"role": "user", "content": "hi"}], [])
    assert turn.usage["cached_tokens"] == 80


def test_usage_omits_cached_tokens_when_absent(monkeypatch):
    """端点没给 details 就不带这个键 —— 绝不估算,消费方按缺省处理。"""
    client = _client_streaming(monkeypatch, [_usage_chunk(cached=None)])
    turn = client.stream_chat([{"role": "user", "content": "hi"}], [])
    assert "cached_tokens" not in turn.usage


# ---------------- W5/W6:首字延迟与思考 token(端点回报了才有,缺了不编) ----------------

def _text_chunk(text="你"):
    return types.SimpleNamespace(
        usage=None,
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(
            content=text, reasoning_content=None, reasoning=None,
            tool_calls=None))])


def test_ttft_is_measured_to_the_first_content_chunk(monkeypatch):
    """W5:首字延迟 = 发请求 → 第一个**内容** chunk;usage-only 的收尾不算字。"""
    client = _client_streaming(monkeypatch,
                               [_text_chunk("你"), _usage_chunk(cached=None)])
    turn = client.stream_chat([{"role": "user", "content": "hi"}], [])
    assert isinstance(turn.ttft_ms, int) and turn.ttft_ms >= 0
    assert turn.ttft_ms <= turn.elapsed_ms


def test_ttft_counts_a_reasoning_chunk_as_the_first_word(monkeypatch):
    """先思考后说话的模型:首个思考 chunk 就是"首字" —— 用户看到动静的那一刻。"""
    think = types.SimpleNamespace(
        usage=None,
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(
            content=None, reasoning_content="想", reasoning=None,
            tool_calls=None))])
    client = _client_streaming(monkeypatch, [think, _text_chunk("答")])
    turn = client.stream_chat([{"role": "user", "content": "hi"}], [])
    assert isinstance(turn.ttft_ms, int) and turn.ttft_ms >= 0


def test_ttft_stays_none_when_no_content_ever_arrives(monkeypatch):
    """没收到任何内容(纯 usage 流)→ None:未记录不是 0,不编"瞬间首字"。"""
    client = _client_streaming(monkeypatch, [_usage_chunk(cached=None)])
    turn = client.stream_chat([{"role": "user", "content": "hi"}], [])
    assert turn.ttft_ms is None


def test_usage_carries_reasoning_tokens_when_reported(monkeypatch):
    """W6:端点回报 completion_tokens_details.reasoning_tokens 时透传。"""
    client = _client_streaming(monkeypatch,
                               [_usage_chunk(cached=None, reasoning=31)])
    turn = client.stream_chat([{"role": "user", "content": "hi"}], [])
    assert turn.usage["reasoning_tokens"] == 31


def test_usage_omits_reasoning_tokens_when_absent(monkeypatch):
    """思考明细端点不给就不带键 —— 与 cached_tokens 同一条"绝不估算"口径。"""
    client = _client_streaming(monkeypatch, [_usage_chunk(cached=None)])
    turn = client.stream_chat([{"role": "user", "content": "hi"}], [])
    assert "reasoning_tokens" not in turn.usage


# ---------------- 400「请求体解析不了」:逐字段摘掉重发,而不是把回合打死 ----------------

_LITELLM_400 = ("Error code: 400 - {'code': 'LITELLM_ERROR', 'message': "
                "'Invalid Field [failed to parse request body]', 'trace': '9a27732c'}")


def _tool(name="demo"):
    from minicode.tools.base import Tool
    return Tool(name=name, description="d",
                parameters={"type": "object", "properties": {"a": {"type": "string"}}},
                handler=lambda ctx, **kw: "ok")


def _ladder(monkeypatch, fail_while, msg=_LITELLM_400):
    """假端点:`fail_while(kwargs)` 为真就回那个 400,否则正常吐一个字。

    用 `deepseek-v4-flash` 是因为它在思考能力表里登记过 —— 选了档位就会往请求里
    塞 `extra_body`,正好是被严格网关拒的那一类字段。
    """
    from dataclasses import replace

    from openai import BadRequestError
    cfg = replace(_cfg(), providers=[replace(_cfg().providers[0],
                                            model="deepseek-v4-flash")])
    client = LLMClient(cfg)
    seen: list[dict] = []

    def fake_create(**kwargs):
        seen.append(dict(kwargs))
        if fail_while(kwargs):
            req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
            resp = httpx.Response(400, request=req, json={"error": {"message": msg}})
            raise BadRequestError(msg, response=resp, body=None)
        return iter([_chunk("好")])

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    monkeypatch.setattr("minicode.llm.time",
                        types.SimpleNamespace(perf_counter=_time.perf_counter,
                                              sleep=lambda _s: None))
    return client, seen


def test_body_parse_400_drops_the_thinking_params_first(monkeypatch):
    """同学实测的形状:「测试」是通的,对话发不出去 —— 差别就在请求多带了字段。"""
    client, seen = _ladder(monkeypatch, lambda kw: "extra_body" in kw)
    client.reasoning_choice = "high"
    notes = []
    turn = client.stream_chat([{"role": "user", "content": "hi"}], [],
                              on_retry=lambda e, n: notes.append(str(e)))
    assert turn.text == "好", "摘掉字段之后这一轮要照常答完"
    assert "extra_body" in seen[0] and "extra_body" not in seen[1]
    assert client.lean == ["extra_body"]
    assert any("思考档位" in n for n in notes), "摘了什么必须说出来,不能静默换形状"


def test_lean_fields_are_not_sent_again_next_turn(monkeypatch):
    """同一台端点撞过一次就够了:第二回合直接不发,不再拿用户的回合去试。"""
    client, seen = _ladder(monkeypatch, lambda kw: "extra_body" in kw)
    client.reasoning_choice = "high"
    client.stream_chat([{"role": "user", "content": "一"}], [])
    n = len(seen)
    client.stream_chat([{"role": "user", "content": "二"}], [])
    assert len(seen) == n + 1, "第二回合不该再先撞一次 400"
    assert "extra_body" not in seen[-1]


def test_ladder_ends_at_tools_and_says_what_it_cost(monkeypatch):
    """什么都摘不干净时:摘到工具为止,再把「这一轮没有工具」讲明白,然后照实报错。"""
    client, seen = _ladder(monkeypatch, lambda kw: True)
    client.reasoning_choice = "high"
    notes = []
    import pytest as _pt
    from openai import BadRequestError
    with _pt.raises(BadRequestError):
        client.stream_chat([{"role": "user", "content": "hi"}], [_tool()],
                           on_retry=lambda e, n: notes.append(str(e)))
    assert client.lean == ["extra_body", "stream_options", "tools"]
    assert "tools" not in seen[-1] and "stream" in seen[-1]
    assert any("工具清单" in n for n in notes), "把工具摘掉了是最大的降级,必须点名"


def test_a_400_that_is_not_about_field_names_still_raises(monkeypatch):
    """参数值写错那类 400 不在这一条路上:摘字段治不了它,原样抛给用户才看得见真相。"""
    client, seen = _ladder(monkeypatch, lambda kw: True,
                           msg="Error code: 400 - temperature: value is not a valid integer")
    import pytest as _pt
    from openai import BadRequestError
    with _pt.raises(BadRequestError):
        client.stream_chat([{"role": "user", "content": "hi"}], [])
    assert len(seen) == 1, "不该乱摘字段重试"
    assert client.lean == []
