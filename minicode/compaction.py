"""上下文压缩:估算 token 占用;接近上限时把早期对话折叠成摘要。

切分按"完整轮"(用户消息起到下一个用户消息前)进行,保证不会把
assistant.tool_calls 与对应 tool 结果拆开——那会导致 API 报错。
"""
from __future__ import annotations

import re
from typing import Protocol

_CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")

SUMMARY_PROMPT = (
    "你是会话压缩器。请把下面的编码助手对话历史压缩成简洁的中文摘要(尽量 400 字内),"
    "必须保留:1) 用户任务目标;2) 已完成的修改(文件路径与改动内容);"
    "3) 关键决定与约束;4) 未完成事项与下一步。直接输出摘要正文,不要客套。"
)

HISTORY_CHAR_CAP = 40000  # 送去摘要的原文上限,超出截尾
TOOL_RESULT_RENDER_CAP = 800


def _text_tokens(text: str) -> int:
    cjk = len(_CJK.findall(text))
    return cjk + (len(text) - cjk) // 4


def estimate_tokens(messages: list[dict]) -> int:
    """粗略估算消息序列的 token 数:CJK 字符≈1 token,其余≈4 字符 1 token。"""
    total = 0
    for m in messages:
        total += 8  # 每条消息的角色/结构开销
        content = m.get("content")
        if isinstance(content, str):
            total += _text_tokens(content)
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            total += _text_tokens(fn.get("arguments", "")) + _text_tokens(fn.get("name", "")) + 8
    return total


class Summarizer(Protocol):
    def stream_chat(self, messages: list[dict], tools: list, **kwargs): ...


def _render(msg: dict) -> str:
    role = msg.get("role", "?")
    content = msg.get("content")
    text = content if isinstance(content, str) else ""
    parts = []
    if text:
        parts.append(text[:TOOL_RESULT_RENDER_CAP])
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        parts.append(f"调用工具 {fn.get('name')}({fn.get('arguments', '')[:200]})")
    if role == "tool":
        return f"[工具结果] {text[:TOOL_RESULT_RENDER_CAP]}"
    return f"[{role}] " + " ".join(parts) if parts else f"[{role}]"


def compact(messages: list[dict], llm: Summarizer,
            keep_recent: int = 2) -> tuple[list[dict], int]:
    """把除最近 keep_recent 轮之外的对话折叠成摘要。

    返回 (新消息列表, 原消息数);没有可压缩内容时原样返回。
    """
    system = messages[:1] if messages and messages[0]["role"] == "system" else []
    body = messages[len(system):]

    turns: list[list[dict]] = []
    for m in body:
        if m["role"] == "user" or not turns:
            turns.append([m])
        else:
            turns[-1].append(m)
    if len(turns) <= keep_recent:
        return messages, len(messages)

    older, recent = turns[:-keep_recent], turns[-keep_recent:]
    older_msgs = [m for t in older for m in t]
    history_text = "\n".join(_render(m) for m in older_msgs)
    if len(history_text) > HISTORY_CHAR_CAP:
        history_text = history_text[-HISTORY_CHAR_CAP:]

    turn = llm.stream_chat(
        [{"role": "user", "content": SUMMARY_PROMPT + "\n\n" + history_text}],
        tools=[],
    )
    summary = turn.text.strip() or "(摘要生成失败,早期对话已丢弃)"

    new_messages = system + [
        {"role": "user", "content": f"(此前对话的摘要,细节可能省略)\n{summary}"},
        *[m for t in recent for m in t],
    ]
    return new_messages, len(messages)
