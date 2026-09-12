"""引路学长 CLI 入口:加载配置 → 构建 Agent → REPL 循环。

用法:
  python main.py               交互式 REPL
  python main.py "任务描述"     一次性执行后退出
  echo "任务" | python main.py 从管道读取一条任务执行后退出
"""
from __future__ import annotations

import platform
import re
import sys
import time
from pathlib import Path

from minicode.app_setup import create_agent
from minicode.compaction import estimate_tokens
from minicode.config import load_config
from minicode.paths import SESSIONS_DIR
from minicode.session import Session, list_sessions
from minicode.ui import TerminalUI


def handle_slash(cmd: str, session: Session, config, ui: TerminalUI,
                 agent: Agent) -> bool:
    """处理斜杠命令;返回 True 表示要退出程序。"""
    parts = cmd.split()
    name = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    if name in ("/exit", "/quit"):
        return True
    if name == "/help":
        ui.help_text()
    elif name == "/clear":
        session.clear()
        ui.info("会话已清空。")
    elif name == "/model":
        from minicode.config import API_KEY_PLACEHOLDERS
        lines = ["[bold]供应商[/bold](切换请编辑 config.json 的 active_provider)"]
        for p in config.providers:
            active = p.id == (config.provider.id if config.provider else "")
            mark = "[cyan]●[/cyan] " if active else "○ "
            key = "[green]已配置[/green]" if p.api_key.strip() not in API_KEY_PLACEHOLDERS else "[red]未配置 key[/red]"
            lines.append(f"  {mark}{p.name}({p.id}) · {p.model} · {key} · {p.base_url}")
        ui.info("\n".join(lines))
    elif name == "/skills":
        from minicode.skills import load_skills, skill_dirs
        skills = load_skills(skill_dirs(workspace))
        if not skills:
            ui.info("还没有安装技能。把技能文件夹(含 SKILL.md)放进程序目录的 skills/ 即可。")
        else:
            ui.info("[bold]可用技能[/bold]\n" + "\n".join(
                f"  • {s.name} —— {s.description or '(无描述)'}" for s in skills))
    elif name == "/kb":
        from minicode.kb import kb_stats, kb_dirs
        stats = kb_stats(kb_dirs(workspace))
        if not stats:
            ui.info("知识库为空。把 JSON 数据文件放进程序目录的 kb/ 即可(notices/knowledge 格式)。")
        else:
            total = sum(s["count"] for s in stats)
            ui.info("[bold]本地知识库[/bold] 共 "
                    + f"{total} 条\n" + "\n".join(
                        f"  • {s['file']}: {s['count']} 条" for s in stats)
                    + "\n  提问相关话题时,Agent 会用 search_knowledge 检索并给出来源网址。")
    elif name == "/history":
        est = estimate_tokens(session.messages)
        threshold = int(config.context_window * config.compact_at_ratio)
        ui.info(f"当前上下文 {len(session.messages)} 条消息,约 {est} tokens"
                f"(压缩阈值 {threshold} tokens)。会话留档在 sessions/ 目录。")
    elif name == "/resume":
        summaries = list_sessions(SESSIONS_DIR)
        if not summaries:
            ui.info("还没有历史会话。")
        elif not arg.isdigit():
            ui.list_sessions(summaries)
        elif not 1 <= int(arg) <= len(summaries):
            ui.error(f"序号超出范围(1-{len(summaries)})。")
        else:
            chosen = summaries[int(arg) - 1]
            n = session.load_from(chosen.path)
            ui.info(f"已恢复 {n} 条消息(来自 {chosen.path.name});"
                    f"后续对话继续写入该文件。")
    elif name == "/undo":
        ui.info(agent.undo())
    elif name == "/compact":
        agent.compact_now()
    else:
        ui.error(f"未知命令 {name}。可用: /help /clear /model /history "
                 f"/resume /undo /compact /exit")
    return False


SLASH_NAMES = {"/help", "/clear", "/model", "/history", "/resume",
               "/undo", "/compact", "/exit", "/quit"}
# Git Bash(MSYS)会把 "/cmd" 形式的参数转成 Git 安装目录下的路径,
# 如 "D:/git/Git/history" 或 "C:/Program Files/Git/history"
_MSYSED_COMMAND = re.compile(r"(?i)^[A-Z]:[\\/].*git[\\/]([^\\/]+)$")


def unmanagle_slash_command(argv: list[str]) -> str:
    """把被 MSYS 路径化的斜杠命令参数还原(仅影响命令行一次性模式)。

    在 argv 数组层面处理:转换出的路径可能含空格,先 join 再判断会拆散。
    """
    if not argv:
        return ""
    parts = list(argv)
    first = parts[0]
    if not first.startswith("/"):
        m = _MSYSED_COMMAND.match(first)
        if m and not Path(first).exists():
            candidate = f"/{m.group(1).lower()}"
            if candidate in SLASH_NAMES:
                parts[0] = candidate
    return " ".join(parts)


def run_once(agent: Agent, ui: TerminalUI, task: str) -> None:
    agent.run_turn(task)


def main() -> None:
    import platform
    import time

    config = load_config()
    workspace = Path.cwd().resolve()
    ui = TerminalUI(config, workspace)
    ui.banner(key_ok=config.api_key_configured)

    agent, session = create_agent(config, workspace, ui)

    # 一次性模式:命令行参数或管道输入(斜杠命令同样支持)
    task = unmanagle_slash_command(sys.argv[1:]) if len(sys.argv) > 1 else None
    if task is None and not sys.stdin.isatty():
        task = sys.stdin.read().strip() or None
    if task is not None:
        if task.startswith("/"):
            handle_slash(task, session, config, ui, agent)
        else:
            run_once(agent, ui, task)
        return

    while True:
        try:
            user_input = ui.prompt()
        except (KeyboardInterrupt, EOFError):
            ui.info("再见!")
            break
        text = user_input.strip()
        if not text:
            continue
        if text.startswith("/"):
            if handle_slash(text, session, config, ui, agent):
                ui.info("再见!")
                break
            continue
        agent.run_turn(text)


if __name__ == "__main__":
    main()
