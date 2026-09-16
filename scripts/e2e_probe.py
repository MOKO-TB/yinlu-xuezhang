"""假 LLM 服务器共用的「模型能力探测」识别。

`minicode/model_probe.py` 会在程序启动时（首次运行、无缓存）向**同一个模型端点**
发两类请求:发 `max_tokens` 巨大值的「问窗口」请求,和带一张 1x1 图片的「问视觉」
请求。它们不是对话请求,**不该占用假服务器的剧本轮次** —— 否则第一回合的工具调用
会被探测吃掉,主对话直接拿到收尾文本,症状是:

    模型说「文件已创建完成」,但那个文件根本不存在(会话留档里也没有 tool 消息)

（2026-09-16 实测:e2e_frozen 就是这么挂的 —— 探测先发了 4 个请求,把 `REQ_COUNT`
从 1 推到 5,于是真对话被当成收尾回合。）

放成独立模块,是因为 `scripts/` 下有 6 个各自实现假服务器的 e2e 脚本,识别逻辑
只该有一份。脚本直接跑时 `sys.path[0]` 就是 `scripts/`,所以 `import e2e_probe`
不需要额外的路径设置。
"""
from __future__ import annotations

import json

_FALLBACK_FLOOR = 1_048_576          # 与 model_probe._WINDOW_LADDER 的最小档一致


def _window_floor() -> int:
    """用 model_probe 自己的候选值当阈值,不各写一份真相。"""
    try:
        from minicode.model_probe import _WINDOW_LADDER   # noqa: WPS433
        return min(_WINDOW_LADDER)
    except Exception:                                     # noqa: BLE001
        return _FALLBACK_FLOOR


def is_probe_request(raw: bytes) -> bool:
    """请求体是不是能力探测(而不是一次对话)?判据见模块 docstring。"""
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except Exception:                                     # noqa: BLE001
        return False
    if not isinstance(body, dict):
        return False
    for m in body.get("messages") or []:
        if isinstance(m, dict) and isinstance(m.get("content"), list):
            return True                    # 视觉探测:多模态 content 列表
    max_tokens = body.get("max_tokens")
    return isinstance(max_tokens, int) and max_tokens >= _window_floor()


def refuse_probe(handler) -> None:
    """回一个「问不出结论」的答复,且**不推进剧本、不计入请求记录**。

    401 + 一段既没有窗口数字、也没有多模态关键字的文本 → `model_probe` 两项都
    拿不到结论 (`context_window=None, supports_vision=None`),于是它既不落缓存
    (见 `ensure_async`:两项都 None 就不写),也不影响之后的对话剧本。

    注意别把 "multimodal" 之类的词写进这段文本 —— 那会被 `probe_vision` 的
    `_NO_VISION_HINTS` 命中,得出「不支持视觉」这个同样没根据的结论。
    """
    body = b'{"error": {"message": "probe not answered"}}'
    try:
        handler.send_response(401)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass                               # 客户端提前断开是常态
