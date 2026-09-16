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
from minicode import model_probe
from minicode.paths import SESSIONS_DIR
from minicode import workspace as ws
from minicode.session import Session, list_sessions
from minicode.ui import TerminalUI


def handle_slash(cmd: str, session: Session, config, ui: TerminalUI,
                 agent: Agent) -> bool:
    """处理斜杠命令;返回 True 表示要退出程序。"""
    workspace = agent.ctx.workspace      # /plan /skills /kb /memory 等都要用
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
        p = config.provider
        lines = ["当前模型(校内分发版固定使用,界面不提供切换)"]
        if p:
            key = "已配置" if p.api_key.strip() not in API_KEY_PLACEHOLDERS else "未配置 key"
            lines.append(f"  ● {p.name}({p.id}) · {p.model} · {key} · {p.base_url}")
        others = [q for q in config.providers if p is None or q.id != p.id]
        if others:
            lines.append(f"  (config.json 里另有 {len(others)} 个供应商配置,保留但不激活)")
        ui.info("\n".join(lines))
    elif name == "/sandbox":
        state = getattr(agent.ctx, "sandbox", None)
        if state is None:
            ui.info("沙箱未启用(工具只允许访问工作区内路径)。")
        else:
            if arg.lower() in ("on", "off"):
                state.policy.enabled = arg.lower() == "on"
                ui.info("已切换(仅本次运行生效;要持久化请改 config.json 的 sandbox 段)。")
            ui.info(state.describe())
    elif name == "/memory":
        from minicode.memory import list_entries
        ui.info(list_entries(workspace, "", arg or "project"))
    elif name == "/remember":
        from minicode.memory import save_entry
        text = " ".join(parts[1:]).strip()
        ui.info(save_entry(workspace, "user", text, "user") if text
                else "用法: /remember 要记住的偏好或事实(存到用户级记忆)")
    elif name == "/dream":
        from minicode.memory import dream, dream_all
        scope = (arg or "project").strip().lower()
        llm = getattr(agent.ctx, "llm", None)
        ui.info(dream_all(workspace, llm) if scope in ("all", "both", "全部")
                else dream(workspace, scope, llm))
    elif name == "/plan":
        from minicode.planning import PLAN_REL
        p = workspace / PLAN_REL
        if p.exists():
            ui.info(p.read_text(encoding="utf-8"))
        else:
            ui.info("当前没有计划文件(工作区 plans/plan.md)。")
    elif name == "/skills":
        from minicode.skills import load_skills, skill_dirs
        skills = load_skills(skill_dirs(workspace))
        if not skills:
            ui.info("还没有安装技能。把技能文件夹(含 SKILL.md)放进程序目录的 skills/ 即可。")
        else:
            ui.info("可用技能\n" + "\n".join(
                f"  • {s.name} —— {s.description or '(无描述)'}" for s in skills))
    elif name == "/kb":
        from minicode.kb import kb_stats, kb_dirs
        stats = kb_stats(kb_dirs(workspace))
        if not stats:
            ui.info("知识库为空。把 JSON 数据文件放进程序目录的 kb/ 即可(notices/knowledge 格式)。")
        else:
            total = sum(s["count"] for s in stats)
            ui.info("本地知识库 共 "
                    + f"{total} 条\n" + "\n".join(
                        f"  • {s['file']}: {s['count']} 条" for s in stats)
                    + "\n  提问相关话题时,Agent 会用 search_knowledge 检索并给出来源网址。")
    elif name == "/usage":
        from minicode import usage as usage_mod
        days = int(arg) if arg.isdigit() and int(arg) in (1, 7, 30) else 7
        s = usage_mod.statistics(days)
        if not s.get("ok"):
            ui.error(s.get("msg", "统计不可用"))
        else:
            r, sum_ = s["range"], s["summary"]
            lines = [f"Token 用量({r['start_date']} 至 {r['end_date']},共 {r['days']} 天)",
                     f"  合计: {sum_['total']:,} tokens(输入 {sum_['prompt']:,} / "
                     f"输出 {sum_['completion']:,})· {sum_['requests']} 次请求"]
            for m in s["models"]:
                lines.append(f"  • {m['model'] or m['key']}: {m['total']:,} tokens"
                             f"(输入 {m['prompt']:,} / 输出 {m['completion']:,})")
            if not s["models"]:
                lines.append("  暂无记录(服务端返回 usage 后自动统计;GUI 顶部「📊 用量」"
                             "可看每日圆环图)")
            ui.info("\n".join(lines))
    elif name == "/library":
        from minicode.library import catalog_health, health_notice
        health = catalog_health(workspace)
        notice = health_notice(health)
        lines = [f"馆藏库状态: {health.get('status')}",
                 f"  库文件: {health.get('path')}"]
        if health.get("total") is not None:
            lines.append(f"  书目: {health['total']} 条")
        if health.get("size") is not None:
            lines.append(f"  大小: {health['size'] / 1e9:.2f} GB")
        if health.get("expected"):
            lines.append(f"  应为: {health['expected'] / 1e9:.2f} GB")
        if notice:
            lines.append(notice)
        else:
            lines.append("  ✓ 图书馆搜书可用")
        lines.append("  逐项核对用:python scripts/check_library_db.py <安装目录>")
        ui.info("\n".join(lines))
    elif name == "/browser":
        from minicode import browser as browser_mod
        h = browser_mod.health()
        lines = [f"浏览器能力: {'✓ 可用' if h['ok'] else '✗ 不可用'}",
                 f"  浏览器: {h['browser'] or '(没找到 Edge/Chrome)'}",
                 f"  接入方式: {h['mode'] or '(未确定)'}",
                 f"  说明: {h['detail'] or '-'}"]
        if h["ok"]:
            lines.append("  Agent 默认会自己起一个独立无窗口实例(不带你现有登录态);"
                         "要看需登录的页面时它会用 mode=attach 连你自己的浏览器。")
        else:
            lines.append("  修复:装一个 Edge/Chrome(或设环境变量 YINLUXS_BROWSER 指向浏览器 exe);"
                         "若只想用已开远程调试的浏览器,可设 YINLUXS_CDP_URL=http://127.0.0.1:9222。")
        ui.info("\n".join(lines))
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
                     f"/resume /undo /compact /sandbox /memory /remember /dream "
                     f"/plan /skills /kb /library /usage /browser /exit")
    return False


SLASH_NAMES = {"/help", "/clear", "/model", "/history", "/resume",
               "/undo", "/compact", "/sandbox", "/memory", "/remember",
               "/dream", "/skills", "/kb", "/plan", "/library", "/usage",
               "/browser", "/workspace",
               "/exit", "/quit"}
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


WORKSPACE_FLAGS = ("--workspace", "-w")


def pop_workspace_flag(argv: list[str]) -> tuple[str, list[str]]:
    """摘出 `--workspace/-w` 的值,其余参数原样返回(给一次性任务用)。

    两种写法都认:`--workspace D:\\资料` 与 `--workspace=D:\\资料`。路径可能含空格,
    所以只吃掉紧跟其后的一个 token(带空格的路径请加引号)。
    """
    value, rest, i = "", [], 0
    while i < len(argv):
        item = argv[i]
        if item in WORKSPACE_FLAGS and i + 1 < len(argv):
            value = argv[i + 1]
            i += 2
            continue
        if any(item.startswith(flag + "=") for flag in WORKSPACE_FLAGS):
            value = item.split("=", 1)[1]
            i += 1
            continue
        rest.append(item)
        i += 1
    return value, rest


def startup_workspace(explicit: str) -> Path:
    """启动工作区:命令行显式指定 > 记忆里的当前 > 当前目录。

    显式指定的路径不合法时**直接报错退出**,不要默默跑到别的地方去干活。
    """
    if (explicit or "").strip():
        target, err = ws.resolve_target(explicit)
        if target is None:
            print(f"错误: --workspace 不可用 —— {err}", file=sys.stderr)
            raise SystemExit(2)
        return target
    return ws.startup_workspace(Path.cwd())


def cmd_workspace(cmd: str, config, workspace: Path, ui: TerminalUI, agent,
                  rebuild: bool = True):
    """/workspace [路径|default]:查看或切换工作区。

    切换要重建 Agent:system prompt 里的工作区路径、工具围栏、附件/计划/记忆
    的落点全部绑定工作区;旧 Agent 的 MCP 子进程先关掉,不留孤儿。
    返回新的 `(agent, session, workspace)`;只是查看/失败时返回 None。
    """
    arg = " ".join(cmd.split()[1:]).strip().strip('"').strip("'")
    if not arg:
        recent = ws.recent_entries(ws.load_state())
        lines = [f"当前工作区: {workspace}",
                 f"  默认工作区: {ws.default_workspace()}"]
        if recent:
            lines.append("  最近用过:")
            lines += [f"    • {e['name']} —— {e['path']}" for e in recent]
        lines.append("  用法: /workspace <文件夹路径> 切换;"
                     "/workspace default 回到默认工作区")
        ui.info("\n".join(lines))
        return None

    if arg.lower() in ("default", "默认"):
        target = ws.default_workspace()
    else:
        target, err = ws.resolve_target(arg)
        if target is None:
            ui.error(err)
            return None
    if ws.key(target) == ws.key(workspace):
        ui.info(f"已经在用这个工作区: {target}")
        return None

    ws.remember(target)
    if not rebuild:      # 一次性模式:进程马上结束,只记下"下次用这个",不重建
        ui.info(f"已记住工作区: {target}(下次启动生效)")
        return None
    agent.close()        # 旧工作区的 MCP 子进程先收掉
    new_agent, new_session = create_agent(config, target, ui)
    ui.workspace = target      # 横幅/权限确认框里的工作区跟着换
    _warn_library_health(target, ui)
    ui.info(f"工作区已切换: {target}\n"
            "(记忆、计划、附件都落在这个文件夹里;对话从新会话开始)")
    return new_agent, new_session, target


def _fix_console_encoding() -> None:
    """Windows 控制台默认代码页是 GBK,rich 的 legacy 渲染器按当前代码页编码,
    碰到非 GBK 字符(✓/🚫 等)会抛 UnicodeEncodeError 把整轮对话打断 —— 打包版
    CLI 实测如此。把输出代码页切到 UTF-8,并给标准流加 replace 兜底。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def main() -> None:
    import platform
    import time

    _fix_console_encoding()

    # --web:切到网页版(校园助手形态),其余参数原样转交
    if "--web" in sys.argv:
        from web_main import main as web_main_entry
        web_main_entry([a for a in sys.argv[1:] if a != "--web"])
        return

    config = load_config()
    # 模型能力探测(窗口/是否支持视觉):有缓存立即生效,没有就后台问一次
    model_probe.ensure_async(config.base_url, config.api_key, config.model)
    explicit_ws, rest_argv = pop_workspace_flag(sys.argv[1:])
    workspace = startup_workspace(explicit_ws)
    ui = TerminalUI(config, workspace)
    ui.banner(key_ok=config.api_key_configured)
    _warn_library_health(workspace, ui)

    agent, session = create_agent(config, workspace, ui)

    try:
        _repl(config, workspace, ui, agent, session, argv=rest_argv)
    finally:
        agent.close()      # 关掉 MCP 子进程,不留孤儿


def _warn_library_health(workspace: Path, ui: TerminalUI) -> None:
    """启动自检馆藏库:库没装全/读不出来时当场说清,别等搜书搜不到才猜。

    库是好的时候什么都不打印(不吵);自检本身出错也绝不能拦住启动。
    """
    try:
        from minicode.library import catalog_health, health_notice
        notice = health_notice(catalog_health(workspace))
    except Exception:
        return
    if notice:
        ui.error(notice)


def _repl(config, workspace, ui, agent, session, argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 一次性模式:命令行参数或管道输入(斜杠命令同样支持)
    task = unmanagle_slash_command(argv) if argv else None
    if task is None and not sys.stdin.isatty():
        task = sys.stdin.read().strip() or None
    if task is not None:
        if task.startswith("/workspace"):
            cmd_workspace(task, config, workspace, ui, agent, rebuild=False)
        elif task.startswith("/"):
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
        if text.startswith("/workspace"):
            switched = cmd_workspace(text, config, workspace, ui, agent)
            if switched is not None:
                agent, session, workspace = switched
            continue
        if text.startswith("/"):
            if handle_slash(text, session, config, ui, agent):
                ui.info("再见!")
                break
            continue
        agent.run_turn(text)


if __name__ == "__main__":
    main()
