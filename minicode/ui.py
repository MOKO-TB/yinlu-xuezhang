"""终端界面:流式 Markdown 渲染、工具卡片、权限确认、输入。"""
from __future__ import annotations

import difflib
import time
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from minicode import __version__
from minicode.config import Config
from minicode.tools.base import Tool

SLASH_COMMANDS = ["/help", "/clear", "/model", "/history", "/resume",
                  "/undo", "/compact", "/skills", "/exit"]

REFRESH_INTERVAL = 0.12     # 流式 Markdown 刷新节流
DIFF_MAX_LINES = 40
PREVIEW_CONTENT_LINES = 15


class TerminalUI:
    def __init__(self, config: Config, workspace: Path):
        self.config = config
        self.workspace = workspace
        self.console = Console(highlight=False)

        history_file = Path.home() / ".mini-code" / "history"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        self._history_file = history_file
        self._completer = WordCompleter(SLASH_COMMANDS, sentence=True)
        self._prompt_session: PromptSession | None = None  # 惰性创建:非 TTY 环境下无法初始化

        self._live: Live | None = None
        self._buf = ""
        self._last_flush = 0.0
        self._tool_t0 = 0.0

    # ---------------------------------------------------------------- 输入

    def prompt(self) -> str:
        if self._prompt_session is None:
            try:
                self._prompt_session = PromptSession(
                    history=FileHistory(str(self._history_file)),
                    completer=self._completer,
                )
            except Exception:
                return input("❯ ")
        try:
            return self._prompt_session.prompt(
                [("class:prompt", "❯ ")],
                complete_while_typing=True,
            )
        except EOFError:
            raise
        except Exception:
            return input("❯ ")

    # ------------------------------------------------------------ 流式输出

    def on_text_delta(self, delta: str) -> None:
        if self._live is None:
            self._live = Live(console=self.console, refresh_per_second=12,
                              vertical_overflow="visible")
            self._live.start()
        self._buf += delta
        now = time.monotonic()
        if now - self._last_flush >= REFRESH_INTERVAL:
            self._last_flush = now
            self._live.update(Markdown(self._buf))

    def on_reasoning_delta(self, text: str) -> None:
        # 思考流通常出现在正文之前;若已在渲染正文则不再穿插显示
        if self._live is None:
            self.console.print(text, end="", style="dim")

    def finish_stream(self) -> None:
        if self._live is not None:
            self._live.update(Markdown(self._buf))
            self._live.stop()
            self._live = None
        self._buf = ""
        self.console.print()  # 与下一轮输出隔开

    # ------------------------------------------------------------ 工具展示

    def tool_start(self, tool: Tool, args: dict) -> None:
        self._tool_t0 = time.monotonic()
        self.console.print(
            f"[bold cyan]●[/bold cyan] [bold]{tool.name}[/bold] "
            f"[dim]{escape(self._arg_summary(tool, args))}[/dim]"
        )

    def tool_done(self, tool: Tool, result: str) -> None:
        dt = time.monotonic() - self._tool_t0
        failed = result.startswith(("错误", "工具执行出错", "用户拒绝"))
        mark = "[red]✗[/red]" if failed else "[green]✓[/green]"
        self.console.print(f"  {mark} [dim]{dt:.1f}s[/dim]")
        if failed:
            self.console.print(f"  [red]{escape(result[:300])}[/red]")

    def tool_rejected_hard(self, tool: Tool, args: dict) -> None:
        self.console.print(Panel(
            f"命令: {escape(args.get('command', ''))}",
            title="[red]🚫 安全策略硬拒绝[/red]",
            border_style="red",
        ))

    def _arg_summary(self, tool: Tool, args: dict) -> str:
        if tool.name == "bash":
            s = args.get("command", "")
        elif tool.name in ("write_file", "edit_file", "read_file"):
            s = str(args.get("path", ""))
        else:
            s = "  ".join(f"{k}={str(v)[:40]}" for k, v in args.items())
        return s[:120]

    # ------------------------------------------------------------ 权限确认

    def confirm_tool(self, tool: Tool, args: dict) -> str:
        """返回 'yes' | 'no' | 'always'。"""
        self.console.print()
        self.console.print(Panel(
            self._permission_detail(tool, args),
            title=f"[yellow]⚠ 权限确认 · {tool.name}[/yellow]",
            border_style="yellow",
        ))
        while True:
            ans = self.console.input(
                "[yellow]允许执行?[/yellow] [bold](y)是[/bold] / [bold](n)否[/bold] "
                "/ [bold](a)本轮总是允许[/bold]: "
            ).strip().lower()
            if ans in ("y", "yes"):
                return "yes"
            if ans in ("a", "always"):
                return "always"
            if ans in ("n", "no", ""):
                return "no"
            self.console.print("[dim]请输入 y、n 或 a[/dim]")

    def _permission_detail(self, tool: Tool, args: dict) -> Text:
        if tool.name == "bash":
            return Text(f"$ {args.get('command', '')}")

        if tool.name == "write_file":
            path = str(args.get("path", "?"))
            content = str(args.get("content", ""))
            target = self.workspace / path
            lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            head = f"{'覆盖已有文件' if target.exists() else '新建文件'}: {path}({lines} 行)\n"
            body = Text("\n".join(content.splitlines()[:PREVIEW_CONTENT_LINES]), style="dim")
            if content.count("\n") >= PREVIEW_CONTENT_LINES:
                body.append("\n…", style="dim")
            return Text(head) + body

        if tool.name == "edit_file":
            path = str(args.get("path", "?"))
            old = str(args.get("old_string", ""))
            new = str(args.get("new_string", ""))
            p = self.workspace / path
            current = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
            after = current.replace(old, new, 1)
            diff = list(difflib.unified_diff(
                current.splitlines(), after.splitlines(), lineterm="", n=2))[2:]
            text = Text(f"{path}\n")
            for ln in diff[:DIFF_MAX_LINES]:
                style = ("green" if ln.startswith("+")
                         else "red" if ln.startswith("-") else "dim")
                text.append(ln[:200] + "\n", style=style)
            if len(diff) > DIFF_MAX_LINES:
                text.append(f"…(diff 共 {len(diff)} 行)\n", style="dim")
            return text

        return Text(str(args)[:500])

    # ------------------------------------------------------------ 提示信息

    def banner(self, key_ok: bool) -> None:
        mode = self.config.permission_mode
        mode_hint = ("变更类操作会先请求确认" if mode == "confirm"
                     else "变更类操作自动执行,请谨慎使用")
        lines = [
            f"[bold]引路学长[/bold] [dim]v{__version__} · 编程引路智能体[/dim]",
            f"模型  {self.config.model}  [dim]@ {self.config.base_url}[/dim]",
            f"工作区  {self.workspace}",
            f"权限  {mode}({mode_hint})· /help 查看命令",
        ]
        self.console.print(Panel("\n".join(lines), border_style="cyan"))
        if not key_ok:
            self.console.print(
                "[bold red]⚠ 尚未配置 api_key:[/bold red]请编辑项目根目录的 "
                "[bold]config.json[/bold] 填入密钥后重启。"
            )

    def help_text(self) -> None:
        self.console.print(
            "[bold]命令[/bold]\n"
            "  /help         显示本帮助\n"
            "  /clear        清空当前会话上下文\n"
            "  /model        查看模型与供应商列表\n"
            "  /history      查看上下文消息数与 token 估算\n"
            "  /resume [<n>] 查看/恢复历史会话\n"
            "  /undo         撤销最近一次文件改动(可连续;bash 改动不在跟踪范围)\n"
            "  /compact      立即压缩上下文(平时到达阈值会自动压缩)\n"
            "  /skills       查看可用技能(SKILL.md)\n"
            "  /exit         退出(或 Ctrl+D)\n"
            "\n[bold]说明[/bold]\n"
            "  Ctrl+C  中断当前回合\n"
            "  直接输入任务即可,Agent 会自动调用工具;写入/执行命令前会先请求确认。"
        )

    def compacted(self, before: int, after: int) -> None:
        self.console.print(
            f"[yellow]✳ 上下文已压缩:{before} 条消息 → 摘要 + 最近几轮原文"
            f"(现共 {after} 条)[/yellow]"
        )

    def list_sessions(self, summaries) -> None:
        if not summaries:
            self.info("还没有历史会话。")
            return
        lines = ["[bold]最近会话[/bold] [dim](用 /resume <序号> 恢复)[/dim]"]
        for i, s in enumerate(summaries, 1):
            preview = escape(s.last_user_text or "(无用户消息)")
            lines.append(
                f"  [cyan]{i}[/cyan]) {escape(s.time)}  {s.n_messages} 条消息  最后任务: {preview}"
            )
        self.console.print("\n".join(lines))

    def info(self, text: str) -> None:
        self.console.print(f"[dim]{escape(text)}[/dim]")

    def error(self, text: str) -> None:
        self.console.print(f"[red]{escape(text)}[/red]")

    def on_retry(self, e: Exception, attempt: int) -> None:
        self.console.print(f"[yellow]请求失败({type(e).__name__}),"
                           f"第 {attempt} 次重试…[/yellow]")

    def turn_interrupted(self) -> None:
        self.finish_stream()
        self.console.print("[yellow]⚠ 已中断当前回合[/yellow]")

    def llm_error(self, e: Exception) -> None:
        self.finish_stream()
        hint = ""
        if "401" in str(e) or "api key" in str(e).lower():
            hint = "(请检查 config.json 中的 api_key)"
        self.error(f"模型调用失败: {e} {hint}")

    def max_steps_reached(self, limit: int) -> None:
        self.console.print(
            f"[yellow]⚠ 本回合工具调用已达 {limit} 轮上限,强制停止。"
            f"可继续对话让它接着做。[/yellow]"
        )
