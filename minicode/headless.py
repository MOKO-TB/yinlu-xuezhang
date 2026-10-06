"""无头模式(`--json`):一条命令跑完就退出,输出是给**机器**读的。

三条必须先讲清的口径,它们决定了这个模块为什么长这样:

1. **stdout 只放那一份 JSON,别的一个字都不许有。** 横幅、进度、错误、MCP 日志
   全部改道 stderr —— 不是"整洁偏好",而是契约:调用方拿到的是
   `json.loads(stdout)`,任何一个多余字符都会让它解析失败,而失败的样子和
   "任务没做成"长得一模一样。
2. **没有人能点头的时候,一律按拒绝,绝不自动放行。** 这不是新规矩:
   `TerminalUI.confirm_tool` 的 docstring 早就写着"非交互场景一律按拒绝处理"
   (管道输入、Ctrl+C、非交互桌面站都走这条路)。无头模式只是把同一条判断
   **说出口**——记进 `asks_refused` 并给一个专属退出码,而不是让调用方以为
   "跑完了 = 每一步都做成了"。竞品里 Codex 的 `--prompt` 默认 `yolo` 是反例:
   那等于"换了个入口就绕过确认",与本项目 v0.9.114 定下的
   「自动化不是绕过确认的第二条路」直接冲突,所以不做。
3. **退出码要能分得开"没做成"的几种样子。** 只有 0/1 两个码(也是 Codex 的形状)
   会把"模型报错""权限没人批""被上限收尾""按了 Ctrl+C"压成同一个数,
   脚本作者只能回去读文本 —— 那正是要省掉的那一步。
"""
from __future__ import annotations

import json
import sys
from typing import Any

from minicode import toolsum

OUTPUT_FORMATS = ("text", "json")

# 退出码:每个值只对应一种结局,且都能在结果对象里找到对应字段。
EXIT_OK = 0
EXIT_RUNTIME = 1        # 模型/网络报错,这一轮没能答完
EXIT_ARGS = 2           # 命令行参数不对(与 --py / --workspace 的既有 2 同一口径)
EXIT_ASK_REFUSED = 3    # 需要确认,但这条路上没有人能点头
EXIT_LIMITED = 4        # 被上限收尾(步数 / 时限 / 预算 / 同一工具连挂)
EXIT_INTERRUPTED = 5    # Ctrl+C 或管道关闭


class UsageError(Exception):
    """参数层面的错:说清哪儿不对,以 EXIT_ARGS 退出,不进 Agent 一步。"""


def pop_output_flags(argv: list[str]) -> tuple[str, list[str]]:
    """摘出输出格式旗标,其余参数原样返回(留给一次性任务)。

    认 `--json`、`--output-format json`、`--output-format=json` 三种写法。
    **两个都给时 `--output-format` 胜出** —— 显式的比简写的说得清楚。
    认不出的值**直接报错**,不静默退回 text:拼错了却以为自己解析成功,
    是这类旗标最贵的一种坏法。
    """
    fmt, rest, i = "text", [], 0
    while i < len(argv):
        item = argv[i]
        if item == "--json":
            fmt = "json"
            i += 1
            continue
        if item == "--output-format" or item.startswith("--output-format="):
            if "=" in item:
                value = item.split("=", 1)[1]
                i += 1
            else:
                if i + 1 >= len(argv):
                    raise UsageError("--output-format 需要一个值(text / json)")
                value = argv[i + 1]
                i += 2
            if value not in OUTPUT_FORMATS:
                raise UsageError(
                    f"--output-format 不认 {value!r},可选 {' / '.join(OUTPUT_FORMATS)}"
                    "(stream-json 没做:事件流要的是逐段回灌界面,无头模式没有界面)")
            fmt = value
            continue
        rest.append(item)
        i += 1
    return fmt, rest


class HeadlessUI:
    """包一层真 UI:吞掉一切渲染,只记账,并把所有"要人点头"的入口答成不点头。

    为什么是包装而不是另写一份 UI:agent 想知道的结局**全部**从界面回调里出来
    (`llm_error` / `max_steps_reached` / `turn_time_limited` / `token_limit_reached`
    / `fail_streak_limit` / `turn_interrupted`),`run_turn` 自己不吃异常也不重抛。
    另写一份就会有两套"这一轮怎么结束的"判断 —— 而这两套总有一天会不一致。
    没想到的方法一律转给内层(`__getattr__`),不会因为少写一个钩子就 AttributeError。
    """

    def __init__(self, inner: Any):
        self.inner = inner
        self.text: list[str] = []          # 流式正文(只为"有没有吐过字"留证据)
        self.tool_calls: list[dict] = []
        self.asks_refused: list[dict] = []
        self.hard_rejected: list[str] = []
        self.limits: list[dict] = []
        self.interrupted = False
        self.llm_error: str = ""
        self.compactions: list[dict] = []
        self._pending: dict = {}

    # ------------------------------------------------------------- 吞掉渲染

    def banner(self, key_ok: bool) -> None:
        pass

    def info(self, text: str) -> None:
        self._note(text)

    def error(self, text: str) -> None:
        self._note(text)

    def on_text_delta(self, delta: str) -> None:
        self.text.append(delta)

    def on_reasoning_delta(self, text: str) -> None:
        pass

    def finish_stream(self) -> None:
        pass

    def tool_progress(self, text: str) -> None:
        pass

    def on_retry(self, e: Exception, attempt: int) -> None:
        self._note(f"重试第 {attempt} 次:{type(e).__name__}")

    @staticmethod
    def _note(text: str) -> None:
        # 诊断走 stderr:stdout 那一份 JSON 不能被任何人顺嘴一句话污染。
        print(str(text), file=sys.stderr, flush=True)

    # ------------------------------------------------------- 工具调用:记账

    def tool_start(self, tool, args: dict) -> None:
        self._pending = {"name": tool.name, "args": _brief(args)}

    def tool_done(self, tool, result: str) -> None:
        row = dict(self._pending or {"name": tool.name})
        row.pop("args", None)
        row["ok"] = not toolsum.is_failure(str(result))
        row["chars"] = len(str(result))
        self.tool_calls.append(row)
        self._pending = {}

    def tool_rejected_hard(self, tool, args: dict) -> None:
        # 硬拒是红线挡的,不是"没人点头"挡的 —— 两件事分开记。
        self.hard_rejected.append(tool.name)

    # ----------------------------------------------- 要人点头的入口:都不点头

    def confirm_tool(self, tool, args: dict, reason: str = "",
                     note: str = "", label: str = "") -> str:
        self.asks_refused.append({"name": tool.name, "reason": reason or note,
                                  "label": label, "args": _brief(args)})
        return "no"

    def confirm_destructive(self, detail: str, question: str = "确定?") -> bool:
        self.asks_refused.append({"name": "(destructive)", "reason": detail,
                                  "label": question, "args": {}})
        return False

    def ask_user(self, questions: list, note: str = "", label: str = "") -> dict:
        from minicode import asking
        for q in questions or []:
            self.asks_refused.append({"name": "(ask_user)",
                                      "reason": str(q.get("q") or ""),
                                      "label": label, "args": {}})
        return {"mode": asking.MODE_UNANSWERED,
                "reason": "无头模式没有人能回答",
                "answers": []}

    # --------------------------------------------------- 这一轮是怎么结束的

    def llm_error(self, e: Exception) -> None:
        self.llm_error = f"{type(e).__name__}: {e}"

    def turn_interrupted(self) -> None:
        self.interrupted = True

    def max_steps_reached(self, limit: int) -> None:
        self.limits.append({"kind": "max_steps", "limit": limit})

    def turn_time_limited(self, seconds: int, steps: int) -> None:
        self.limits.append({"kind": "time_limit", "limit": seconds, "steps": steps})

    def fail_streak_limit(self, tool: str, limit: int) -> None:
        self.limits.append({"kind": "fail_streak", "tool": tool, "limit": limit})

    def token_limit_reached(self, limit: int, used: int) -> None:
        self.limits.append({"kind": "token_budget", "limit": limit, "used": used})

    def compacted(self, before: int, after: int) -> None:
        self.compactions.append({"before": before, "after": after})

    def __getattr__(self, name: str):
        # 只在本类查不到属性时触发;self.inner 在 __init__ 里已设好,不会绕回来。
        return getattr(self.inner, name)


def _brief(args: dict, cap: int = 160) -> dict:
    """把工具参数收成"能指认是哪一步"的那几个字段,不整包外发。

    无头输出常被贴进 CI 日志,而 `write_file` 的 `content` 可能装着同学的课件原文
    或凭据 —— 记键名与长度就够定位了,正文不进机器可读的那份结果。
    """
    out: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if key in ("content", "new_string", "old_string", "text", "body"):
            out[key + "_chars"] = len(str(value))
            continue
        out[key] = str(value)[:cap]
    return out


def exit_code_for(ui: HeadlessUI) -> int:
    """结局 → 退出码。优先级照"离真相最近"排,不按发生先后。"""
    if ui.interrupted:
        return EXIT_INTERRUPTED
    if ui.llm_error:
        return EXIT_RUNTIME
    if ui.limits:
        return EXIT_LIMITED
    if ui.asks_refused:
        return EXIT_ASK_REFUSED
    return EXIT_OK


def build_result(ui: HeadlessUI, agent, session, config, *, task: str,
                 elapsed_ms: int, fmt: str) -> dict:
    """攒出那一份给机器读的结果。字段一次给全,拿不到的给 null 而不是不写。"""
    answer = ""
    for msg in reversed(getattr(session, "messages", []) or []):
        if msg.get("role") == "assistant" and msg.get("content"):
            answer = str(msg["content"])
            break
    used = window = None
    try:
        from minicode.model_catalog import context_reading
        used, _source = context_reading(agent, session)
        window = getattr(config, "context_window", None) or None
    except Exception:
        pass
    log = getattr(session, "log_path", None)
    return {
        "ok": exit_code_for(ui) == EXIT_OK,
        "status": status_for(ui),
        "exit_code": exit_code_for(ui),
        "format": fmt,
        "task": task,
        "response": answer,
        "model": getattr(config, "model", ""),
        # `config.provider` 是**返回 Provider 对象**的 property,而 Provider 是
        # dataclass、repr 里带 api_key —— 走 default=str 就等于把密钥打进一份常被
        # 贴进 CI 日志的输出里。只给 id。
        "provider": getattr(config, "active_provider_id", "") or "",
        "workspace": str(getattr(agent, "ctx", None) and
                         getattr(agent.ctx, "workspace", "") or ""),
        "session_file": str(log) if log else None,
        "elapsed_ms": elapsed_ms,
        "turn_tokens": int(getattr(agent, "turn_tokens", 0) or 0),
        "context_used": used,
        "context_window": window,
        "tool_calls": ui.tool_calls,
        "asks_refused": ui.asks_refused,
        "hard_rejected": ui.hard_rejected,
        "limits": ui.limits,
        "interrupted": ui.interrupted,
        "error": ui.llm_error or None,
        "compactions": ui.compactions,
    }


def status_for(ui: HeadlessUI) -> str:
    if ui.interrupted:
        return "interrupted"
    if ui.llm_error:
        return "llm_error"
    if ui.limits:
        return "limited"
    if ui.asks_refused:
        return "ask_refused"
    return "ok"


def render(result: dict) -> str:
    """那一份 JSON。`ensure_ascii=False` 是必须的:中文被转成 \\uXXXX 会让
    人眼读不了、却让机器照样能解 —— 最难查的一种"看起来没问题"。"""
    return json.dumps(result, ensure_ascii=False, indent=1, default=str)
