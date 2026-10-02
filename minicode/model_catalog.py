"""模型能力目录与任务面板指标:对齐星瑶 Y-Code 的 model_catalog 形态。

context_window / supports_vision 数据取自星瑶 Y-Code 的
core/model_catalog.py(公开仓库 gitee.com/feng-chenhao/xingyao-y-code),
覆盖它预置的主流模型;目录里没有的模型按「未知窗口 + 纯文本」兜底,
只是不显示能力标签与窗口上限,不会挡着正常使用。

任务面板(v0.9.04):输入框下展示
- 当前任务用的模型 + 能力标签(文本/视觉/工具调用)+ 窗口大小;
- 本次任务消耗的真实 token(agent 每回合推 turn_usage 事件);
- 当前任务上下文占用 / 模型窗口上限(压缩触发器的同一把尺子)。
"""
from __future__ import annotations

from minicode.compaction import context_breakdown, estimate_tokens, schema_tokens
from minicode.config import API_KEY_PLACEHOLDERS

# model -> {"context_window": int, "supports_vision": bool}
# 数据源:星瑶 Y-Code core/model_catalog.py(2026-09 同步)
MODEL_CATALOG: dict[str, dict] = {
    "kimi-for-coding":           {"context_window": 1_000_000, "supports_vision": True},
    "k3":                        {"context_window": 1_000_000, "supports_vision": True},
    "k3-256k":                   {"context_window": 256_000,   "supports_vision": True},
    "kimi-k3":                   {"context_window": 1_000_000, "supports_vision": True},
    "kimi-k2.7-code":            {"context_window": 256_000,   "supports_vision": True},
    "kimi-k2.6":                 {"context_window": 256_000,   "supports_vision": True},
    "deepseek-flash":            {"context_window": 1_000_000, "supports_vision": True},
    "deepseek-v4-pro":           {"context_window": 1_000_000, "supports_vision": False},
    # 校内服务实测窗口 327680(2026-09-16 用 model_probe 问出来的,旧值 1M 是错的)
    "deepseek-v4-flash":         {"context_window": 327_680,   "supports_vision": False},
    "deepseek-v4-flash-0731":    {"context_window": 1_000_000, "supports_vision": False},
    "deepseek-v4-pro-0813":      {"context_window": 1_000_000, "supports_vision": False},
    "agnes-3.0-flash":           {"context_window": 512_000,   "supports_vision": True},
    "agnes-2.5-flash":           {"context_window": 512_000,   "supports_vision": True},
    "minimax-m2.5":              {"context_window": 200_000,   "supports_vision": False},
    "minimax-m2.7":              {"context_window": 204_800,   "supports_vision": True},
    "minimax-m3":                {"context_window": 1_000_000, "supports_vision": True},
    "mimo-v2.5-pro":             {"context_window": 256_000,   "supports_vision": False},
    "qwen3.7-max":               {"context_window": 1_000_000, "supports_vision": False},
    "qwen3.8-max":               {"context_window": 1_000_000, "supports_vision": True},
    "qwen3.8-flash":             {"context_window": 1_000_000, "supports_vision": True},
    "qwen3.8-27b":               {"context_window": 1_000_000, "supports_vision": True},
    "glm-5.1":                   {"context_window": 1_000_000, "supports_vision": False},
    "glm-5.2":                   {"context_window": 1_000_000, "supports_vision": False},
    "zai-org/glm-5.2":           {"context_window": 1_000_000, "supports_vision": False},
    "glm-5.3":                   {"context_window": 1_000_000, "supports_vision": False},
    "glm-5.3-flash":             {"context_window": 1_000_000, "supports_vision": True},
    "seed-2.1-turbo":            {"context_window": 262_144,   "supports_vision": True},
    "seed-2.1-pro":              {"context_window": 262_144,   "supports_vision": True},
    "longcat-2.0":               {"context_window": 1_000_000, "supports_vision": False},
    "sensenova-6.8-flash-lite":  {"context_window": 256_000,   "supports_vision": True},
    "step-3.7-flash":            {"context_window": 256_000,   "supports_vision": True},
    "step-router-v1":            {"context_window": 256_000,   "supports_vision": False},
}


# 运行时探测结果(见 minicode/model_probe.py):键为小写模型名,按字段覆盖
# 静态目录。静态目录是「人工维护的常见模型」,探测结果才是「这台服务上这个
# 模型真实的窗口与多模态能力」。两者都有时探测优先 —— 学校换模型/换网关,
# 面板与压缩阈值自动跟上,不用改代码。
_DETECTED: dict[str, dict] = {}


def set_detected(model: str, *, context_window: int | None = None,
                 supports_vision: bool | None = None) -> None:
    """登记一条探测结论;None 表示这一项没问到,不覆盖。"""
    key = (model or "").strip().lower()
    if not key:
        return
    entry = _DETECTED.setdefault(key, {})
    if isinstance(context_window, int) and context_window > 0:
        entry["context_window"] = context_window
    if supports_vision is not None:
        entry["supports_vision"] = bool(supports_vision)


def clear_detected() -> None:
    _DETECTED.clear()


def detected_for(model: str) -> dict:
    return dict(_DETECTED.get((model or "").strip().lower()) or {})


def lookup(model: str) -> dict:
    """查模型能力;键统一小写。探测结果优先,静态目录兜底,未知不猜数字。"""
    key = (model or "").strip().lower()
    base = MODEL_CATALOG.get(key)
    det = _DETECTED.get(key) or {}
    window = det.get("context_window") or (base or {}).get("context_window")
    vision = det.get("supports_vision")
    if vision is None:
        vision = bool((base or {}).get("supports_vision", False))
    return {
        "known": base is not None or bool(det),
        "context_window": window,
        "supports_vision": bool(vision),
        "detected": bool(det),
    }


def context_window_for(model: str, default: int) -> int:
    """模型的上下文窗口;目录里没有就用配置默认(压缩触发器同款)。"""
    info = lookup(model)
    return info["context_window"] or default


def capabilities(model: str) -> list[str]:
    """能力清单:文本恒有;视觉看目录;工具调用是 Agent 的基本盘,恒有。"""
    caps = ["文本"]
    if lookup(model)["supports_vision"]:
        caps.append("视觉")
    caps.append("工具调用")
    return caps


def context_reading(agent, session) -> tuple[int, str]:
    """面板上「上下文占用」那个数,连同这把尺子的来源 —— 与压缩触发器同一次读数。

    分开算会出这种事:面板按"消息 ÷ 窗口"说还没到七成,实际请求里还有 40 来个工具
    定义,压缩提前来了 —— 用户看到的是"它自己乱压"。没有 ctx 的测试桩退回本地估算,
    来源如实写 estimated:没量过就不假装是实测。
    """
    pair = getattr(agent, "context_pair", None)
    if callable(pair):
        used, source = pair()
        return int(used), str(source or "estimated")
    calc = getattr(agent, "context_tokens", None)   # 只报一个数的桩(测试 FakeAgent)
    if callable(calc):
        return int(calc()), "estimated"
    return (estimate_tokens(session.messages)
            + schema_tokens(getattr(agent, "registry", []) or [])), "estimated"


def push_turn_usage(agent, session, config, turn) -> None:
    """给 GUI 推一条 turn_usage 事件(任务面板:消耗 + 上下文占用)。

    consumed = 本任务累计真实消耗(prompt+completion,服务端返回才累计);
    context  = 本回合发给模型的上下文占用:优先服务端真实 prompt_tokens,
               缺了退回本地估算(与压缩触发器同一把尺子),estimated 标来源。
    TerminalUI 没有 push,getattr 兜底即可,CLI 零影响。
    """
    push = getattr(agent.ui, "push", None)
    if not callable(push):
        return
    usage = turn.usage if isinstance(turn.usage, dict) else {}
    prompt = usage.get("prompt_tokens")
    estimated = not (isinstance(prompt, int) and prompt >= 0)
    context = prompt if not estimated else context_reading(agent, session)[0]
    try:
        push("turn_usage",
             consumed=int(getattr(agent, "turn_tokens", 0) or 0),
             context_tokens=int(context),
             estimated=bool(estimated),
             model=config.model)
    except Exception:
        pass        # 面板数据绝不能影响对话主流程


def _has_key(api_key: str) -> bool:
    return (api_key or "").strip() not in API_KEY_PLACEHOLDERS


def session_metrics(agent, session, config, llm_override: dict | None) -> dict:
    """任务面板一屏数据:当前会话的模型/能力/消耗/上下文占用/可切列表。

    llm_override 非 None 表示这个会话单独切过模型(不影响全局配置)。

    `selectable` 是**供应商 × 模型**的展开(v0.9.13):一个供应商挂多个模型时,
    用户能在任务面板里直接选到具体那个模型,而不是只能选到供应商。每项带
    `key`("供应商::模型")给前端做选中判断 —— 光比 provider_id 会让同一供应商
    下的两个模型都显示成"选中"。
    """
    model = llm_override["model"] if llm_override else config.model
    window = context_window_for(model, config.context_window)
    used, source = context_reading(agent, session)
    consumed = getattr(agent, "turn_tokens", 0) or 0
    info = lookup(model)
    selectable = []
    for p in config.providers:
        if p.id != "gzasc" and not getattr(p, "enabled", True):
            continue                       # 停用的供应商不进可切列表
        for entry in p.model_entries():
            if not entry.get("enabled", True):
                continue                   # 停用的模型也不进(但管理面板里还看得到)
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            caps = lookup(name)
            selectable.append({
                "key": f"{p.id}::{name}",
                "id": p.id, "name": p.name, "model": name,
                "has_key": _has_key(p.api_key),
                "builtin": p.id == "gzasc",
                "supports_vision": caps["supports_vision"],
                "context_window": caps["context_window"],
                "capabilities": capabilities(name),
            })
    active_id = (llm_override["provider_id"] if llm_override
                 else (config.provider.id if config.provider else ""))
    breakdown = context_breakdown(agent, session, config, window, used, source)
    return {
        "provider_id": active_id,
        "selected_key": f"{active_id}::{model}" if active_id and model else "",
        "model": model,
        "capabilities": capabilities(model),
        "supports_vision": info["supports_vision"],
        # 真正决定是否发图的那个:v0.9.22 起按模型能力判定,面板上的"强制看图"
        # 只是把 config.vision 这个总开关打开(能力表没登记的自定义模型用得上)。
        "vision": bool(info["supports_vision"] or getattr(config, "vision", False)),
        "vision_forced": bool(getattr(config, "vision", False)),
        "known": info["known"],
        "context_window": window,
        "context_tokens": used,
        # 这个占用的尺子来源:"real" = 上次请求服务端真返回的 prompt_tokens 当的下限,
        # "estimated" = 本地粗估。面板必须把它显示出来 —— 同一个百分比,
        # 实测和估出来的可信度不是一回事。
        "context_source": source,
        "consumed": consumed,
        "compact_ratio": config.compact_at_ratio,
        "context_breakdown": breakdown,
        "selectable": selectable,
    }
