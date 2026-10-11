"""引路学长 CLI 入口:加载配置 → 构建 Agent → REPL 循环。

用法:
  python main.py               交互式 REPL
  python main.py "任务描述"     一次性执行后退出
  echo "任务" | python main.py 从管道读取一条任务执行后退出
  python main.py --json "..."  无头:stdout 只有一份 JSON,退出码说清结局
  python main.py --output-format stream-json "..."  无头:stdout 是 NDJSON 事件流(最后一行 result)
  python main.py --output-schema schema.json "..."  同上,另按 schema 要一份结构化结果(不合 schema → 退出码 6)
  python main.py --mcp-serve [--workspace 路径]  作 MCP 服务端被外部客户端按 stdio 调用(只读校园能力)
  python main.py --version     打印名字与版本就退出(不发请求、不读配置)
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from minicode.app_setup import apply_retention, create_agent
from minicode.config import load_config
from minicode import APP_NAME, __version__
from minicode import headless, mcp_server
from minicode import model_probe
from minicode.paths import SESSIONS_DIR
from minicode import workspace as ws
from minicode.session import (HISTORY_SCAN_FILES, Session, history_hits,
                              list_sessions)
from minicode.ui import SLASH_COMMANDS, TerminalUI

if TYPE_CHECKING:      # 只给注解用:运行期不 import
    from minicode.agent import Agent


def expand_with_args(cmd: str, workspace, ui) -> "str | None":
    """`/命令 [内容]` → 展开后的提示词(F2,CLI 侧)。

    无参命令返回的就是完整提示词(直接发);带 $ARGS 的用命令行里剩余的
    内容替换($text 的内置命令在 CLI 没有"光标停空处"那回事,同理处理);
    不认识的命令返回 None(走管理命令那条路)。
    """
    from minicode import slash_commands as sc
    head = cmd.split(None, 1)[0]          # 只拿命令词(后面可能是要带进去的内容)
    prompt = sc.expand(head, workspace)
    if prompt is None:
        return None
    parts = cmd.split(None, 1)
    rest = parts[1] if len(parts) > 1 else ""
    # 两种占位都认:内置命令用 $text,工作区自定义用 $ARGS
    if sc.ARG_PLACEHOLDER in prompt or sc.ARGS_PLACEHOLDER in prompt:
        if not rest.strip():
            ui.info("这条命令要带内容,如:" + head + " <要处理的文字>")
            return None
        return prompt.replace(sc.ARGS_PLACEHOLDER, rest).replace(
            sc.ARG_PLACEHOLDER, rest)
    return prompt


UNDO_USAGE = ("只看:/undo 列清单 · /undo diff <编号> 看某一处的「改之前 vs 磁盘上现在」\n"
              "动手:/undo <编号> 退那一处 · /undo last 退最近一步 · /undo all 一路退回本轮开始前"
              "(三种都要点头)")


def _undo_row(rec: dict) -> str:
    """清单里的一行 —— 三个旗各说一件真事,不写就等于让人拿对不上的账做决定。"""
    flags = []
    if rec["drift"]:
        flags.append("我改过之后它还被别的工具动过")
    if rec["newer"]:
        flags.append(f"这一处上面还有 {rec['newer']} 处没退")
    if rec["stale"]:
        flags.append("压过历史之前那一轮记的(逐处退照旧,按第几问退不行)")
    return (f"  [{rec['seq']}] {rec['kind']} {rec['path']}"
            f"({rec['at']},退下去={rec['verb']})"
            + (" ⚠ " + ";".join(flags) if flags else ""))


def _undo_list_text(agent) -> str:
    rep = agent.undo_records()
    if not rep["items"]:
        return rep["hint"]
    head = f"本轮改动 {rep['total']} 处(最新在前"
    if rep.get("turns", 1) > 1:
        # 这本栈是按会话攒的,不是按问:以前这句「本轮改动」在长会话里是句假话
        head += f",跨 {rep['turns']} 问"
    if rep.get("turn"):
        head += f",现在第 {rep['turn']} 问"
    if rep["more"]:
        head += f",这里只列 {len(rep['items'])} 处,另有 {rep['more']} 处"
    head += "):"
    return "\n".join([head] + [_undo_row(it) for it in rep["items"]]
                     + [rep["note"], UNDO_USAGE])


def _undo_diff_text(agent, seq, full: bool = False) -> tuple:
    """(能不能算, 给人看的那段)。不能算的原因原样传出去,不换成一句「没有变化」。"""
    d = agent.undo_diff(seq)
    if not d["ok"]:
        return False, d["msg"]
    if d["same"]:
        return True, (f"{d['path']}:两侧一样 —— 这一处现在和改之前没差别"
                      "(改回去了,或被别的工具又改成了原样)。")
    text = (f"{d['path']}({d['kind']}):+{d['added']} 行 / −{d['removed']} 行\n"
            + d["note"])
    if not full:
        return True, text + f"\n要看整段:/undo diff {seq}"
    lines = [text] + list(d["lines"])
    if d["more"]:
        lines.append(f"(另有 {d['more']} 行没显示 —— 这一处比一屏长,自己打开文件看。)")
    return True, "\n".join(lines)


def _undo_command(agent, ui, rest: list) -> None:
    """/undo 的一族(设计稿 §四.3:回退是动手面,要点头)。

    无参数**只列不动手**:老行为是直接退最近一步且一声不问,而退下去会真改磁盘 ——
    GUI 那侧早就是「先看这一处改了什么,再点退回去」,命令行没有理由更松。
    """
    from rich.markup import escape      # 磁盘上拼出来的文字,方括号不许当标签吃掉

    head = (rest[0].lower() if rest else "")
    if not head or head in ("list", "ls"):
        ui.info(escape(_undo_list_text(agent)))
        return
    if head == "diff":
        if len(rest) < 2:
            ui.info("看哪一处?编号见 /undo   (例:/undo diff 3)")
            return
        _ok, text = _undo_diff_text(agent, rest[1], full=True)
        ui.info(escape(text))
        return
    if head == "all":
        rep = agent.undo_records()
        if not rep["items"]:
            ui.info(rep["hint"])
            return
        detail = (f"要把这 {rep['total']} 处一路退回本轮开始前的样子"
                  "(最新→最旧,同一处退到最早那份前像):\n"
                  + "\n".join(_undo_row(it) for it in rep["items"])
                  + "\n" + rep["note"])
        if not ui.confirm_destructive(detail, "全部回退?"):
            ui.info("已取消:磁盘一个字没动。")
            return
        ui.info(escape(agent.undo_all()))
        return
    last = head in ("last", "-1")
    if last:
        rep = agent.undo_records()
        if not rep["items"]:
            ui.info(rep["hint"])
            return
        seq = rep["items"][0]["seq"]
    elif head.isdigit():
        seq = head
    else:
        ui.info("没这条命令。" + UNDO_USAGE)
        return
    # 点头之前先摆出这一处的规模与漂移:让人是看着事实说"退",不是手滑到底
    ok, preview = _undo_diff_text(agent, seq)
    if not ok:
        ui.info(escape(preview + "\n(什么都没退 —— 上面是为什么算不出。)"))
        return
    row = next((it for it in agent.undo_records()["items"]
                if str(it["seq"]) == str(seq)), None)
    if not ui.confirm_destructive((_undo_row(row) + "\n" if row else "") + preview,
                                  "退回这一步?"):
        ui.info("已取消:磁盘一个字没动。")
        return
    ui.info(escape(agent.undo() if last else agent.undo(seq)))


def _instructions_text(workspace) -> str:
    """L3 的终端形态:报「读到没读到、几行、来自哪份、有没有被截、采没采纳」。

    报不了「我之所以这么做是因为你第 N 条」—— 那是把模型的自述当因果证据。
    """
    from minicode.app_setup import (AGENTS_CHAR_LIMIT, INSTRUCTION_GUARD,
                                    USER_AGENTS_NAME, app_path,
                                    instruction_layers)
    layers = instruction_layers(workspace)
    lines = ["这一轮提示词里的指令层:内置那一份 + 下面这些"]
    if not layers:
        lines.append("  用户级、项目级都没读到 —— 现在只有内置那一份。")
        lines.append(f"  想给自己定规矩:在 {app_path(USER_AGENTS_NAME)} 写一份(跨项目生效),"
                     f"或在工作区根放 {USER_AGENTS_NAME}(只对这个项目;也能用 /init 起草)")
    for it in layers:
        if it["scope"] == "未读":
            # 看到了但没读的(j,v0.9.153):**这一行不能省** —— 它正是
            # "我明明在 .cursor/rules/ 里写了这一条,它怎么不听"的答案。
            lines.append(f"  • 没读 {it['name']} —— {it['why']}")
            lines.append("    (本项目不做规则引擎:带条件的一条不假装生效;"
                         "想让它对所有文件生效,就把它并进上面那一份)")
            continue
        if it["scope"] == "用户级":
            where = app_path(USER_AGENTS_NAME)
        elif it["scope"] == "目录级":
            where = f"{workspace}\\({it['rel']})"
        else:
            # 项目级现在可能是**一组**来源(`AGENTS.md` + `.cursorrules` +
            # `.cursor/rules/*.mdc`),所以路径报的是工作区根,而不是某一份文件。
            where = f"{workspace}\\({'、'.join(it.get('sources') or [it['name']])})"
        cut = (f"(超过 {AGENTS_CHAR_LIMIT} 字,只注入了前面那一截,后面没进提示词)"
               if it["truncated"] else "")
        # 项目级那一行要说得出"在场但没采纳"(v0.9.127 §四.2):这一屏的全部意义
        # 就是让用户不必猜"它到底读了没有"。
        pending = (it["scope"] in ("项目级", "目录级") and not it.get("adopted", True))
        lines.append(f"  • {it['scope']} {it['name']}:"
                     f"{it['lines']} 行 / {it['chars']} 字{cut}"
                     + ("  ← 还没点过头,**没进提示词**" if pending else ""))
        lines.append(f"    {where}")
        if pending:
            lines.append("    下一问会弹一张卡请你采纳;这个界面点不了头时,把规矩写进"
                         "用户级那一份(那一份是你自己写的,不问)")
    lines.append("  " + INSTRUCTION_GUARD)
    return "\n".join(lines)


REWIND_SCOPES = ("files", "talk", "both")
REWIND_USAGE = ('用法: /rewind <第几问> [files|talk|both] —— 退到那一问**之前**'
                "(默认 both:文件与对话一起退;留档不删,想找回原会话用 /resume)")


def _snapshots_command(agent, ui) -> None:
    """`/snapshots`:影子库里有哪几次整树快照(H,v0.9.157)。"""
    from minicode import shadow_git
    ui.info(agent.shadow_line())
    rows = shadow_git.snapshots(agent.ctx.workspace)
    if not rows:
        ui.info("库里还是空的 —— 它是**要动文件之前**才快照的,所以只读的会话里它就该是空的。")
        return
    lines = ["最近的整树快照(新到旧,`/restore N` 里的 N 就是下面这个序号):"]
    for n, r in enumerate(rows, start=1):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"]))
        lines.append(f"  {n}. {when}  {r['subject']}")
    lines.append("**回滚只还原、绝不删**:快照之后新建的文件会原样留着。")
    ui.info("\n".join(lines))


def _restore_command(agent, ui, rest: list) -> None:
    """`/restore <序号|sha>`:把工作区还原到那次快照(H,v0.9.157)。"""
    from minicode import shadow_git
    if not rest:
        ui.error("用法: /restore <序号> —— 序号看 /snapshots。")
        return
    rows = shadow_git.snapshots(agent.ctx.workspace)
    want = str(rest[0]).strip()
    if want.isdigit():
        idx = int(want)
        if not (1 <= idx <= len(rows)):
            ui.error(f"序号要在 1..{len(rows)} 之间(先 /snapshots 看一眼)。")
            return
        sha = rows[idx - 1]["sha"]
    else:
        hit = [r for r in rows if r["sha"].startswith(want)]
        if len(hit) != 1:
            ui.error(f"'{want}' 对不上唯一一次快照(先 /snapshots,或用序号)。")
            return
        sha = hit[0]["sha"]
    n, why = shadow_git.restore_only(agent.ctx.workspace, sha)
    if why:
        ui.error(f"没还原成:{why}")
        return
    ui.info(f"已把 {n} 份文件还原到那次快照。**没有删除任何东西** —— "
            "快照之后新建的文件还在原处(这是有意的:它只把你改过的东西退回去)。")


def _rewind_command(agent, session, ui, rest: list) -> None:
    """`/rewind`:检查点。R3 那套坐标与纪元闸早就在了,可它只有「编辑重发」一条路能碰到。

    同学说的是"回到我问那句之前",不是"改那句重问" —— 同一套机制,两种意图,
    只接了一条就等于那条机制只有一半人用得上。

    命令行没有界面那份 `expect`(原文比对),所以这里**把找到的那一问原样摆出来**给人核对:
    数错号一眼就能看出来,而"压过历史之后号对不上"那种看不出来的,由 `rollback_to_ordinal`
    自己拒绝。顺序与 R3 一致:**先退文件再截对话** —— 截断会改掉回合坐标。
    """
    from rich.markup import escape      # 那一问的原文里什么都有,方括号不许被当标签吃掉

    head = (rest[0].strip() if rest else "")
    scope = (rest[1].strip().lower() if len(rest) > 1 else "both")
    if scope not in REWIND_SCOPES:
        ui.info(f"没这个范围「{scope}」。可用:files(只退文件)/ talk(只截对话)/ both。\n"
                + REWIND_USAGE)
        return
    from minicode.session import user_message_at
    idx, msg = user_message_at(session.messages, head)
    if msg is None:
        ui.info(f"第 {head} 问不在当前上下文里(一共没这么多问,或已被压缩折进摘要)。"
                "文件与对话都没动。")
        return
    n = int(head)
    shown = " ".join(str(msg.get("content") or "").split())[:80]
    drop = len(session.messages) - idx
    prev = agent.rollback_preview(n)
    lines = [f"第 {n} 问你说的是:「{shown}」"]
    if scope in ("both", "files"):
        if not prev["ok"]:
            ui.info(prev["msg"])
            return
        if prev["stale"]:
            ui.info(f"这中间压过一次历史:第 {n} 问的位置已经对不上了,有 {prev['stale']} 处改动"
                    "我不敢按号退 —— **文件与对话都没动**。要退就用「任务监控 → 本轮改动」"
                    "逐处退(那里退具体某一处,不需要编号)。")
            return
        lines.append((f"  文件:退 {prev['count']} 处 ——"
                      + "、".join(f"{k} {p}" for k, p in zip(prev["kinds"], prev["paths"]))
                      + (f" 等 {prev['count']} 处" if prev["more"] else "") + " ")
                     if prev["count"] else f"  文件:{prev['msg']}")
    if scope in ("both", "talk"):
        lines.append(f"  对话:那一条与其后共 {drop} 条离开上下文"
                     "(留档 sessions/ 不删,想看回来过的内容用 /resume 或界面上翻)")
    lines.append("  注:命令与外部程序改的文件从来不在账里,这一档退不到它们。")
    if not ui.confirm_destructive("\n".join(lines), f"退到第 {n} 问之前?"):
        ui.info("已取消:文件与对话都没动。")
        return
    if scope in ("both", "files"):
        ui.info(escape(agent.rollback_to_ordinal(n).get("msg") or ""))
    if scope in ("both", "talk"):
        session.replace_messages(session.messages[:idx])
        ui.info(f"对话已退回第 {n} 问之前(去掉 {drop} 条;留档没删)。")


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
        p = config.provider
        lines = ["当前模型(切换在界面顶栏「模型管理」;这里只做展示)"]
        if p:
            key = "已配 Key" if p.has_key else "未配 Key"
            lines.append(f"  ▶ {p.name}({p.id}) · {p.model} · {key} · {p.base_url}")
            for entry in p.model_entries():
                mark = "✓" if entry.get("enabled", True) else "✗"
                lines.append(f"      {mark} {entry.get('name', '')}")
        for q in config.providers:
            if p is not None and q.id == p.id:
                continue
            state = "" if q.enabled else " · 已停用"
            lines.append(f"  ● {q.name}({q.id}) · {q.model} · "
                         f"{len(q.enabled_models())} 个启用模型{state}")
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
        from minicode.planning import PLAN_REL, plan_state
        sub = (arg or "").strip().lower()
        sb = getattr(agent.ctx, "sandbox", None)
        # 规划锁只有人能解(v0.9.129)—— 所以命令行这一条不是"给模型用的开关",
        # 而是**用户自己**批准/放弃这张单子的出口(桌面版对应核对单与卡片那颗按钮)。
        if sub in ("approve", "批准", "unlock"):
            ok = bool(sb is not None and sb.unlock_plan())
            ui.info("已批准这张计划,规划锁解除 —— 可以开工了。" if ok else
                    "现在没有锁(没锁就不用批:写文件与命令照常走档位)。")
        elif sub in ("lock",):
            st = plan_state(workspace)
            # 没有计划就不许锁:锁一张不存在的单子,等于把用户关在一个
            # 没有出口的地方(他连可批的东西都没看到)。
            ok = bool(st.get("has_plan") and sb is not None
                      and sb.lock_plan(st.get("goal") or ""))
            ui.info("已上规划锁:在你 /plan approve 之前,写文件与跑命令都会被拒。"
                    if ok else "没有可锁的计划(先让学长 plan 一版)。")
        elif sub in ("status", "状态"):
            locked = bool(sb is not None and getattr(sb, "plan_lock", False))
            ui.info(("🔒 规划锁开着" if locked else "规划锁没开")
                    + f";计划:{'有' if (workspace / PLAN_REL).exists() else '没有'}。")
        else:
            p = workspace / PLAN_REL
            if p.exists():
                locked = bool(sb is not None and getattr(sb, "plan_lock", False))
                ui.info(("🔒 这张计划还没被批准(写文件与跑命令会被拒)。"
                         "要开工输入 /plan approve。" if locked else "")
                        + p.read_text(encoding="utf-8"))
            else:
                ui.info("当前没有计划文件(工作区 plans/plan.md)。")
    elif name == "/skills":
        from minicode.skills import load_skills, skill_dirs, dependency_report
        skills = load_skills(skill_dirs(workspace))
        if not skills:
            ui.info("还没有安装技能。把技能文件夹(含 SKILL.md)放进程序目录的 skills/ 即可。")
        else:
            try:
                from minicode.tools.registry import build_default_registry
                tool_names = {t.name for t in build_default_registry()}
            except Exception:
                tool_names = None          # 装配不起来就只标库,不猜工具
            rows = []
            for s in skills:
                r = dependency_report(s, available_tools=tool_names)
                miss = []
                if r["missing_tools"]:
                    miss.append("缺工具:" + "、".join(r["missing_tools"]))
                if r["missing_libs"]:
                    miss.append("缺库:" + "、".join(r["missing_libs"]))
                rows.append(f"  • {s.name} —— {s.description or '(无描述)'}"
                            + (f"(⚠ {';'.join(miss)})" if miss else ""))
            ui.info("可用技能\n" + "\n".join(rows))
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
    elif name == "/style":
        from minicode.config import save_config
        want = arg.strip().lower()
        if want not in ("normal", "guide"):
            cur = getattr(config, "reply_style", "normal")
            ui.info(f"回复风格当前:{cur}。可切:normal(直接帮做)/guide(讲解模式,"
                    "拆步骤引导你自己完成)。用法:/style guide 或 /style normal")
        else:
            from minicode.config import CONFIG_PATH
            config.reply_style = want
            save_config(CONFIG_PATH, config)
            ui.info(f"已切到 {want} 风格。"
                    + ("下一轮起,学长会拆步骤引导你自己动手,不整段代做。"
                       if want == "guide" else "下一轮起恢复直接帮做。"))
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
            # C1(v0.9.108):今天的账 + 日预算状态
            today = usage_mod.today_total()
            lines.append(f"  今天: {today:,} tokens")
            budget = usage_mod.budget_state(
                int(getattr(config, "daily_token_budget", 0) or 0))
            if budget.get("enabled"):
                if budget["level"] == "over":
                    lines.append(f"  ⚠ 日预算 {budget['limit']:,} 已超支"
                                 f"(超出 {today - budget['limit']:,})")
                else:
                    lines.append(f"  日预算 {budget['limit']:,}:已用 "
                                 f"{int(budget['ratio'] * 100)}%,还剩 {budget['remaining']:,}")
            ui.info("\n".join(lines))
    elif name == "/library":
        from minicode.library import catalog_health, health_notice, library_search
        # 带书名 = 搜书。走的是工具**同一个 handler 与同一个 ctx**(同一份检索、
        # 同一句"没查到"、同一个 CatalogUnavailable 口径),不在命令行里再抄一份
        # 检索逻辑 —— 抄第二份就有两处真相,而这两处都会被人当成"学长说的"。
        query = " ".join(parts[1:]).strip()
        if query:
            ui.info(library_search(agent.ctx, query))
            return False
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
        # 与压缩触发器同一份算法(消息 + 工具定义),阈值也走同一个
        # `context_window_for`:以前这里直接拿 config.context_window 算阈值,
        # 而触发器用的是运行时探测到的窗口 —— 两个数不一样时,用户按 /history
        # 说的"还早"去判断,却见它突然压了一次。
        # 四栏明细与图形界面那个「上下文窗口」面板共用 `compaction.context_breakdown`:
        # 命令行不该有一份自己的、算法不同的占用读法(v0.9.106)。
        from minicode.compaction import context_breakdown
        from minicode.model_catalog import context_reading, context_window_for
        total, source = context_reading(agent, session)
        tools_only = int(getattr(agent, "tools_tokens", 0) or 0)
        window = context_window_for(config.model, config.context_window)
        threshold = int(window * config.compact_at_ratio)
        src = ("这把尺子取的是上次请求**服务端真返回的 prompt_tokens**(当下限)。"
               if source == "real"
               else "这把尺子是本地估算(端点没返回 usage 时只能估)。")
        probed = "" if window == config.context_window else \
            f";窗口按运行时探测算 {window}(config 里写的是 {config.context_window})"
        lines = [f"当前上下文 {len(session.messages)} 条消息,约 {total} tokens"
                 f"(其中工具定义 {tools_only} tokens;压缩阈值 {threshold} tokens{probed})。"
                 f"{src}"]
        breakdown = context_breakdown(agent, session, config, window, total, source)
        if breakdown:
            base = max(1, sum(int(x["tokens"]) for x in breakdown["buckets"]))
            lines.append(f"占用拆四栏(本地估算之和 {breakdown['estimated']} tokens,量级参考):")
            for row in breakdown["buckets"]:
                extra = f"({row['count']}个)" if row.get("count") else ""
                lines.append(f"  {row['label']}{extra}:"
                             f" {row['tokens']} tokens"
                             f" ({round(row['tokens'] / base * 100)}%)")
            if source == "real":
                lines.append("  实测那个数比四栏之和大,是大模型的真实分词与本地粗估的差,"
                             "不是漏算了什么。")
        lines.append("会话留档在 sessions/ 目录。")
        ui.info("\n".join(lines))
    elif name == "/rename":
        # v0.9.126:改名只动留档顶上那行元数据,不碰任何一条消息 —— 会话是同学的
        # 记录,程序没权利因为"你换了个叫法"就把旧那份挪走或删掉。
        from minicode.session import current_title, set_title
        path = agent.session.log_path
        if not arg:
            head = ("当前标题:" + current_title(path) + "\n") if path \
                else "当前会话还没有留档。\n"
            ui.info(head + "用法: /rename <新标题>")
        else:
            ui.info(set_title(path, arg) if path else
                    "当前会话还没有留档 —— 先说一句(第一句话才会落盘成留档)。")
    elif name in ("/archive", "/unarchive"):
        from minicode.session import set_archived
        path = agent.session.log_path
        if path is None:
            ui.info("当前会话还没有留档(先说一句话)。")
        else:
            ui.info(set_archived(path, name == "/archive"))
    elif name == "/fork":
        # v0.9.126:分叉**列表里那条会话**开新任务。与 /resume 的区别:/resume 是
        # 回到原文件接着写,/fork 是复制一份新的、原留档一个字不动。行级复制保留
        # 原消息的 ts —— 分叉的意义是"从那一刻换个思路再走一遍",改写时间线=伪造历史。
        from minicode.session import fork_log_file
        summaries = list_sessions(SESSIONS_DIR)
        if not summaries:
            ui.info("还没有历史会话。")
        elif not arg.isdigit():
            ui.list_sessions(summaries)
            ui.info("用法: /fork <序号>(原会话原样保留,新的一份接管当前上下文)")
        elif not 1 <= int(arg) <= len(summaries):
            ui.error(f"序号超出范围(1-{len(summaries)})。")
        else:
            chosen = summaries[int(arg) - 1]
            got = fork_log_file(chosen.path, max(1, chosen.n_messages),
                                SESSIONS_DIR, workspace)
            if got is None:
                ui.error("分叉没成:那条留档读不到,或里面还没有消息。")
            else:
                new_path, kept = got
                n = session.load_from(new_path)
                ui.info(f"已从 {chosen.path.name} 分叉出新会话:复制 {kept} 条消息,"
                        f"当前上下文载入 {n} 条。原会话原样留在列表里。")
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
    elif name == "/find":
        # 与模型侧 search_history 同一份取数(history_hits):口径改一处,两边一起变
        if not arg.strip():
            ui.info("用法: /find <关键词>   在你自己的历史会话留档里搜,如 /find 报错")
        else:
            found = history_hits(arg, top=5)
            if not found:
                ui.info(f"最近 {HISTORY_SCAN_FILES} 个会话留档里没提到「{arg.strip()}」。")
            else:
                blocks = [f"[{i}] {h['when']}"
                          + (f" · {h['workspace']}" if h["workspace"] else "")
                          + f"(命中 {h['hits']} 次)\n    {h['snippet']}"
                          for i, h in enumerate(found, start=1)]
                ui.info("\n".join(blocks))
                ui.info("接着聊那次:GUI 在侧栏搜索框点开,CLI 用 /resume <序号>。")
    elif name == "/undo":
        _undo_command(agent, ui, parts[1:])
    elif name == "/instructions":
        ui.info(_instructions_text(workspace))
    elif name == "/snapshots":
        _snapshots_command(agent, ui)
    elif name == "/restore":
        _restore_command(agent, ui, parts[1:])
    elif name == "/rewind":
        _rewind_command(agent, session, ui, parts[1:])
    elif name == "/compact":
        # 压没压、压掉了多少,必须报出来:原来这条按完就像静默成功,
        # 而"早期对话还不够多"和"摘编那次调用失败了"都是什么都没发生。
        before = int(agent.context_tokens())
        done = bool(agent.compact_now())
        after = int(agent.context_tokens())
        if done:
            ui.info(f"已压缩:占用约 {before} → {after} tokens"
                    f"(省出 {max(0, before - after)} tokens)。")
        else:
            ui.info(f"没压掉东西:早期对话还不够多,或这次摘编没做成;"
                    f"占用仍是约 {after} tokens。")
    elif name == "/permission":
        from minicode.permission import MODE_LABELS, PERMISSION_MODES
        if arg:
            mode = arg.strip().lower()
            if mode in PERMISSION_MODES:
                config.permission_mode = mode
                ui.info(f"权限模式已切换为「{MODE_LABELS[mode]}」(仅本次运行;持久化请改 config.json)")
            else:
                ui.error(f"未知档位 {arg}。可用: " + "/".join(PERMISSION_MODES))
        else:
            mode = config.permission_mode
            lines = ["当前权限档位: " + f"「{MODE_LABELS[mode]}」({mode})"]
            for m in PERMISSION_MODES:
                mark = "●" if m == mode else "○"
                lines.append(f"  {mark} {m:<13} {MODE_LABELS[m]}")
            # 项目白名单的现状直接从 gate 读(v0.9.87):PermissionGate 构造时就装载并核对过
            # 指纹,这里再 load 一次等于第二份真相,还会绕过 tool_names 那套校验口径。
            proj = agent.gate.project
            if proj is not None:
                if proj.effective:
                    lines.append(f"  项目白名单:{len(proj.rules)} 条**已生效**"
                                 "(改 permissions.json 一个字就整份失效)")
                elif proj.rules:
                    lines.append(f"  项目白名单:{len(proj.rules)} 条**未生效**"
                                 f"({proj.reason})—— 要批准请在对话里说「批准这个项目的权限」;"
                                 "命令行不留批准入口,免得绕过确认框")
                else:
                    lines.append("  项目白名单:无(想少问几次,可在项目根放 permissions.json)")
                for p in proj.problems[:3]:
                    lines.append(f"    装载时拒收:{p}")
            lines.append("切换: /permission <档位>(GUI 在顶栏下拉)")
            ui.info("\n".join(lines))
    elif name == "/think":
        from minicode.reasoning import LABELS, get_capability, normalize_choice
        model = config.model
        cap = get_capability(model)
        llm = getattr(agent, "llm", None)
        current = getattr(llm, "reasoning_choice", "default") if llm else "default"
        if cap.kind == "hidden":
            ui.info(f"模型 {model} 未登记思考能力,暂不支持思考档位设置。")
        elif cap.kind == "fixed":
            ui.info(f"模型 {model} 固定开启思考,不可调节。")
        elif arg:
            choice = normalize_choice(model, arg.strip().lower())
            llm.reasoning_choice = choice
            ui.info(f"思考档位已切换为「{LABELS[choice]}」({model},下一个请求生效)")
        else:
            lines = [f"当前思考档位: 「{LABELS[current]}」 · 模型 {model}"]
            for o in cap.options:
                mark = "●" if o == current else "○"
                lines.append(f"  {mark} {o:<8} {LABELS[o]}")
            lines.append("切换: /think <档位>(GUI 在输入框下方按钮)")
            ui.info("\n".join(lines))
    else:
            ui.error(f"未知命令 {name}。可用: " + " ".join(SLASH_COMMANDS))
    return False


# 命令表只有一份(`minicode/ui.py::SLASH_COMMANDS`):这一行是它的别名,不是第二张表。
# 以前这里手抄一遍,于是补全、未知命令提示、MSYS 还原三处各长各的 ——
# /workspace 能跑却不在提示里,/find /permission /think 能跑却补不出来。
SLASH_NAMES = frozenset(SLASH_COMMANDS)
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

    _fix_console_encoding()

    # --version:零成本的本地旗标 —— 打印名字与版本就退,**不发请求、不读配置**。
    # 为什么要有它:从前"问版本"会被当成一次性任务发给模型(花一次额度),而网关
    # 不通时回来的是一串 401 —— 版本号是最不该依赖网络的东西。放在 `load_config()`
    # **之前**就是这条承诺的实现方式:配置坏掉、Key 没配、网关不可达都照样答得出。
    # 刻意不加 `-v`:那个字母在别处通常指 verbose,这里只认长写法。
    if "--version" in sys.argv:
        print(f"{APP_NAME} {__version__}")
        raise SystemExit(0)

    # --web:切到网页版(校园助手形态),其余参数原样转交
    if "--web" in sys.argv:
        from web_main import main as web_main_entry
        web_main_entry([a for a in sys.argv[1:] if a != "--web"])
        return

    # --py <脚本>:用内置解释器执行一个 Python 脚本后退出。
    # py_script 工具在打包版里靠它拿到带 pptx/docx/openpyxl/pypdf 的运行时
    # (学生机器上没有裸 Python,这是转换类技能的执行底座)。
    if "--py" in sys.argv:
        rest = sys.argv[sys.argv.index("--py") + 1:]
        if not rest:
            print("错误: --py 需要一个脚本路径", file=sys.stderr)
            raise SystemExit(2)
        script = Path(rest[0])
        try:
            code = script.read_text(encoding="utf-8")
        except OSError as e:
            print(f"错误: 读不到脚本 {script}: {e}", file=sys.stderr)
            raise SystemExit(2)
        globs = {"__name__": "__main__", "__file__": str(script.resolve())}
        sys.argv = rest
        try:
            exec(compile(code, str(script), "exec"), globs)  # noqa: S102
        except SystemExit:
            raise
        except BaseException:
            import traceback
            traceback.print_exc()
            raise SystemExit(1)
        raise SystemExit(0)

    config = load_config()
    # 输出格式先摘出来:留在 argv 里会被当成一次性任务的一部分
    # (`unmanagle_slash_command` 是把剩下的参数整个 join 起来当提问的)。
    try:
        out_fmt, argv_for_ws = headless.pop_output_flags(sys.argv[1:])
        schema_raw, argv_for_ws = headless.pop_output_schema(argv_for_ws)
    except headless.UsageError as e:
        print(f"错误: {e}", file=sys.stderr)
        raise SystemExit(headless.EXIT_ARGS)
    # `--output-schema` 给了、且没显式要事件流时,按 json 出:那份结构化结果本来
    # 就是给机器读的,让人去看一行缩进 JSON 不是它的读者。**只有 text 才强制改** ——
    # schema 只做两件事(照它问模型、验回来),与"过程怎么上线"正交,所以
    # `stream-json` + schema 仍然出事件流,`structured_output` 只出现在 `result` 行里。
    if schema_raw and out_fmt == "text":
        out_fmt = "json"
    schema = None
    if schema_raw:
        from minicode import schema as schema_mod
        try:
            schema = schema_mod.load(schema_raw)
        except schema_mod.SchemaError as e:
            # 在读到 schema 这一步就退,不进 Agent 一步:花掉一轮额度之后才说
            # "这份 schema 我不认",是把用户的钱和时间拿去换一句报错。
            print(f"错误: --output-schema 不能用 —— {e}", file=sys.stderr)
            raise SystemExit(headless.EXIT_ARGS)
    # `--mcp-serve`:本 agent 反过来作 MCP 服务端,被外部客户端(Claude Code /
    # Cursor / Qoder 一类)按 stdio 调用。分支必须在 `TerminalUI` **之前** return
    # —— 它会往 stdout 打横幅,而服务端的 stdout 只能是协议报文。
    serve, argv_for_ws = mcp_server.pop_mcp_serve_flag(argv_for_ws)
    if serve and out_fmt != "text":
        print("错误: --mcp-serve 与 --json / --output-format 不能同时给"
              "(服务端的 stdout 只能是协议报文)。", file=sys.stderr)
        raise SystemExit(headless.EXIT_ARGS)
    if serve:
        explicit_ws, _ = pop_workspace_flag(argv_for_ws)
        workspace = startup_workspace(explicit_ws)
        raise SystemExit(mcp_server.serve(config, workspace))
    if out_fmt in ("json", "stream-json"):
        # 那份 JSON 必须按 UTF-8 出去:控制台代码页对管道无效,默认会按 cp936 建流,
        # 中文回答与中文路径当场变乱码(打包态真跑抓到的)。
        headless.ensure_utf8_stdout()
    # 模型能力探测(窗口/是否支持视觉):有缓存立即生效,没有就后台问一次
    model_probe.ensure_async(config.base_url, config.api_key, config.model)
    explicit_ws, rest_argv = pop_workspace_flag(argv_for_ws)
    workspace = startup_workspace(explicit_ws)
    ui = TerminalUI(config, workspace)
    # --json / stream-json:外面再包一层静默 UI。横幅/进度/错误一律改道 stderr,
    # 要人点头的入口全部答"不",结局由退出码与最后那份结果说 —— 见 minicode/headless.py。
    hui = (headless.HeadlessUI(
        ui, events=(headless.EventStream()
                    if out_fmt == "stream-json" else None))
        if out_fmt != "text" else None)
    active = hui or ui
    active.banner(key_ok=config.api_key_configured)
    _warn_library_health(workspace, active)

    agent, session = create_agent(config, workspace, active)
    # 留档保留策略:配置里有这个数才动手,默认 0 = 一个都不删(话术与 GUI 走同一个函数)
    apply_retention(config, active, current=session.log_path)

    try:
        _repl(config, workspace, active, agent, session, argv=rest_argv,
              hui=hui, schema=schema, fmt=out_fmt)
    finally:
        agent.close()      # 关掉 MCP 子进程,不留孤儿


def run_json(config, agent, session, hui, task: str, schema=None,
             fmt: str = "json") -> None:
    """无头那一条路:跑一轮,把结局交给 stdout,按结局退出。

    `fmt="json"` 出单份 JSON(调用方 `json.loads(stdout)`);`fmt="stream-json"`
    出 NDJSON 事件流:第一行 `stream_start`,中间是过程事件,最后一行是与前者
    **同构**的 `result`。放在 main 这一侧而不是 headless 里:它要的是"什么时候
    开始跑"这种进程级事实;headless 只管形状与判断,这样它能被测试直接调。
    """
    started = time.perf_counter()
    from minicode import schema as schema_mod
    events = getattr(hui, "events", None)
    if events is not None:
        events.emit("stream_start", task=task, format=fmt,
                    model=getattr(config, "model", "") or "")
    agent.run_turn(task, output_directive=(
        schema_mod.instruction(schema) if schema else ""))
    if events is not None and events.broken:
        # 下游把 stdout 关了:与 Ctrl+C 同一归宿(退出码 5),不新开判断。
        hui.interrupted = True
    result = headless.build_result(hui, agent, session, config, task=task,
                                   elapsed_ms=int((time.perf_counter() - started) * 1000),
                                   fmt=fmt, schema=schema)
    if events is not None:
        events.emit("result", **result)     # 最后一行:与 --json 那份同构 + type/seq
    else:
        print(headless.render(result), flush=True)
    raise SystemExit(result["exit_code"])


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


def _repl(config, workspace, ui, agent, session, argv=None, hui=None,
          schema=None, fmt: str = "json") -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 一次性模式:命令行参数或管道输入(斜杠命令同样支持)
    task = unmanagle_slash_command(argv) if argv else None
    if task is None and not sys.stdin.isatty():
        task = sys.stdin.read().strip() or None
    if hui is not None:
        # 无头只在一次性模式里有意义:REPL 里静默不渲染不叫"无头",叫"看不见"。
        flag = "--json" if fmt == "json" else f"--output-format {fmt}"
        if not task:
            print(f"错误: {flag} 需要一条任务(命令行参数或管道输入)。",
                  file=sys.stderr)
            raise SystemExit(headless.EXIT_ARGS)
        if task.startswith("/"):
            # 斜杠命令的输出是给人看的一段话 —— 让它进来,stdout 那份 JSON 就废了。
            print(f"错误: {flag} 只跑一条任务,不跑斜杠命令。", file=sys.stderr)
            raise SystemExit(headless.EXIT_ARGS)
        run_json(config, agent, session, hui, task, schema=schema, fmt=fmt)
        return
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
            # F2(v0.9.100):对话型命令(内置五条 + 工作区 .commands/)先查展开
            # —— 认识就当普通输入发出去(与手打同一条链);不认识才走管理命令。
            expanded = expand_with_args(text, workspace, ui)
            if expanded is not None:
                agent.run_turn(expanded)
                continue
            if handle_slash(text, session, config, ui, agent):
                ui.info("再见!")
                break
            continue
        agent.run_turn(text)


if __name__ == "__main__":
    main()
