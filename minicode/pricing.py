"""估算费用(W2):本地价目表 × 真实用量 = 一个「大概是多少钱」的数。

定位对齐 dsh-watcher 0.4.2 的同款功能(默认价目取自它的 pricing/models.yaml,
2026-09-19 取自官方页与 OpenRouter,MIT),三条红线照抄:

- 这是**估算,不是账单**:价目是公开挂牌价的本地快照,滞后于官方页面、
  不含折扣与错峰档 —— 显示必须带「估算」二字,绝不冒充账单;
- **缺行的模型是「未知」,绝不当成 $0**:把不知道的记成零是最会骗人的诚实,
  $0 只允许出现在 token 数真的为 0 的时候;主数字只显示有价可依的那部分,
  缺价的写在脚注(「含未标价模型」);
- **缓存命中不进估算**:校园内网端点不返回缓存明细(usage.py 开头第 3 条
  边界的同款),没有明细就不按缓存价折算,整段 prompt 按普通输入价计 ——
  宁可估多,不玩「假装享受了缓存折扣」。

改价:`pricing-override.json`(「我的数据 → 设置与偏好」里登记的那份),
只存与默认价**不同**的行,删掉文件即回默认价目。匹配口径:模型名精确 →
别名 → 最长前缀(带日期后缀的变体名落进主行),都对不上 = 未知。
"""
from __future__ import annotations

import json
from pathlib import Path

from minicode.paths import app_path

OVERRIDE_FILE = "pricing-override.json"

# 价目来源:dsh-watcher 0.7.0 的 pricing/models.yaml(2026-09-19,MIT)。
# USD / 每百万 token;cache = 缓存命中的输入价,cache_write = 写缓存价。
# 这份数据**会滞后**,滞后的代价由调用方的「估算」二字承担;欢迎订正。
RATES: dict[str, dict] = {
    "claude-haiku-4.5": {"input": 1.0, "output": 5.0, "cache": 0.1, "cache_write": 1.25,
                   "aliases": ["claude-haiku-4-5", "claude-haiku-4.5", "anthropic/claude-haiku-4.5", "Claude-Haiku-4.5", "\"Anthropic: Claude Haiku 4.5\"", "Claude Haiku 4.5", "openrouter/anthropic/claude-haiku-4.5"]},
    "claude-opus-4.5": {"input": 5.0, "output": 25.0, "cache": 0.5, "cache_write": 6.25,
                   "aliases": ["claude-opus-4.5", "\"Anthropic: Claude Opus 4.5\"", "Claude Opus 4.5", "Claude-Opus-4.5", "anthropic/claude-opus-4.5", "openrouter/anthropic/claude-opus-4.5"]},
    "claude-opus-4.6": {"input": 5.0, "output": 25.0, "cache": 0.5, "cache_write": 6.25,
                   "aliases": ["claude-opus-4.6", "\"Anthropic: Claude Opus 4.6\"", "Claude Opus 4.6", "Claude-Opus-4.6", "anthropic/claude-opus-4.6", "openrouter/anthropic/claude-opus-4.6"]},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache": 0.5, "cache_write": 6.25,
                   "aliases": ["claude-opus-5", "anthropic/claude-opus-5", "Claude-Opus-5", "\"Anthropic: Claude Opus 5\"", "Claude Opus 5", "openrouter/anthropic/claude-opus-5"]},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0, "cache": 0.3, "cache_write": 3.75,
                   "aliases": ["claude-sonnet-4", "\"Anthropic: Claude Sonnet 4\"", "Claude Sonnet 4", "Claude-Sonnet-4", "anthropic/claude-sonnet-4", "openrouter/anthropic/claude-sonnet-4"]},
    "claude-sonnet-4.5": {"input": 3.0, "output": 15.0, "cache": 0.3, "cache_write": 3.75,
                   "aliases": ["claude-sonnet-4-5", "anthropic/claude-sonnet-4.5", "anthropic/claude-sonnet-4.6", "claude-sonnet-4.6", "claude-sonnet-4-6", "claude-sonnet-4.5", "\"Anthropic: Claude Sonnet 4.5\"", "Claude Sonnet 4.5", "Claude-Sonnet-4.5", "Claude Sonnet 4.6", "Claude-Sonnet-4.6", "openrouter/anthropic/claude-sonnet-4.5", "openrouter/anthropic/claude-sonnet-4.6"]},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache": 0.2, "cache_write": 2.5,
                   "aliases": ["claude-sonnet-5", "anthropic/claude-sonnet-5", "Claude Sonnet 5", "Claude-Sonnet-5", "\"Anthropic: Claude Sonnet 5\"", "openrouter/anthropic/claude-sonnet-5"]},
    "deepseek-flash": {"input": 0.15, "output": 0.6, "cache": 0.003,
                   "aliases": ["deepseek-v4-flash", "deepseek-v4-flash-vision-exp", "deepseek-v4.1-flash", "deepseek-v4-1-flash", "deepseek-chat", "deepseek-reasoner", "openrouter/deepseek/deepseek-chat", "openrouter/deepseek/deepseek-reasoner", "openrouter/deepseek/deepseek-v4-flash", "openrouter/deepseek/deepseek-v4.1-flash", "deepseek-flash", "DeepSeek V4 Flash", "DeepSeek-V4-Flash", "DeepSeek V4.1 Flash", "DeepSeek-V4.1-Flash", "deepseek/deepseek-flash", "openrouter/deepseek/deepseek-flash", "deepseek/deepseek-v4-flash", "deepseek/deepseek-v4.1-flash", "deepseek/deepseek-chat", "deepseek/deepseek-reasoner"]},
    "deepseek-r1": {"input": 0.7, "output": 2.5,
                   "aliases": ["deepseek-r1", "\"DeepSeek: R1\"", "R1", "deepseek/deepseek-r1", "openrouter/deepseek/deepseek-r1"]},
    "deepseek-v4-pro": {"input": 0.66, "output": 1.98, "cache": 0.022,
                   "aliases": ["deepseek-pro", "openrouter/deepseek/deepseek-v4-pro", "deepseek-v4-pro", "DeepSeek V4 Pro", "DeepSeek-V4-Pro", "deepseek/deepseek-v4-pro", "deepseek/deepseek-pro", "openrouter/deepseek/deepseek-pro"]},
    "gemini-2.5-flash": {"input": 0.3, "output": 2.5, "cache": 0.03, "cache_write": 0.083333,
                   "aliases": ["gemini-2.5-flash", "Gemini 2.5 Flash", "\"Google: Gemini 2.5 Flash\"", "Gemini-2.5-Flash", "google/gemini-2.5-flash", "openrouter/google/gemini-2.5-flash"]},
    "gemini-2.5-flash-lite": {"input": 0.1, "output": 0.4, "cache": 0.01, "cache_write": 0.083333,
                   "aliases": ["gemini-2.5-flash-lite", "\"Google: Gemini 2.5 Flash Lite\"", "Gemini 2.5 Flash Lite", "Gemini-2.5-Flash-Lite", "google/gemini-2.5-flash-lite", "openrouter/google/gemini-2.5-flash-lite"]},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0, "cache": 0.125, "cache_write": 0.375,
                   "aliases": ["Gemini 2.5 Pro", "gemini-2.5-pro", "\"Google: Gemini 2.5 Pro\"", "Gemini-2.5-Pro", "google/gemini-2.5-pro", "openrouter/google/gemini-2.5-pro"]},
    "gemini-3-flash-preview": {"input": 0.5, "output": 3.0, "cache": 0.05, "cache_write": 0.083333,
                   "aliases": ["gemini-3-flash-preview", "\"Google: Gemini 3 Flash Preview\"", "Gemini 3 Flash Preview", "Gemini-3-Flash-Preview", "google/gemini-3-flash-preview", "openrouter/google/gemini-3-flash-preview"]},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.5, "cache": 0.025, "cache_write": 0.083333,
                   "aliases": ["gemini-3.1-flash-lite", "\"Google: Gemini 3.1 Flash Lite\"", "Gemini 3.1 Flash Lite", "Gemini-3.1-Flash-Lite", "google/gemini-3.1-flash-lite", "openrouter/google/gemini-3.1-flash-lite"]},
    "glm-4.5": {"input": 0.6, "output": 2.2, "cache": 0.11,
                   "aliases": ["glm-4.5", "\"Z.ai: GLM 4.5\"", "GLM 4.5", "GLM-4.5", "z-ai/glm-4.5", "openrouter/z-ai/glm-4.5", "zhipu/glm-4.5", "zai/glm-4.5", "glm/glm-4.5"]},
    "glm-4.6": {"input": 0.43, "output": 1.75, "cache": 0.08,
                   "aliases": ["glm-4.6", "\"Z.ai: GLM 4.6\"", "GLM 4.6", "GLM-4.6", "z-ai/glm-4.6", "openrouter/z-ai/glm-4.6", "zhipu/glm-4.6", "zai/glm-4.6", "glm/glm-4.6"]},
    "glm-4.7": {"input": 0.4, "output": 1.75, "cache": 0.08,
                   "aliases": ["glm-4.7", "\"Z.ai: GLM 4.7\"", "GLM 4.7", "GLM-4.7", "z-ai/glm-4.7", "openrouter/z-ai/glm-4.7", "zhipu/glm-4.7", "zai/glm-4.7", "glm/glm-4.7"]},
    "glm-4.7-flash": {"input": 0.0605, "output": 0.4,
                   "aliases": ["glm-4.7-flash", "\"Z.ai: GLM 4.7 Flash\"", "GLM 4.7 Flash", "GLM-4.7-Flash", "z-ai/glm-4.7-flash", "openrouter/z-ai/glm-4.7-flash", "zhipu/glm-4.7-flash", "zai/glm-4.7-flash", "glm/glm-4.7-flash"]},
    "glm-5": {"input": 0.6, "output": 1.92, "cache": 0.12,
                   "aliases": ["glm-5", "GLM-5", "GLM 5", "\"Z.ai: GLM 5\"", "z-ai/glm-5", "openrouter/z-ai/glm-5", "zhipu/glm-5", "zai/glm-5", "glm/glm-5"]},
    "glm-5.2": {"input": 0.5544, "output": 1.7424, "cache": 0.10296,
                   "aliases": ["glm-5.2", "\"Z.ai: GLM 5.2\"", "GLM 5.2", "GLM-5.2", "z-ai/glm-5.2", "openrouter/z-ai/glm-5.2", "zhipu/glm-5.2", "zai/glm-5.2", "glm/glm-5.2"]},
    "glm-5.3": {"input": 0.91, "output": 2.86, "cache": 0.169,
                   "aliases": ["glm-5.3", "\"Z.ai: GLM 5.3\"", "GLM 5.3", "GLM-5.3", "z-ai/glm-5.3", "openrouter/z-ai/glm-5.3", "zhipu/glm-5.3", "zai/glm-5.3", "glm/glm-5.3"]},
    "glm-5.3-flash": {"input": 0.09, "output": 0.3, "cache": 0.018,
                   "aliases": ["glm-5.3-flash", "\"Z.ai: GLM 5.3 Flash\"", "GLM 5.3 Flash", "GLM-5.3-Flash", "z-ai/glm-5.3-flash", "openrouter/z-ai/glm-5.3-flash", "zhipu/glm-5.3-flash", "zai/glm-5.3-flash", "glm/glm-5.3-flash"]},
    "gpt-4.1": {"input": 2.0, "output": 8.0, "cache": 0.5,
                   "aliases": ["GPT-4.1", "gpt-4.1", "\"OpenAI: GPT-4.1\"", "openai/gpt-4.1", "openrouter/openai/gpt-4.1"]},
    "gpt-4.1-mini": {"input": 0.4, "output": 1.6, "cache": 0.1,
                   "aliases": ["gpt-4.1-mini", "\"OpenAI: GPT-4.1 Mini\"", "GPT-4.1 Mini", "GPT-4.1-Mini", "openai/gpt-4.1-mini", "openrouter/openai/gpt-4.1-mini"]},
    "gpt-4.1-nano": {"input": 0.1, "output": 0.4, "cache": 0.025,
                   "aliases": ["gpt-4.1-nano", "\"OpenAI: GPT-4.1 Nano\"", "GPT-4.1 Nano", "GPT-4.1-Nano", "openai/gpt-4.1-nano", "openrouter/openai/gpt-4.1-nano"]},
    "gpt-4o": {"input": 2.5, "output": 10.0, "cache": 1.25,
                   "aliases": ["GPT-4o", "gpt-4o", "\"OpenAI: GPT-4o\"", "openai/gpt-4o", "openrouter/openai/gpt-4o"]},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6, "cache": 0.075,
                   "aliases": ["GPT-4o-mini", "GPT-4o mini", "gpt-4o-mini", "\"OpenAI: GPT-4o-mini\"", "openai/gpt-4o-mini", "openrouter/openai/gpt-4o-mini"]},
    "gpt-5": {"input": 1.25, "output": 10.0, "cache": 0.125,
                   "aliases": ["GPT-5", "gpt-5", "\"OpenAI: GPT-5\"", "openai/gpt-5", "openrouter/openai/gpt-5"]},
    "gpt-5-mini": {"input": 0.25, "output": 2.0, "cache": 0.025,
                   "aliases": ["GPT-5 Mini", "GPT-5-mini", "gpt-5-mini", "\"OpenAI: GPT-5 Mini\"", "GPT-5-Mini", "openai/gpt-5-mini", "openrouter/openai/gpt-5-mini"]},
    "gpt-5-nano": {"input": 0.05, "output": 0.4, "cache": 0.005,
                   "aliases": ["GPT-5 Nano", "GPT-5-nano", "gpt-5-nano", "\"OpenAI: GPT-5 Nano\"", "GPT-5-Nano", "openai/gpt-5-nano", "openrouter/openai/gpt-5-nano"]},
    "gpt-5.1": {"input": 1.25, "output": 10.0, "cache": 0.125,
                   "aliases": ["GPT-5.1", "gpt-5.1", "\"OpenAI: GPT-5.1\"", "openai/gpt-5.1", "openrouter/openai/gpt-5.1"]},
    "grok-4.20": {"input": 1.25, "output": 2.5, "cache": 0.2,
                   "aliases": ["grok-4.20", "\"SpaceXAI: Grok 4.20\"", "Grok 4.20", "Grok-4.20", "x-ai/grok-4.20", "openrouter/x-ai/grok-4.20", "xai/grok-4.20"]},
    "grok-4.3": {"input": 1.25, "output": 2.5, "cache": 0.2,
                   "aliases": ["grok-4.3", "\"SpaceXAI: Grok 4.3\"", "Grok 4.3", "Grok-4.3", "x-ai/grok-4.3", "openrouter/x-ai/grok-4.3", "xai/grok-4.3"]},
    "grok-4.5": {"input": 2.0, "output": 6.0, "cache": 0.3,
                   "aliases": ["grok-4.5", "\"SpaceXAI: Grok 4.5\"", "Grok 4.5", "Grok-4.5", "x-ai/grok-4.5", "openrouter/x-ai/grok-4.5", "xai/grok-4.5"]},
    "grok-4.6": {"input": 2.0, "output": 6.0, "cache": 0.5,
                   "aliases": ["grok-4.6", "Grok 4.6", "Grok-4.6", "\"SpaceXAI: Grok 4.6\"", "x-ai/grok-4.6", "openrouter/x-ai/grok-4.6", "xai/grok-4.6"]},
    "minimax-m2.1": {"input": 0.3, "output": 1.2, "cache": 0.03,
                   "aliases": ["minimax-m2.1", "\"MiniMax: MiniMax M2.1\"", "MiniMax M2.1", "MiniMax-M2.1", "minimax/minimax-m2.1", "openrouter/minimax/minimax-m2.1"]},
    "minimax-m2.5": {"input": 0.27, "output": 1.08, "cache": 0.027,
                   "aliases": ["minimax-m2.5", "\"MiniMax: MiniMax M2.5\"", "MiniMax M2.5", "MiniMax-M2.5", "minimax/minimax-m2.5", "openrouter/minimax/minimax-m2.5"]},
    "minimax-m2.7": {"input": 0.3, "output": 1.2, "cache": 0.06, "cache_write": 0.375,
                   "aliases": ["MiniMax-M2.7", "minimax-m2.7", "openrouter/minimax/minimax-m2.7", "MiniMax M2.7", "minimax/minimax-m2.7"]},
    "minimax-m2.7-highspeed": {"input": 0.6, "output": 2.4, "cache": 0.06, "cache_write": 0.375,
                   "aliases": ["MiniMax-M2.7-highspeed", "minimax-m2.7-highspeed", "openrouter/minimax/minimax-m2.7-highspeed", "MiniMax M2.7 highspeed", "minimax/minimax-m2.7-highspeed"]},
    "minimax-m3": {"input": 0.3, "output": 1.2, "cache": 0.06,
                   "aliases": ["minimax-m3", "\"MiniMax: MiniMax M3\"", "MiniMax M3", "MiniMax-M3", "minimax/minimax-m3", "openrouter/minimax/minimax-m3"]},
    "o3": {"input": 2.0, "output": 8.0, "cache": 0.5,
                   "aliases": ["o3", "\"OpenAI: o3\"", "openai/o3", "openrouter/openai/o3"]},
    "o3-mini": {"input": 1.1, "output": 4.4, "cache": 0.55,
                   "aliases": ["o3-mini", "\"OpenAI: o3 Mini\"", "o3 Mini", "o3-Mini", "openai/o3-mini", "openrouter/openai/o3-mini"]},
    "o4-mini": {"input": 1.1, "output": 4.4, "cache": 0.275,
                   "aliases": ["o4-mini", "\"OpenAI: o4 Mini\"", "o4 Mini", "o4-Mini", "openai/o4-mini", "openrouter/openai/o4-mini"]},
    "qwen3-235b-a22b-2507": {"input": 0.0875, "output": 0.35, "cache": 0.0175,
                   "aliases": ["qwen3-235b-a22b-2507", "\"Qwen: Qwen3 235B A22B Instruct 2507\"", "Qwen3 235B A22B Instruct 2507", "Qwen3-235B-A22B-Instruct-2507", "qwen/qwen3-235b-a22b-2507", "openrouter/qwen/qwen3-235b-a22b-2507"]},
    "qwen3-coder-flash": {"input": 0.195, "output": 0.975, "cache": 0.039, "cache_write": 0.24375,
                   "aliases": ["qwen3-coder-flash", "\"Qwen: Qwen3 Coder Flash\"", "Qwen3 Coder Flash", "Qwen3-Coder-Flash", "qwen/qwen3-coder-flash", "openrouter/qwen/qwen3-coder-flash"]},
    "qwen3-coder-next": {"input": 0.12, "output": 0.8, "cache": 0.07,
                   "aliases": ["qwen3-coder-next", "\"Qwen: Qwen3 Coder Next\"", "Qwen3 Coder Next", "Qwen3-Coder-Next", "qwen/qwen3-coder-next", "openrouter/qwen/qwen3-coder-next"]},
    "qwen3-next-80b-a3b-instruct": {"input": 0.09, "output": 1.1,
                   "aliases": ["qwen3-next-80b-a3b-instruct", "\"Qwen: Qwen3 Next 80B A3B Instruct\"", "Qwen3 Next 80B A3B Instruct", "Qwen3-Next-80B-A3B-Instruct", "qwen/qwen3-next-80b-a3b-instruct", "openrouter/qwen/qwen3-next-80b-a3b-instruct"]},
    "qwen3.7-flash": {"input": 0.03, "output": 0.13, "cache": 0.006, "cache_write": 0.038,
                   "aliases": ["qwen3.7-flash", "\"Qwen: Qwen3.7 Flash\"", "Qwen3.7 Flash", "Qwen3.7-Flash", "qwen/qwen3.7-flash", "openrouter/qwen/qwen3.7-flash"]},
    "qwen3.8-flash": {"input": 0.15, "output": 0.47, "cache": 0.016, "cache_write": 0.2,
                   "aliases": ["qwen3.8-flash", "Qwen3.8 Flash", "Qwen3.8-Flash", "\"Qwen: Qwen3.8 Flash\"", "qwen/qwen3.8-flash", "openrouter/qwen/qwen3.8-flash"]},
}

RATES_SOURCE = "dsh-watcher 0.7.0 pricing/models.yaml(2026-09-19,MIT)"

_OVERRIDE: "tuple[float, dict[str, dict]] | None" = None   # (mtime, 解析结果)
_TABLE: dict[str, dict] | None = None


def override_path() -> Path:
    return app_path(OVERRIDE_FILE)


# ------------------------------------------------------------ 匹配与估算

def _norm(name: str) -> str:
    """匹配前先把名字放平:小写、去首尾空白。别名表里大小写五花八门。"""
    return str(name or "").strip().lower()


def _count(v) -> "int | None":
    """token 数只认非负整数;缺了/非法回 None,由调用方决定当不算还是不计价。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _flat(table: dict[str, dict]) -> dict[str, dict]:
    """别名摊平:alias → 同一行价目。撞名时**主名先注册**(setdefault 保序)。"""
    out: dict[str, dict] = {}
    for key, row in table.items():
        out.setdefault(_norm(key), row)
        for a in row.get("aliases", ()):
            out.setdefault(_norm(a), row)
    return out


def _table() -> dict[str, dict]:
    """生效价目 = 默认表 + 用户改价。覆盖落在它**命中的那一行**上(别名命中
    的也是同一行):按别名改价却只改别名那一个键,同一行就有了两个价 ——
    按主名查的和按别名查的对不上,那种账最会骗人。默认表本体永不改动。"""
    global _TABLE
    if _TABLE is None:
        _TABLE = _flat(RATES)
    merged = dict(_TABLE)
    for key, row in load_override().items():
        base = _TABLE.get(_norm(key))
        if base is not None:
            fresh = {**base, **row}
            for k2, r2 in merged.items():
                if r2 is base:
                    merged[k2] = fresh
        else:
            merged[_norm(key)] = {**row, "aliases": []}
    return merged


def rate_for(model: str, table: "dict | None" = None) -> "dict | None":
    """这一模型的生效价目:精确/别名 → 最长前缀 → None(未知,不当 $0)。

    `table` 给定时在这份表里找(save_override 要对**默认表**比 diff,
    不能对已生效的覆盖比 —— 否则重存一次同价会把用户上一轮的改价静默丢掉)。
    """
    m = _norm(model)
    if not m:
        return None
    tab = table if table is not None else _table()
    hit = tab.get(m)
    if hit is not None:
        return hit
    best = ""
    for key, row in tab.items():
        if len(key) > len(best) and m.startswith(key):
            best, hit = key, row
    return hit


def estimate(model: str, prompt_tokens, completion_tokens,
             cached_tokens=None) -> dict:
    """(这笔用量值多少钱, 有没有价可依)。

    没有价目 → {"usd": None, "priced": False}:调用方据此写「未知」,
    绝不许拿 None 去参加加法(那就是把未知当 0)。缓存价只在这笔用量**真的
    带了缓存明细**时才用;cache 数超出 prompt 视为脏数据,整段按输入价。
    """
    p, c = _count(prompt_tokens), _count(completion_tokens)
    if p is None or c is None:
        return {"usd": None, "priced": False}
    row = rate_for(model)
    if row is None:
        return {"usd": None, "priced": False}
    rate_in = float(row.get("input") or 0)
    rate_out = float(row.get("output") or 0)
    cache = _count(cached_tokens)
    if cache is not None and "cache" in row and 0 <= cache <= p:
        usd = ((p - cache) * rate_in + cache * float(row["cache"])
               + c * rate_out) / 1e6
    else:
        usd = (p * rate_in + c * rate_out) / 1e6
    out = {"usd": usd, "priced": True}
    note = _rate_note(row)
    if note:
        out["rate"] = note
    return out


def _rate_note(row: dict) -> str:
    """悬停提示用的单价一句话(与 estimate 同一把尺,只此一处格式化)。"""
    bits = [f"输入 ${float(row.get('input') or 0):g}",
            f"输出 ${float(row.get('output') or 0):g}"]
    if "cache" in row:
        bits.append(f"缓存 ${float(row['cache']):g}")
    return " / ".join(bits) + " 每 1M token"


def summarize(models: list) -> dict:
    """给 usage.statistics 的 models 行就地添费用;返回摘要片。

    口径:主数字只显示**有价可依**的那部分;缺价但有用量的模型列进
    `unpriced_models`(前端写脚注),绝不把它们当 0 混进总数。token 全为 0
    时 cost_usd 是 0.0 —— 这是唯一允许 $0 的场合。
    """
    total = 0.0
    priced_any = False
    unpriced: list[str] = []
    for m in models or []:
        if not isinstance(m, dict):
            continue
        prompt = _count(m.get("prompt")) or 0
        completion = _count(m.get("completion")) or 0
        r = estimate(m.get("model") or m.get("key"), prompt, completion)
        m["cost_usd"] = r["usd"]
        m["priced"] = r["priced"]
        if "rate" in r:
            m["rate_note"] = r["rate"]
        if r["priced"]:
            priced_any = True
            total += r["usd"]
        elif prompt + completion > 0:
            unpriced.append(str(m.get("model") or m.get("key") or "?"))
    return {"cost_usd": (round(total, 6) if priced_any else None),
            "unpriced_models": unpriced}


# ------------------------------------------------------------ 改价(覆盖)

def load_override() -> dict[str, dict]:
    """用户改价 {"模型名": {"input": x, ...}};读不出来 = 没改价,绝不抛。

    按 mtime 缓存:文件被外部改过(我的数据面板里直接编辑)下一次统计就生效,
    不用重启 —— 价目表是给「估算」用的,不值得为它做一套失效广播。
    """
    global _OVERRIDE
    try:
        mt = override_path().stat().st_mtime
    except OSError:
        mt = -1.0
    if _OVERRIDE is not None and _OVERRIDE[0] == mt:
        return _OVERRIDE[1]
    out: dict[str, dict] = {}
    try:
        raw = json.loads(override_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        raw = None
    if isinstance(raw, dict):
        rows = raw.get("models") if isinstance(raw.get("models"), dict) else raw
        for name, row in (rows or {}).items():
            if not isinstance(row, dict):
                continue
            clean = {k: float(row[k]) for k in
                     ("input", "output", "cache", "cache_write")
                     if isinstance(row.get(k), (int, float))
                     and not isinstance(row.get(k), bool) and row[k] >= 0}
            if clean:
                out[str(name)] = clean
    _OVERRIDE = (mt, out)
    return out


def save_override(rows: dict) -> dict:
    """存一次改价:只落与默认价**不同**的行,与默认相同的行当场删掉(diff-only)。

    返回**生效后**的改价表。写不进去(只读目录)不抛 —— 但改价也没改成,
    后续估算按默认价走:价目不是界面偏好,估算每次都从文件读,没有可依的
    内存副本;返回值如实回空,不装作改成功。
    """
    global _OVERRIDE
    clean: dict[str, dict] = {}
    defaults = _flat(RATES)
    for name, row in (rows or {}).items():
        if not isinstance(row, dict):
            continue
        base = rate_for(str(name), defaults)
        diff: dict[str, float] = {}
        for k in ("input", "output", "cache", "cache_write"):
            v = row.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                f = float(v)
                if base is None or base.get(k) != f:
                    diff[k] = f
        if diff:
            clean[str(name)] = diff
    payload = {"note": "与默认价不同的行才在 models 里;删掉本文件 = 回默认价目。"
                       "单位:USD / 每百万 token。",
               "models": clean}
    try:
        override_path().write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
    except OSError:
        pass
    _OVERRIDE = None              # mtime 已变;强制下一次重读
    _TABLE = None                 # 生效表跟着重建
    return load_override()


def effective_rates() -> dict[str, dict]:
    """生效价目(默认+覆盖,别名已摊平)—— 给测试与将来的改价界面同一份真相。"""
    return dict(_table())
