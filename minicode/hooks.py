"""工具钩子(F3,v0.9.100;升级 H1,v0.9.115):用户在 config.json 里配置的命令。

对齐 ZCode/Claude Code 的 hooks 能力。**配置仍只在 config.json,不认工作区文件**:

- 为什么不认工作区文件:钩子是**任意命令执行**,「clone 别人的仓库带进来一份
  hooks 配置」和 permissions.json 那条教训同源 —— 项目文件不能自证清白,
  任何能从工作区长出钩子的路都是在给恶意仓库发通行证。Config 只从用户的
  config.json 构造,这条路上没有第二个入口。升级这一层**一个入口都没新增**。
- 命令模板的占位符:`{tool}` = 工具名(不敏感,直接进命令行);
  `{args_file}` / `{result_file}` = 临时 JSON 文件的路径 —— 参数与结果
  **经文件交付**,不进命令行(§7 (f) 的口径:密钥暴露进命令行会被权限层拦,
  而工具参数里可能就有敏感内容)。
- **退出码三种命运**(分开是因为该说的话完全不同):
  `0` = 跑成了,整体 stdout 若是 JSON 对象就按结构化决策办;
  `2` = 钩子**明确拒绝**这一步(与 Claude/Cline 同口径),拒绝理由回给模型;
  其它非零 / 超时 / 起不动 = **钩子自己坏了**,拦不拦由失败语义决定 ——
  单条 `failure_mode`(或老名字 `on_error`,两个词都认)优先,其次全局
  `hooks.failure_mode`,都没有 = `closed` 照旧拦。写 `open`(=`on_error:
  "continue"`)则放行并在结果里如实附一行。混成一句话,用户就分不清该改命令
  还是该改做法。**注意 `failure_mode` 只在闸类(`pre_tool`/`permission_request`)
  上决定拦不拦** —— 其余各类本来就不拦东西,写 `open` 在那几类上的含义是
  "钩子坏掉这件事别往模型那边说"(审计里照旧留着)。
- **事件面**(当前起十一类):有工具维度的 `pre_tool` / `post_tool` /
  `post_tool_failure`(失败那半边,与 post_tool 同载荷同档,一次调用只命中一个)/
  `permission_request`;无工具维度的 `session_start` / `user_prompt_submit` /
  `stop` / `subagent_start` / `subagent_stop` / `pre_compact` / `post_compact`
  (见 `TOOL_EVENT_KINDS` / `EVENT_KINDS` 上面的注释)。
  **刻意没做的**:`file_changed` / `config_change` / `cwd_changed`(前两个已有更贴的
  机制:改动可见性走 `_mark_after_mutation` + 时间线,配置改动走 `config.json`
  本身;后一个每次 `cd` 都发一条,噪声远大于信息)。
- 结构化决策可带的字段:`decision`(allow|deny|continue)、pre 的 `updated_input`
  (改写这一步的参数)、post 的 `updated_output`(要回灌给模型的正文,如 lint
  失败原文)与 `context`(一行附注)。**没有"替换结果"的语义** —— 原文丢了就是
  丢了,所以后两样都是追加;普通 stdout 文本照旧丢弃,不改没动过配置的人的行为。
- matcher 除了 tools/paths,新增 `command_prefix`(bash 的参数叫 command,
  旧形状下"只对 git 开头的命令审计"表达不出来)与 `args_match`(通用逃生口,
  沿用同一种 fnmatch 语法,不引入第二套正则)。
- 每次工具调用都会跑一次认领它的钩子 —— 这是用户自己配的,代价他配的时候就知道;
  文档写明。钩子命令走系统 shell(cmd.exe),要 PowerShell 就显式写
  `powershell -Command ...`。
"""
from __future__ import annotations

import fnmatch
import json
import subprocess
import tempfile
import time
from pathlib import Path

HOOK_TIMEOUT = 10       # 单条钩子命令的默认超时;超时按"钩子没跑成"处理(见 on_error)
HOOK_TIMEOUT_MAX = 120  # per-entry timeout 的硬上限:钩子跑在用户等的那条路上,
                        # 让它无限期挂着等于把界面交给一条外部命令
HOOK_NOTE_CHARS = 1200      # 附注一句话的上限(截了几字要在正文里写明)
HOOK_OUTPUT_CHARS = 8000    # 要回灌给模型的正文(lint/测试原文)的上限
HOOK_STDOUT_CHARS = 65536   # 解析用的 stdout 读数上限:再多也只可能是刷屏不是结论
EXIT_DENY = 2           # 与 Claude/Cline 同一口径:2 = 我(钩子)明确拒绝这一步

# 有"哪只工具"这个维度的事件 —— `tools` / `paths` / `command_prefix` / `args_match`
# 四个 matcher 只在这几类上说得通(见 `_matches`)。
#
# `post_tool_failure`(v0.9.173)是 `post_tool` 的**失败那半边**:同一次调用只会命中
# 其中一个(成功了走 post_tool、失败了走它),载荷同形状(带 args 与 result)。
# 为什么值得单开一格而不是让 post_tool 自己看 result:①"失败"的判定在这个项目里
# 只有一处(`toolsum.is_failure`),钩子里手写一份字符串匹配迟早跟它对不上;②外部
# 系统要的是**一条条失败事件**,连续性它自己数得出来 —— 我们这边的
# `_fail_streak_count` 是**本回合内**的内部计数(跨回合清零),塞进载荷反而误导。
TOOL_EVENT_KINDS = ("pre_tool", "post_tool", "post_tool_failure", "permission_request")

# 非工具类事件(H4,v0.9.115;`stop` v0.9.151;子代理那两只 v0.9.173):没有"哪只工具"
# 这个维度,也只能附一句话 —— 既不改写参数、不接管结果,更拦不住用户开口说的那句话。
# **配了 tools/paths 也不会被筛成永不触发**(见 `_matches`)。
#
# `stop` 与另外几个**时机不同**:它在一轮**结束时**跑,而那一轮已经发不出请求了,
# 所以它附的那句话落在**下一轮的第一条请求**上(见 `Agent._fire_stop_hooks`)。
# 这不是缺陷,是这一格的边界 —— 钩子改不了已经发生的事,能做的只有"给下一轮留句话"
# 与"把这一轮记下来"。
#
# 子代理那两只由 `run_subagent` 那一层发,**不由子代理自己发** —— 子代理里
# `is_sub=True`,连 session_start 都不叫;派活与收口这两件事本来就是主代理做的。
# 一进一出各留一句话:`subagent_start` 那句附在**子代理读到的任务文本**上(与
# session_start 附在这次请求里对称),`subagent_stop` 那句附在**回主代理的报告**上
# (与 stop 留给下一轮对称)。两处都只加一句话,不改派活内容、不改结论。
EVENT_KINDS = ("session_start", "user_prompt_submit", "stop",
               "subagent_start", "subagent_stop", "pre_compact", "post_compact")

ALL_KINDS = TOOL_EVENT_KINDS + EVENT_KINDS

# `hooks` 底下**不是**"某一类钩子"的键(全局开关):遍历配置时要跳过 —— 否则
# `failure_mode` 会被当成一类钩子名显示出来(见 `hook_packs.list_line`)。
TOP_KEYS = ("failure_mode",)

# 钩子**自己坏了**(起不动 / 超时 / 非零退出且不是 EXIT_DENY)时怎么办:
# `closed` = 拦(今天的默认,即 `on_error: "block"`);`open` = 放行(`on_error:
# "continue"`)。两个词都认 —— `failure_mode` 是对标矩阵里的叫法(Cline 的
# fail_open/fail_closed),`on_error` 是本项目 v0.9.115 起就在用的叫法。
# **同一件事不留第二套实现**,只是两边的用户都念得出口。单条上写的赢过全局,
# 两边都没写 = `closed`(与 `on_error` 缺省 block 逐字节一致)。
FAILURE_MODES = {"closed": "closed", "block": "closed", "fail_closed": "closed",
                 "open": "open", "continue": "open", "fail_open": "open"}


def failure_mode(spec: dict, hooks_cfg: dict | None) -> str:
    """这一条钩子坏掉时是拦(`closed`)还是放(`open`)。

    优先级:单条 `failure_mode` → 单条 `on_error` → 全局 `hooks.failure_mode`
    → `closed`。**不认识的值一律按 `closed`**(与今天 `on_error != "continue"`
    就拦同一条口径):一个拼错的开关不该悄悄把审计变成"什么都不拦"。
    """
    for raw in (spec.get("failure_mode"), spec.get("on_error"),
                (hooks_cfg or {}).get("failure_mode")):
        if raw is None or not str(raw).strip():
            continue
        got = FAILURE_MODES.get(str(raw).strip().lower())
        if got:
            return got
        return "closed"      # 写了但不认识 —— 按拦办,别猜用户想放行
    return "closed"


def _as_list(val) -> list:
    """一维配置允许写字符串或数组两种形状(`"command_prefix": "git"` 与 `["git", "npm"]`)。"""
    if val is None:
        return []
    if isinstance(val, (list, tuple, set)):
        return [x for x in val if str(x).strip()]
    return [val] if str(val).strip() else []


def _glob(value: str, pattern: str) -> bool:
    """通配匹配:反斜杠按正斜杠口径归一(Windows 传参形状),只此一种语法。"""
    return fnmatch.fnmatch(str(value).replace(chr(92), "/"),
                           str(pattern).replace(chr(92), "/"))


def _clip_note(text: str, limit: int = HOOK_NOTE_CHARS) -> str:
    """封顶但如实说封了几字 —— "只显示了一段"却写成"这就是全部"是这个项目不许的。"""
    body = str(text or "").strip()
    if len(body) <= limit:
        return body
    return body[:limit] + f"\n…(钩子输出共 {len(body)} 字,上面只留了 {limit} 字)"


def _timeout_for(spec: dict) -> "tuple[int, str]":
    """取这条钩子的超时,夹进 [1, HOOK_TIMEOUT_MAX];被夹时带回一句要如实说的话。"""
    raw = spec.get("timeout")
    if raw is None:
        return HOOK_TIMEOUT, ""
    try:
        want = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return HOOK_TIMEOUT, f"钩子配的 timeout「{raw}」不是数字,按默认 {HOOK_TIMEOUT} 秒跑"
    if want < 1:
        return 1, "钩子配的 timeout 小于 1 秒,按 1 秒跑"
    if want > HOOK_TIMEOUT_MAX:
        return HOOK_TIMEOUT_MAX, (f"钩子要 {want} 秒,超过上限 {HOOK_TIMEOUT_MAX} 秒,"
                                  f"按上限跑(钩子跑在你等的那条路上)")
    return want, ""


def _decision(raw: str) -> str:
    """把钩子声明的表态归一:不认识的取值一律当"没表态"。"""
    text = str(raw or "").strip().lower()
    return text if text in ("allow", "deny", "continue") else "continue"


def _parse_stdout(out: str) -> "tuple[dict, str]":
    """stdout 整体是一个 JSON 对象就当结构化决策,否则整段当给人看的一句话。

    只认"整段能解析成对象"这一种:从一屏混排文本里猜哪一段是 JSON,等于让用户
    的一次格式化失误变成一次静默的权限变化。
    """
    body = str(out or "").strip()
    if not body:
        return {}, ""
    try:
        got = json.loads(body)
    except (ValueError, TypeError):
        return {}, body
    if isinstance(got, dict):
        return got, str(got.get("reason") or got.get("output") or "").strip()
    return {}, body


def matched(kind: str, hooks_cfg: dict | None, tool: str,
            args: dict | None = None) -> bool:
    """这一步会不会跑这条钩子 —— **不启动任何进程**,给调用方做红线预检用。

    要这一条是因为"钩子可以改写参数"与"硬拒根本不叫钩子"这两句凑在一起时,
    必须先知道有没有钩子认领这一步,才能决定要不要在白名单确认框之前先跑门。
    """
    got = _entry(hooks_cfg, kind)
    if got is None:
        return False
    _cmd_tpl, spec = got
    return _matches(spec, tool, args)


def run_hook(kind: str, hooks_cfg: dict | None, tool: str = "",
             args: dict | None = None, result: str | None = None,
             workspace: str = "") -> "dict | None":
    """跑一条钩子;没配置该类返回 None(调用方直接跳过,零开销)。

    各类钩子共用这一份形状:`pre_tool`(拦/改写这一步)、`post_tool`(回灌正文)、
    `post_tool_failure`(v0.9.173:同一次调用**失败**时走它,载荷与 post_tool 一样)、
    `permission_request`(H3,v0.9.115:替用户在确认框之前答一句 allow/deny)、
    `session_start` / `user_prompt_submit` / `stop` / `subagent_start` /
    `subagent_stop` / `pre_compact` / `post_compact`(H4 起:只能附一句话 ——
    前两个附在**这一次请求**,`stop` 附在
    **下一轮的第一条请求**,子代理那两只一进一出各附一句,见 `EVENT_KINDS` 上面那段)。
    `updated_input` 只在 pre 上成立 —— 门已经对 permission_request 判过 ASK 了,
    这时候再改参数就是"对 A 点头、执行 B";`updated_output` 只在两个 post 上成立;
    `context` 各类都能带。

    H1 的形状(旧的两个键名字与语义都保留,配了钩子的人不改配置行为不变):

    - `ok` / `blocked` —— 老口径。pre 的"没跑成"与非零退出仍归 blocked,
      但**只有 exit 2 与 `decision:"deny"` 才是"钩子明确拒绝"**,其它非零退出
      按"钩子自己坏了"走失败语义(单条 `failure_mode` / `on_error` 优先,其次全局
      `hooks.failure_mode`,都没有 = `closed` 拦)。
      分开是因为这两种情况该说的话完全不同:一种是有人判了这一步,
      一种是一条命令没跑成 —— 混成一句话,用户就再也分不清该改命令还是该改做法。
    - `decision` —— allow|deny|continue(不认识的值当 continue)。
    - `updated_input` —— 仅 pre_tool 有意义:改写这一步的参数。形状只在这里查
      (必须是非空 dict),**必填项与参数名由调用方对着 schema 再查、权限门由调用方
      重跑** —— 门的真相只有一处,不在钩子层复刻第二套。
    - `updated_output` / `context` —— 仅两个 post 有意义:要回灌给模型的正文
      (如 lint/测试的失败原文)与一行附注。**没有"替换结果"的语义**,原文丢了
      就是丢了,所以这两样都是追加(见 agent._finish 的接法)。
    - `failure_mode` / `suppressed` —— 这次按哪种失败语义走的,以及"钩子坏了这句
      要不要往模型那边说"(非闸类 + `open` 时为真;审计里照旧留着)。
    - `elapsed_ms` / `ran` / `hook` —— 实测耗时与"到底跑没跑",给观测线用。
    """
    got = _entry(hooks_cfg, kind)
    if got is None:
        return None
    cmd_tpl, spec = got
    if not _matches(spec, tool, args):
        return None
    payload_key = "result_file" if kind in ("post_tool", "post_tool_failure") else "args_file"
    if kind in ("post_tool", "post_tool_failure"):
        # H6:post 的载荷原来只有 {tool, result} —— 一条"写完 .py 顺手 lint 一遍"
        # 的钩子拿不到路径,只能去解析给人看的那句结果文本,而结果文本不是接口。
        # 加 `args` 是**追加键**:老钩子照旧读它的 tool/result,一个字都不用改。
        # `post_tool_failure` 用**同一份载荷**(加一个 `event`) —— 失败那半边的钩子
        # 想做的事(记一笔、报给外部系统、回灌一句"别重试同一个参数")与成功那半边
        # 一样需要 tool/args/result,两套形状只会让人抄错一份。
        payload = {"tool": tool, "args": args or {}, "result": result}
        if kind == "post_tool_failure":
            payload["event"] = kind
    else:
        payload = args

    cmd, tmp = _render(cmd_tpl, tool, payload, payload_key, workspace)
    seconds, timeout_note = _timeout_for(spec)
    mode = failure_mode(spec, hooks_cfg)
    started = time.perf_counter()
    exit_code: "int | None" = None
    raw = ""
    broken = ""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=seconds,
                           encoding="utf-8", errors="replace")
        exit_code = r.returncode
        raw = ((r.stdout or "") + (r.stderr or ""))[:HOOK_STDOUT_CHARS]
    except subprocess.TimeoutExpired:
        broken = f"钩子超过 {seconds} 秒没跑完"
    except OSError as e:
        broken = f"{type(e).__name__}: {e}"
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    elapsed = int((time.perf_counter() - started) * 1000)

    spec_out, reason = _parse_stdout(raw)
    decision = _decision(spec_out.get("decision"))
    if broken:
        # 没跑成:拦不拦由 `failure_mode`(老名字 `on_error`)决定,而**只有闸类**
        # (pre_tool / permission_request)拦得住东西 —— 见下面 `blocked` 那两行。
        # 钩子自己说的话排在后面 —— 它可能已经打了一半才卡住,那句才是线索。
        ok, declared = False, False
        decision = "deny" if mode == "closed" else "continue"
        reason = broken + (f" — 它卡住前还说了:{reason[:200]}" if reason else "")
    elif exit_code == EXIT_DENY:
        ok, decision, declared = False, "deny", True
    elif exit_code not in (0, None):
        ok, declared = False, False
        decision = "deny" if mode == "closed" else "continue"
        reason = (reason or f"钩子退出码 {exit_code}").strip()
    else:
        ok = True
        # "明确表过态"= 钩子跑成且话说死了(allow 或 deny)。exit 0 只打了一句
        # 日志、没给 decision 的,算"没表态"—— H3 那一类靠这个字段决定要不要
        # 替用户点头,把"没说话"当成"说了允许"是最贵的一种错位。
        declared = decision in ("allow", "deny")
    # post 永远不拦(老口径)。pre 与 permission_request 都只认 "deny" 这一个字,
    # 但调用方接的是两件不同的事:pre 的 deny = 这一步不许跑;permission_request
    # 的 deny = **替用户答了一句"不"**。后者只在钩子真跑成、并且明确表过态时才成立
    # —— 钩子没跑成的时候,agent 一律退回"照常弹框"(见 `_ask_via_permission_hook`),
    # 一条坏掉的策略钩子既不配替人拒绝、更不配替人放行。
    # `post_tool_failure` 与 `post_tool` 同档:**它是一次观测,不是一道闸** ——
    # 失败已经发生了,一条钩子的 stdout 不该有权力改写"这一步到底算不算失败"。
    blocked = (kind in ("pre_tool", "permission_request") and decision == "deny")

    got_input = None
    if kind == "pre_tool":
        # 形状只查到这里:值必然来自 json.loads,可序列化是它的前提,再试一次
        # dumps 是给不会发生的场景兜底。必填与参数名归 agent._apply_hook_input,
        # 业务合法性归 handler —— 每样都只有一处说得上话。
        raw_input = spec_out.get("updated_input")
        if isinstance(raw_input, dict) and raw_input:
            got_input = raw_input

    out_text = _clip_note(reason, HOOK_NOTE_CHARS)
    if timeout_note:
        out_text = (out_text + "\n" if out_text else "") + timeout_note
    # 非工具类事件(H4)只带走一样东西:context。`deny` 在这几类上**不生效** ——
    # 钩子拦不住用户开口说的那句话(子代理那两只也一样:它们拦不住一次派活)。
    # 这既没配过也没实现过。但"配了一句拒绝、什么也没拦住"必须当场说出来,
    # 悄悄吞掉等于教用户去信任一个不存在的能力。
    if kind in EVENT_KINDS and decision == "deny":
        out_text = (out_text + "\n" if out_text else "") + \
            "(这一类钩子只能附一句话,拦不下这一步,deny 没生效)"

    # 只有**结构化**声明的两样才会被调用方追加进结果:普通 stdout 文本今天
    # 是丢掉的,把它当成"要回灌的正文"等于改了没动过配置的人的行为。
    body = _clip_note(str(spec_out.get("updated_output") or ""),
                      HOOK_OUTPUT_CHARS) \
        if kind in ("post_tool", "post_tool_failure") else ""
    extra = _clip_note(str(spec_out.get("context") or ""), HOOK_NOTE_CHARS)
    # `failure_mode: "open"` 在**非闸类**上唯一说得通的含义:钩子自己坏了这件事
    # 别往模型那边说(它在审计里照旧留着)。闸类上它已经决定了拦不拦,不需要再来
    # 一遍 —— 那一格由 `blocked` 说话。默认 closed = 今天的表现(坏了就如实附一行)。
    suppressed = bool(not ok and mode == "open"
                      and kind not in ("pre_tool", "permission_request"))
    return {"ok": ok, "blocked": blocked, "decision": decision,
            # declared = 这一步是被"有人判了"拦下的,不是被一条没跑成的命令拦下的。
            # 调用方靠它决定回话怎么说:前者该问做法,后者该修钩子。
            "declared": declared, "output": out_text, "reason": reason,
            "updated_input": got_input, "updated_output": body, "context": extra,
            "elapsed_ms": elapsed, "ran": True, "hook": kind,
            "failure_mode": mode, "suppressed": suppressed,
            "exit_code": exit_code}


def _render(cmd: str, tool: str, payload: dict | None, payload_key: str,
            workspace: str = "") -> "tuple[str, Path | None]":
    """模板替换:{tool} 与 {workspace} 进命令行,{args_file}/{result_file} 落临时文件。

    `{workspace}` 是 L3(v0.9.116)补的:钩子命令由 `subprocess(shell=True)` 起,**继承
    本进程的 cwd** —— 源码态 cwd 一般就是工作区,而打包版 GUI 的 cwd 是安装目录。
    没有这个占位符,"改完跑一次 pytest"这类配方会在错误的目录里跑,而且**只在装好的包里
    发作**(开发态一切正常),那是最难查的一类坏。
    """
    text = str(cmd or "").replace("{tool}", tool)
    if "{workspace}" in text:
        text = text.replace("{workspace}", str(workspace or ""))
    tmp: Path | None = None
    if "{" + payload_key + "}" in text and payload is not None:
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8")
        try:
            json.dump(payload, fd, ensure_ascii=False)
        finally:
            fd.close()
        tmp = Path(fd.name)
        text = text.replace("{" + payload_key + "}", str(tmp))
    return text, tmp


def _entry(hooks_cfg: dict | None, kind: str) -> "tuple[str, dict] | None":
    """取出该类钩子的 (命令模板, 配置对象);没配返回 None。

    两种形态(C2,v0.9.102 起):字符串 = 对**所有**工具跑(老形状,向后兼容);
    对象 = {"command": …, "tools": [工具名…], "paths": ["通配符…"]} ——
    matcher 语义:tools 缺省 = 全部;paths 只对带 path 参数的工具生效,
    工具没有 path 或路径不匹配任何一个通配符 = 不跑。
    """
    raw = (hooks_cfg or {}).get(kind)
    if not raw:
        return None
    if isinstance(raw, str):
        return raw, {}
    if isinstance(raw, dict) and str(raw.get("command") or "").strip():
        return str(raw["command"]), raw
    return None


def _matches(spec: dict, tool: str, args: dict | None) -> bool:
    """matcher 判定:全部声明的维度都过才跑;一个都没声明 = 对所有工具跑。

    **非工具类事件(`tool` 传空)一律放行**:session_start / user_prompt_submit / stop
    没有"哪只工具"这个维度,若照旧走下面的判定,用户在那类钩子上写的 `tools`/`paths`
    会把它筛成永不触发 —— 配了却不跑、又没人说话,是这批里最难查的一种坏。

    H1 补的两维:
    - `command_prefix` 治的是"paths 只对带 path 参数的工具有效,而 bash 的参数
      叫 command"——旧形状下"只对 git 开头的命令审计"根本表达不出来。
      大小写不敏感(命令行里的 git/Git/GIT 是同一个东西)。
    - `args_match` {参数名: 通配符} 是通用逃生口(如 mcp 工具的 url),沿用
      paths 那一种 fnmatch 语法与正斜杠归一,**不引入第二套正则语法**。
    """
    if not tool:
        return True
    tools = spec.get("tools")
    if tools:
        names = [str(t).strip() for t in tools if str(t).strip()]
        if names and tool not in names:
            return False
    paths = spec.get("paths")
    if paths:
        pats = [str(x).strip() for x in paths if str(x).strip()]
        path = str((args or {}).get("path") or "")
        if not path:
            return False          # 指定了 paths 而这只工具没有路径参数 = 不适用
        if not any(_glob(path, pat) for pat in pats):
            return False
    prefixes = _as_list(spec.get("command_prefix"))
    if prefixes:
        cmd = str((args or {}).get("command") or "").strip()
        if not cmd:               # 声明了 command_prefix 而这只工具没有命令参数
            return False
        low = cmd.lower()
        if not any(low.startswith(str(p).strip().lower()) for p in prefixes
                   if str(p).strip()):
            return False
    args_match = spec.get("args_match")
    if isinstance(args_match, dict) and args_match:
        for key, pat in args_match.items():
            val = str((args or {}).get(str(key)) or "")
            pats = [str(x).strip() for x in _as_list(pat) if str(x).strip()]
            if not val or not any(_glob(val, p) for p in pats):
                return False      # 参数缺席或通配不上:这条钩子跟这一步无关
    return True
