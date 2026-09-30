"""LLM 调用层:OpenAI 兼容协议,流式输出 + 工具调用增量聚合。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from typing import Callable
from urllib.parse import urlparse

from openai import (BadRequestError, InternalServerError, OpenAI,
                    APIConnectionError, APITimeoutError, RateLimitError)

from minicode.config import Config

MAX_RETRIES = 2
RETRYABLE_ERRORS = (APIConnectionError, APITimeoutError, RateLimitError,
                    InternalServerError)


def _is_direct_host(host: str) -> bool:
    """本地回环与私网地址(10/172.16-31/192.168)直连,不走系统代理。"""
    host = (host or "").lower()
    if host in ("localhost", "::1") or host.startswith("127."):
        return True
    m = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", host)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (a == 10
                or (a == 172 and 16 <= b <= 31)
                or (a == 192 and b == 168))
    return False


def _make_http_client(base_url: str, timeout_seconds: int):
    """本地/内网地址必须直连:系统代理(Clash 等)会拦截并返回 502。"""
    if not _is_direct_host(urlparse(base_url).hostname or ""):
        return None
    try:
        import httpx2 as httpx
    except ImportError:
        import httpx as httpx
    return httpx.Client(trust_env=False,
                        timeout=httpx.Timeout(timeout_seconds, connect=30))

TextCallback = Callable[[str], None]
RetryCallback = Callable[[Exception, int], None]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # 原始 JSON 字符串,由调用方解析


@dataclass
class AssistantTurn:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict | None = None
    # 本次请求的实测数据(流式收包耗时,不是挂钟回合时长):
    # elapsed_ms 由 _stream_once 记(从发请求到流结束);completion_tokens 优先
    # 用服务端 usage,缺了退回文本粗估(只用于展示"每秒 token",不进记账)。
    elapsed_ms: int | None = None
    completion_tokens: int | None = None


class LLMClient:
    def __init__(self, config: Config):
        self.config = config
        http_client = _make_http_client(config.base_url, config.request_timeout_seconds)
        kwargs = {"http_client": http_client} if http_client is not None else {}
        self._client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key or "EMPTY",  # 空 key 允许构造(未配置时调用会 401,本地服务也需要占位)
            timeout=config.request_timeout_seconds,
            max_retries=0,  # 重试由本层自己做,以便向 UI 汇报
            **kwargs,
        )
        # 思考档位:界面上选的 default/off/low/high/max。会话级状态
        # (跟着 LLMClient 走 —— 每个会话切模型时会重建,所以新会话回到
        # "默认";Bridge 切档位时只重建共享客户端,不影响别的会话)。
        self.reasoning_choice = "default"

    @classmethod
    def scoped(cls, config: Config, provider_id: str = "",
               model: str = "") -> "LLMClient":
        """换一个供应商/换一个模型重建客户端 —— **这条构造只此一处**。

        `LLMClient` 只认 `config.model` 与活动供应商,不认"供应商 + 模型"二元组,
        所以换模型必须把 providers 里那条的 `model` 改掉再重建。两处各写一遍
        (会话切模型 / 子代理按档位分模型)总有一天一处漏带 `active_provider_id` ——
        表现是"模型换了、Key 还是旧供应商的",报 401 看着像 Key 坏了。
        """
        target_id = provider_id or config.active_provider_id
        providers = config.providers
        if model:
            providers = [replace(p, model=model) if p.id == target_id else p
                         for p in providers]
        return cls(replace(config, providers=providers, active_provider_id=target_id))

    def stream_chat(self, messages: list[dict], tools: list,
                    on_text: TextCallback | None = None,
                    on_reasoning: TextCallback | None = None,
                    on_retry: RetryCallback | None = None) -> AssistantTurn:
        """发起一轮流式对话;返回聚合完成的 AssistantTurn。"""
        schemas = [t.to_openai_schema() for t in tools]
        for attempt in range(MAX_RETRIES + 1):
            emitted = {"any": False}
            try:
                return self._stream_once(messages, schemas, on_text, on_reasoning,
                                         emitted)
            except RETRYABLE_ERRORS as e:
                # 已经往界面上吐过 chunk 就不再重试:重跑一遍用户会看到"半截答案 +
                # 完整答案"两段叠在一起(这条口径原来只写在一行注释里,`received_any`
                # 记了却谁都没读 —— #85 跑 ruff 才撞出来,见 F841)。
                if attempt == MAX_RETRIES or emitted["any"]:
                    raise
                if on_retry:
                    on_retry(e, attempt + 1)
                time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    def complete(self, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        """一次性(非流式)补全:给记忆整理这类内部工具用,不涉及工具调用。

        与 stream_chat 分开是有意为之——它不注册工具、不流式回调,
        失败直接抛异常由调用方兜底,避免把内部整理逻辑和主循环缠在一起。
        """
        kwargs: dict = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        resp = self._client.chat.completions.create(**kwargs)
        choices = getattr(resp, "choices", None) or []
        if not choices:
            return ""
        return getattr(choices[0].message, "content", "") or ""

    def _create_stream(self, kwargs: dict):
        try:
            return self._client.chat.completions.create(**kwargs)
        except BadRequestError as e:
            # 个别兼容端点不认识 stream_options,去掉后重试一次
            if "stream_options" in str(e) and "stream_options" in kwargs:
                kwargs = {k: v for k, v in kwargs.items() if k != "stream_options"}
                return self._client.chat.completions.create(**kwargs)
            raise

    def _stream_once(self, messages: list[dict], schemas: list[dict],
                     on_text: TextCallback | None,
                     on_reasoning: TextCallback | None,
                     emitted: dict | None = None) -> AssistantTurn:
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if schemas:
            kwargs["tools"] = schemas
        # 思考档位 → 真实请求参数(见 minicode/reasoning.py)。
        # default 时一个参数都不加:请求字节与旧版完全一致。
        from minicode.reasoning import apply_reasoning_choice
        apply_reasoning_choice(kwargs, self.config.model, self.reasoning_choice)

        turn = AssistantTurn()
        fragments: dict[int, dict] = {}
        started = time.perf_counter()

        for chunk in self._create_stream(kwargs):
            if emitted is not None:
                # 收到首个 chunk 就记一笔:此后失败**不再重试**(见 stream_chat),
                # 否则用户会看到半截答案后面又接一份完整答案。
                emitted["any"] = True
            if getattr(chunk, "usage", None):
                turn.usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                }
                # 缓存命中的 prompt 部分:端点在 usage.prompt_tokens_details.cached_tokens
                # 里回报,缺了就不带这个键 —— 绝不估算。提示词前缀稳定化(v0.9.49
                # 静态段前置)之后,这是那件事收益的唯一可见证据。
                details = getattr(chunk.usage, "prompt_tokens_details", None)
                cached = getattr(details, "cached_tokens", None)
                if isinstance(cached, int) and cached >= 0:
                    turn.usage["cached_tokens"] = cached
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            content = getattr(delta, "content", None)
            if content:
                turn.text += content
                if on_text:
                    on_text(content)

            reasoning = getattr(delta, "reasoning_content", None) \
                or getattr(delta, "reasoning", None)
            if reasoning:
                turn.reasoning += reasoning
                if on_reasoning:
                    on_reasoning(reasoning)

            for tc in getattr(delta, "tool_calls", None) or []:
                slot = fragments.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function.arguments:
                        slot["arguments"] += tc.function.arguments

        turn.tool_calls = [
            ToolCall(id=f["id"] or f"call_{i}", name=f["name"],
                     arguments=f["arguments"] or "{}")
            for i, f in sorted(fragments.items())
        ]
        # 实测耗时与输出 token 数(展示用,失败/缺失不挡流程):
        turn.elapsed_ms = int((time.perf_counter() - started) * 1000)
        u = turn.usage or {}
        ct = u.get("completion_tokens")
        if isinstance(ct, int) and ct >= 0:
            turn.completion_tokens = ct
        elif turn.text:
            # 服务端没给 usage:按"1 token ≈ 0.6 个非 ASCII 字符或 4 个 ASCII 字符"粗估,
            # 只用于"每秒 token"展示,绝不进 token 记账/上下文占用口径。
            wide = sum(1 for ch in turn.text if ord(ch) > 0x2E7F)
            turn.completion_tokens = int(wide / 0.6 + (len(turn.text) - wide) / 4)
        return turn
