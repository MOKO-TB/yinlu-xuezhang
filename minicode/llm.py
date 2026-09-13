"""LLM 调用层:OpenAI 兼容协议,流式输出 + 工具调用增量聚合。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
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

    def stream_chat(self, messages: list[dict], tools: list,
                    on_text: TextCallback | None = None,
                    on_reasoning: TextCallback | None = None,
                    on_retry: RetryCallback | None = None) -> AssistantTurn:
        """发起一轮流式对话;返回聚合完成的 AssistantTurn。"""
        schemas = [t.to_openai_schema() for t in tools]
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self._stream_once(messages, schemas, on_text, on_reasoning)
            except RETRYABLE_ERRORS as e:
                if attempt == MAX_RETRIES:
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
                     on_reasoning: TextCallback | None) -> AssistantTurn:
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if schemas:
            kwargs["tools"] = schemas

        turn = AssistantTurn()
        fragments: dict[int, dict] = {}
        received_any = False  # 收到首个 chunk 之后的网络错误不再重试,避免输出重复

        for chunk in self._create_stream(kwargs):
            received_any = True
            if getattr(chunk, "usage", None):
                turn.usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                }
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
        return turn
