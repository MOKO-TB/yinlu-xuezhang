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

# 终端里"给人看的命令表"这一份唯一真相(v0.9.103):补全、未知命令提示、
# `main.SLASH_NAMES` 三处都从它派生。以前这三处各抄一份,于是 /find /permission
# /think /workspace 能打却补不出来 —— 补全表落后了四条没人知道。
# 帮助文本仍是各条带说明的那一份,由 tests/test_main.py 的静态闸核"每条都有说明行"。
SLASH_COMMANDS = ["/help", "/clear", "/model", "/history", "/resume", "/find",
                  "/undo", "/compact", "/sandbox", "/permission", "/think",
                  "/instructions", "/rewind", "/snapshots", "/restore", "/memory", "/remember", "/dream",
                  "/plan",
                  "/skills", "/kb", "/library", "/style", "/usage", "/browser",
                  "/workspace", "/rename", "/archive", "/unarchive", "/fork", "/exit", "/quit"]

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

    def tool_progress(self, text: str) -> None:
        """长命令每 `shell.PROGRESS_EVERY` 秒一行(与 GUI 同一份节流,不是逐行刷屏)。

        GUI 那边是原地改「此刻」那一行,终端只能往上追加 —— 所以间隔由调用方(shell)
        统一管,这里**不自己再判一次要不要打印**:两处各判总有一天不一样。
        """
        self.console.print(f"  [dim]↳ {escape(str(text or ''))}[/dim]")

    def tool_done(self, tool: Tool, result: str) -> None:
        dt = time.monotonic() - self._tool_t0
        # 与 GUI 那侧同一个口径:失败判定与截断长度都取自 toolsum
        from minicode import toolsum
        failed = toolsum.is_failure(result)
        mark = "[red]✗[/red]" if failed else "[green]✓[/green]"
        self.console.print(f"  {mark} [dim]{dt:.1f}s[/dim]")
        if failed:
            self.console.print(f"  [red]{escape(toolsum.error_preview(result))}[/red]")

    def tool_rejected_hard(self, tool: Tool, args: dict) -> None:
        self.console.print(Panel(
            f"命令: {escape(args.get('command', ''))}",
            title="[red]🚫 安全策略硬拒绝[/red]",
            border_style="red",
        ))

    def _arg_summary(self, tool: Tool, args: dict) -> str:
        # 与 GuiUI 同一口径(minicode.toolsum),只是终端一行更窄。
        # 原来这里自己抄了一份 if/elif,已经和 GUI 那份差在截断长度上 ——
        # 再被时间线抄第三份,就会出现"终端说改了 a.py、监控栏写着别的"。
        from minicode import toolsum
        return toolsum.summarize(tool.name, args, limit=120)

    # ------------------------------------------------------------ 权限确认

    def confirm_tool(self, tool: Tool, args: dict, reason: str = "",
                     note: str = "", label: str = "",
                     can_remember: bool = False) -> str:
        """返回 'yes' | 'no' | 'always' | 'remember'。reason=模型说明的"这一步为什么做"。

        `label` 是并发子代理路的标签(v0.9.60,如「第2路[implement] 修复登录」):
        CLI 里拼进面板标题,让用户分得清是哪一路在等确认。
        `can_remember` 为真才多出 (r) 那一项(v0.9.133):这一步收窄得成一条本机
        免确认规则才给这颗按钮 —— 给了又点不成的按钮就是样子货。
        非交互场景(stdin 关闭、管道输入、Ctrl+C)一律按"拒绝"处理并说明原因,
        不能让确认框把整个进程带崩。
        """
        from minicode.explain import permission_why
        why = permission_why(tool.name, args, reason)
        self.console.print()
        title = f"[yellow]⚠ 权限确认 · {tool.name}[/yellow]"
        if label:
            title = f"[yellow]⚠ 权限确认 · {tool.name} · {label}[/yellow]"
        self.console.print(Panel(
            self._permission_detail(tool, args, why),
            title=title,
            border_style="red" if note else "yellow",
        ))
        if note:
            self.console.print(f"  [bold red]{escape(note)}[/bold red]")
        if can_remember:
            self.console.print(
                "[dim]  (r) 以后都允许:只记在这台机器上、只对这一个项目,"
                "不动项目里的任何文件;撤销用 permissions_trust action=revoke。[/dim]")
        while True:
            choices = "[yellow]允许执行?[/yellow] [bold](y)是[/bold] / [bold](n)否[/bold] " \
                      "/ [bold](a)本轮总是允许[/bold]"
            if can_remember:
                choices += " / [bold](r)以后都允许[/bold]"
            try:
                ans = self.console.input(choices + ": ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self.console.print("[dim]没有可用的交互输入,已按拒绝处理。[/dim]")
                return "no"
            if ans in ("y", "yes"):
                return "yes"
            if ans in ("a", "always"):
                return "always"
            if can_remember and ans in ("r", "remember"):
                return "remember"
            if ans in ("n", "no", ""):
                return "no"
            self.console.print("[dim]请输入 "
                               + ("y、n、a 或 r" if can_remember else "y、n 或 a")
                               + "[/dim]")

    # ------------------------------------------------------------ 动手面的点头

    def confirm_destructive(self, detail: str, question: str = "确定?") -> bool:
        """人在命令行里主动发起的**改磁盘**动作(退文件)也要先点头。

        与 `confirm_tool` 同一条规矩:拿不到交互输入(管道、Ctrl+C、非 TTY)
        一律按**拒绝**处理 —— "没答"不等于"同意",在动手面上尤其不等于。
        `detail` 是磁盘上的内容拼出来的(文件路径、diff 行),里面任何方括号
        都不许被当成 rich 标签,所以走 `Text` 不走 markup。
        """
        self.console.print()
        self.console.print(Panel(
            Text(detail),
            title="[yellow]⚠ 这一步会改磁盘上的文件[/yellow]",
            border_style="yellow",
        ))
        while True:
            try:
                ans = self.console.input(
                    f"{question} [bold](y)是[/bold] / [bold](n)否[/bold]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self.console.print("[dim]没有可用的交互输入,已按拒绝处理。[/dim]")
                return False
            if ans in ("y", "yes"):
                return True
            if ans in ("n", "no", ""):
                return False
            self.console.print("[dim]请输入 y 或 n[/dim]")

    # ------------------------------------------------------------ 提问卡(终端形态)

    def ask_user(self, questions: list[dict], note: str = "",
                 label: str = "") -> dict:
        """终端版的提问卡:一题一题问,回传与 GUI 同一个形状(`asking` 判的)。

        三条与 GUI 同口径的规矩:①`EOFError`/`KeyboardInterrupt`(管道输入、Ctrl+C、
        非交互桌面站)一律按**未回答**收口,绝不猜一个选项;②「按推荐」是用户自己按的
        (`r`),不是没人按就自动替他选;③他打的其它文字原样进 `free`,不改写成选项。
        """
        from minicode import asking
        head = "[cyan]学长想问一句[/cyan]"
        if label:
            head = f"[cyan]学长想问一句 · {label}[/cyan]"
        self.console.print()
        self.console.print(Panel(Text.from_markup(head + (f"\n{note}" if note else "")),
                                 border_style="cyan"))
        answers: list[dict] = []
        for q in questions:
            body = Text()
            body.append(f"{q['q']}\n", style="bold")
            if q.get("why"):
                body.append(f"{q['why']}\n", style="dim")
            for i, opt in enumerate(q["options"], start=1):
                body.append(f"  ({i}) {opt['label']}", style="bold")
                if opt.get("note"):
                    body.append(f" —— {opt['note']}", style="dim")
                body.append("\n")
            body.append("  (r) 按推荐继续   (回车/其它文字) 我自己说一句\n", style="cyan")
            self.console.print(Panel(body, border_style="cyan"))
            multi = bool(q.get("multi"))
            hint = (f"[cyan]第 {q['id'] + 1} 题[/cyan]"
                    + ("(多选用逗号分隔,如 1,3): " if multi else ": "))
            try:
                raw = self.console.input(hint).strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print("[dim]没有可用的交互输入,这一题按未回答处理。[/dim]")
                return {"mode": asking.MODE_UNANSWERED,
                        "reason": "终端没拿到交互输入",
                        "answers": answers}
            low = raw.lower()
            if low in ("r", "recommend"):
                return {"mode": asking.MODE_RECOMMEND, "answers": answers}
            if not raw:
                answers.append({"id": q["id"], "choice": [], "free": ""})
                continue
            if multi:
                picks = [self._pick_option(q, p) for p in raw.split(",")]
                picks = [p for p in picks if p]
                if picks:
                    answers.append({"id": q["id"], "choice": picks, "free": ""})
                    continue
            else:
                one = self._pick_option(q, raw)
                if one:
                    answers.append({"id": q["id"], "choice": [one], "free": ""})
                    continue
            # 认不出编号 = 他在自己说一句话,原样带回去,不替他归到某个选项上
            answers.append({"id": q["id"], "choice": [], "free": raw})
        return {"mode": asking.MODE_ANSWER, "answers": answers}

    @staticmethod
    def _pick_option(q: dict, token: str) -> str:
        """把 "2" / "2. 模板" / 完整 label 认成选项 label;认不出回空串。"""
        token = str(token or "").strip()
        if not token:
            return ""
        head = token.split(".", 1)[0].strip()
        if head.isdigit():
            opts = q["options"]
            idx = int(head) - 1
            if 0 <= idx < len(opts):
                return opts[idx]["label"]
            return ""
        for opt in q["options"]:
            if token == opt["label"]:
                return opt["label"]
        return ""

    def _permission_detail(self, tool: Tool, args: dict,
                           why: dict | None = None) -> Text:
        header = self._why_header(why)
        return header + self._tool_detail(tool, args)
    @staticmethod
    def _why_header(why: dict | None) -> Text:
        """确认框顶部的"为什么要做 / 会影响什么":先给判断依据,再给原始命令。"""
        if not why:
            return Text()
        head = Text()
        head.append("为什么要做这一步:", style="bold yellow")
        head.append(f"{why.get('why', '')}\n")
        if why.get("digest"):
            head.append("这一步会做什么:", style="bold yellow")
            head.append(f"{why['digest']}\n")
        risks = why.get("risks") or []
        if risks:
            head.append("影响:", style="bold yellow")
            head.append(";".join(risks) + "\n")
        head.append("────── 原始内容 ──────\n", style="dim")
        return head

    def _tool_detail(self, tool: Tool, args: dict) -> Text:
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
        mode_hint = {
            "confirm": "变更类操作会先请求确认",
            "accept_edits": "工作区内普通编辑与构建命令自动执行",
            "accept_all": "变更类操作自动执行(沙箱边界外仍确认)",
            "full": "完全访问(敏感路径与灾难命令仍硬拒绝)",
        }.get(mode, "变更类操作会先请求确认")
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
            "  /model        查看当前使用的模型(GUI 顶部标识可添加/切换模型)\n"
            "  /usage [1|7|30] 查看 token 用量统计(默认近 7 天)\n"
            "  /style [normal|guide] 回复风格:normal=直接帮做;guide=讲解模式\n"
            "                (拆步骤引导你自己完成,不整段代做;适合学习场景)\n"
            "  /workspace [路径|default] 查看/切换工作区(Agent 在哪个文件夹里干活)\n"
            "  /browser      查看浏览器能力(能不能开网页/截图,连的是哪个浏览器)\n"
            "  /library      查看馆藏书目库状态;带书名就是搜书:/library 活着\n"
            "  /history      查看上下文消息数与 token 估算\n"
            "  /resume [<n>] 查看/恢复历史会话\n"
            "  /rename [<新标题>] 给**当前这条会话**改名:只改留档顶上那行名字,\n"
            "                不删内容;不带参数时看当前叫什么\n"
            "  /fork [<序号>] 从列表里那条会话复制一份开新任务(原会话不动)\n"
            "  /archive      把当前会话沉到列表末尾(归档不删:内容照旧能 /resume)\n"
            "  /unarchive    把归档的会话捞回列表原位(序号来源不变,只是不再沉底)\n"
            "  /find <关键词> 在自己的历史会话留档里搜(与模型侧 search_history 同一份)\n"
            "  /undo         列出本轮我改过的文件(逐处编号;命令改的不在这份账里)\n"
            "  /undo diff <编号> 只看某一处的前像 vs 磁盘现值\n"
            "  /undo <编号>  退那一处 · /undo last 退最近一步 · /undo all 一路退回本轮开始前"
            "(三种都要点头)\n"
            "  /compact      立即压缩上下文(平时到达阈值会自动压缩)\n"
            "  /sandbox [on|off] 查看/临时切换沙箱(持久化改 config.json)\n"
            "  /permission [档位] 查看/切换权限档位(confirm/accept_edits/accept_all/full;不带参数时同时报出项目白名单的生不生效)\n"
            "  /think [档位]     查看/切换思考档位(default/off/low/high/max,按模型能力)\n"
            "  /instructions 这一轮的指令层:内置 + 用户级/项目级各读到几行、来自哪份文件\n"
            "  /rewind <第几问> [files|talk|both] 退到那一问之前:文件与对话一起退"
            "  /snapshots                  整树检查点:影子库里有哪几次(影子库在数据目录里,不碰你仓库的 .git;H,v0.9.157)",
            "  /restore <序号>              还原到那次快照 —— **只还原、绝不删**(快照之后新建的文件原样留着)",
            "(默认 both;留档不删,想找回原会话用 /resume)\n"
            "  /memory [user] 查看长期记忆(默认本项目;user=用户级)\n"
            "  /remember <内容> 记一条用户级偏好(跨项目)\n"
            "  /dream [project|user|all] 整理记忆:去重合并(自动备份)\n"
            "  /plan         看当前计划(plans/plan.md;带验收判据的那几步会一起显示)\n"
            "  /skills       查看可用技能(SKILL.md)\n"
            "  /kb           查看本地知识库条数(提问相关话题时会自动检索)\n"
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
            ws_hint = f"  [dim]📁 {escape(str(s.workspace))}[/dim]" if s.workspace else ""
            lines.append(
                f"  [cyan]{i}[/cyan]) {escape(s.time)}  {s.n_messages} 条消息  最后任务: {preview}{ws_hint}"
            )
        self.console.print("\n".join(lines))

    def info(self, text: str) -> None:
        self.console.print(f"[dim]{escape(text)}[/dim]")

    def error(self, text: str) -> None:
        self.console.print(f"[red]{escape(text)}[/red]")

    def on_retry(self, e: Exception, attempt: int) -> None:
        # 把消息本身带上:重试这一路有两种句子 —— 一种是异常原文(网络/限流,
        # 里面常有 `[failed to parse request body]` 这种会被 rich 当成标记的东西,
        # 所以 escape 一遍),另一种是**写给人看的整句话**(已切到备选 / 摘掉某字段),
        # 只报异常类型会把后者压成「请求失败(RuntimeError)」,等于没说。
        note = " ".join(str(e).split())[:160]
        what = f"{type(e).__name__}: {escape(note)}" if note else type(e).__name__
        self.console.print(f"[yellow]请求失败({what}),"
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
        # 不承诺"接下来会给一段收尾":那次收尾调用可能失败(失败就静默退回原样),
        # 承诺了却没兑现,这条提示自己就成了假话。
        self.console.print(
            f"[yellow]⚠ 本回合工具调用已达 {limit} 轮上限,已停止调用工具。"
            f"想接着做直接说「继续」。[/yellow]"
        )

    def turn_time_limited(self, seconds: int, steps: int) -> None:
        self.console.print(
            f"[yellow]⏱ 本回合已跑 {steps} 步、超过一轮总时限 {seconds} 秒,"
            f"已停下(正在执行的那一步不会被强杀)。可继续对话让它接着做。[/yellow]"
        )

    def fail_streak_limit(self, tool: str, limit: int) -> None:
        self.console.print(
            f"[yellow]⚠ 「{tool}」已连续 {limit} 次以同样方式失败,"
            f"本轮先停在这里,不再原样重试。换个做法或说「继续」。[/yellow]"
        )

    def token_limit_reached(self, limit: int, used: int) -> None:
        self.console.print(
            f"[yellow]💰 本会话累计消耗约 {used} token,已到 "
            f"session_token_limit({limit})上限,本轮先停在这里"
            f"(正在执行的那一步不会被强杀)。可提高上限或新开会话再继续。[/yellow]"
        )

    def task_budget_reached(self, limit: int, used: int) -> None:
        self.console.print(
            f"[yellow]💰 本回合累计消耗约 {used} token,已到 "
            f"task_token_budget({limit})上限,本轮先停在这里"
            f"(正在执行的那一步不会被强杀)。说「继续」会重新起算一格。[/yellow]"
        )

    def daily_budget_reached(self, limit: int, used: int) -> None:
        self.console.print(
            f"[yellow]💰 今天累计消耗约 {used} token,已到 "
            f"daily_token_budget({limit})上限,本轮先停在这里"
            f"(正在执行的那一步不会被强杀)。可等明天,或显式选择今天继续。[/yellow]"
        )
