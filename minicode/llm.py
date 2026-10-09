"""LLM 调用层:OpenAI 兼容协议,流式输出 + 工具调用增量聚合。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from typing import Callable
from urllib.parse import urlparse

from openai import (AuthenticationError, BadRequestError, InternalServerError,
                    NotFoundError, OpenAI, PermissionDeniedError,
                    APIConnectionError, APITimeoutError, RateLimitError)

from minicode.config import Config

MAX_RETRIES = 2

# 网关说「请求体解析不了」的几种写法。共同点是**字段不认识**,不是参数值不对 ——
# 后者改不动(我们的值都是按 OpenAI 协议发的),前者可以一项项摘。
BODY_PARSE_MARKS = ("failed to parse request body", "invalid field",
                    "unrecognized key", "extra inputs are not permitted",
                    "error parsing body", "invalid_json")
# 我们在一轮标准请求之上多带的东西,按「摘掉最不伤」排:
# thinking/reasoning 是锦上添花,include_usage 只影响实测数据,tools 摘掉才动到能力。
LEAN_LADDER = ("extra_body", "stream_options", "tools")
LEAN_LABELS = {"extra_body": "思考档位参数(thinking / reasoning_effort)",
               "stream_options": "stream_options(要求回 usage 那个字段)",
               "tools": "工具清单(这一轮没有工具可调用,只能纯对话)"}



class FallbackUnavailable(RuntimeError):
    """备选供应商没有可用 Key —— 回落没发生,原错误继续往上走(C2)。"""

RETRYABLE_ERRORS = (APIConnectionError, APITimeoutError, RateLimitError,
                    InternalServerError)

# 「上下文太长」这一类失败长什么样:端点与网关各说各话,只能按标记串认。
_OVERFLOW_MARKERS = (
    "context_length_exceeded", "maximum context length", "context length",
    "too many tokens", "reduce the length", "input is too long",
    "prompt is too long", "exceeds the model", "超出上下文", "上下文长度",
    "超过最大长度", "token 超限",
)


def is_context_overflow(exc) -> bool:
    """这一次失败是不是"上下文太长"。

    判据是端点回的那句话里的标记串,不是 HTTP 状态码 —— 网关把 400 包成
    `BadRequestError`,而"参数写错了"与"太长了"共用这一个码;认错就会白压一次,
    而**压缩是有损的**。所以认不准一律判"不是":宁可把这次失败照原样交给用户,
    也不要在不该压的时候把对话压掉。
    """
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _OVERFLOW_MARKERS)


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
    # W5:首字延迟(发请求 → 第一个内容 chunk 到达)。没收到任何内容(失败/
    # 纯 usage 流)就是 None —— 宁可"未记录",不编一个 0 出来。
    ttft_ms: int | None = None


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
        # 这台端点**不收**的请求字段(撞过 400 之后记在这儿,后面几轮直接不发):
        # 换供应商/换模型会重建客户端,所以这份账不会串到别的端点上,也不会跨重启留着。
        self.lean: list[str] = []

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
                    on_retry: RetryCallback | None = None,
                    fallback_provider_id: str = "",
                    on_fallback: "RetryCallback | None" = None) -> AssistantTurn:
        """发起一轮流式对话;返回聚合完成的 AssistantTurn。

        `fallback_provider_id`(C2,v0.9.108):主供应商连**认证都过不去**
        (401/403/404——Key 失效、被停、模型下线)且备选供应商有可用 Key 时,
        自动切备选重试一次;连接错误/限流**不触发回落**(那是重试的活儿,
        修网络的归修网络)。切换必须说出来:`on_fallback(原因)` 由调用方
        推给 UI —— 绝不静默换模型(§3 第 14 条口径)。
        """
        schemas = [t.to_openai_schema() for t in tools]
        try:
            return self._with_retries(messages, schemas, on_text, on_reasoning,
                                      on_retry)
        except (AuthenticationError, NotFoundError, PermissionDeniedError) as e:
            if not fallback_provider_id:
                raise
            backup = LLMClient.scoped(self.config, provider_id=fallback_provider_id)
            if not backup.config.provider or not backup.config.provider.has_key:
                raise FallbackUnavailable(
                    f"备选供应商 {fallback_provider_id} 没有可用 Key,回落没发生") from e
            note = (f"主供应商连不上({type(e).__name__}),已自动切到备选"
                    f"「{backup.config.provider.name}」继续 —— 原因与切换都说在这里,"
                    "模型回答照常,但质量可能不同。")
            if on_fallback is not None:
                on_fallback(RuntimeError(note), 1)
            elif on_retry is not None:
                on_retry(RuntimeError(note), 1)
            return backup._with_retries(messages, schemas, on_text, on_reasoning,
                                        on_retry)

    def _with_retries(self, messages, schemas, on_text, on_reasoning, on_retry):
        for attempt in range(MAX_RETRIES + 1):
            emitted = {"any": False}
            try:
                return self._stream_once(messages, schemas, on_text, on_reasoning,
                                         emitted, on_retry)
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

    def _create_stream(self, kwargs: dict,
                       on_retry: RetryCallback | None = None):
        """发一次流式请求;端点说「请求体解析不了」时**逐字段摘掉重试**。

        原来的做法只认 `stream_options` 一个名字,而且要求错误文本里正好写着它 ——
        LiteLLM 那类网关回的是「Invalid Field [failed to parse request body]」,
        一个字段名都不点,于是整个回合死在一张红卡上(同学实测:模型「测试」是通的,
        对话发不出去,差别就在测试请求不带 tools/extra_body)。
        现在按 `LEAN_LADDER` 的顺序摘:先想档位、再 usage 字段、最后才是工具,
        **摘了哪一样必须说出来**,并且记在 `self.lean` 里 —— 同一轮里别每次都先撞一遍 400。
        """
        for gone in self.lean:
            kwargs.pop(gone, None)
        attempt = 0
        while True:
            try:
                return self._client.chat.completions.create(**kwargs)
            except BadRequestError as e:
                text = str(e).lower()
                if not any(m in text for m in BODY_PARSE_MARKS):
                    raise
                named = next((f for f in LEAN_LADDER
                              if f in kwargs and f in text), "")
                drop = named or next((f for f in LEAN_LADDER if f in kwargs), "")
                if not drop:
                    raise
                kwargs.pop(drop)
                if drop not in self.lean:
                    self.lean.append(drop)
                attempt += 1
                if on_retry is not None:
                    on_retry(RuntimeError(
                        "这个端点不收我们发的" + LEAN_LABELS[drop]
                        + " —— 已把它从这次请求里摘掉重发,后面的回合也不再发它"),
                        attempt)

    def _stream_once(self, messages: list[dict], schemas: list[dict],
                     on_text: TextCallback | None,
                     on_reasoning: TextCallback | None,
                     emitted: dict | None = None,
                     on_retry: RetryCallback | None = None) -> AssistantTurn:
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if schemas:
            kwargs["tools"] = schemas
        # 模型配置里声明的"最大输出 Token"→ 真发进请求。没声明就不带这个键
        # (与旧请求逐字节一致)—— 这条是"编辑器不是样子货"的一部分。
        # 这里不再判类型:`model_cfg_from_raw` 只让正整数进来,重复校验就是第二把尺子。
        declared_out = self.config.effective_model_cfg().get("max_output_tokens")
        if declared_out:
            kwargs["max_tokens"] = declared_out
        # 思考档位 → 真实请求参数(见 minicode/reasoning.py)。
        # default 时一个参数都不加:请求字节与旧版完全一致。
        # 用户在模型配置里自己登记的档位与映射优先于内置那张按模型名的表。
        from minicode.reasoning import apply_reasoning_choice
        apply_reasoning_choice(kwargs, self.config.model, self.reasoning_choice,
                               cfg=self.config.effective_model_cfg())

        turn = AssistantTurn()
        fragments: dict[int, dict] = {}
        started = time.perf_counter()
        first_at: float | None = None      # W5:首个内容 chunk 的到达时刻

        for chunk in self._create_stream(kwargs, on_retry):
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
                # W6:思考 token —— completion_tokens_details.reasoning_tokens,
                # 与上面 cached_tokens 同一条口径:端点回报了才带这个键,
                # 缺了不估算(校园端点大概率不给 → 面板如实写「未记录」)。
                cdetails = getattr(chunk.usage, "completion_tokens_details", None)
                reasoned = getattr(cdetails, "reasoning_tokens", None)
                if isinstance(reasoned, int) and reasoned >= 0:
                    turn.usage["reasoning_tokens"] = reasoned
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            content = getattr(delta, "content", None)
            reasoning = getattr(delta, "reasoning_content", None) \
                or getattr(delta, "reasoning", None)
            tool_deltas = getattr(delta, "tool_calls", None) or []
            # W5:首个**内容** chunk(文本/思考/工具参数都算字)到达的时刻。
            # usage-only 的收尾 chunk 在上面 choices 为空时已 continue,不会冒充首字。
            if first_at is None and (content or reasoning or tool_deltas):
                first_at = time.perf_counter()
            if content:
                turn.text += content
                if on_text:
                    on_text(content)

            if reasoning:
                turn.reasoning += reasoning
                if on_reasoning:
                    on_reasoning(reasoning)

            for tc in tool_deltas:
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
        # W5:首字延迟;没收到任何内容(空流/纯 usage)保持 None,不编 0
        if first_at is not None:
            turn.ttft_ms = max(0, int((first_at - started) * 1000))
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
