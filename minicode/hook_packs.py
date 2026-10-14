"""内置钩子配方(L3,v0.9.116):把「能用」变成「敢开」。

钩子做成能改能问之后,留下的真实缺口是:要挂一条,得用户自己找到 config.json、
手写一段带转义的 JSON、还不确定占位符怎么拼。这一模块把两条配方做成"说一声就挂上",
代价是必须守住三条:

1. **写的是用户级 config.json,而那是任意命令执行的口子**(hooks.py 开头写死了
   "工作区文件长不出钩子"这条)。所以这里走 preview → nonce → apply 三段,与
   `skill_pack` 同一条纪律:看一眼之后配置被换掉,apply 就该失败而不是照旧写。
   apply 之前**先备份**旧 config.json。
2. **默认一条都不挂**。这不是保守,是这件事唯一的安全形态:自动化一旦默认开,
   就等于用"方便"的名义给用户装了个自动执行的入口。
3. **预览必须把这行命令真会做什么说清楚**。`run-tests` 会在每次改完 .py 之后
   **执行工作区里的代码** —— 这句话不写在预览里,就是开后门还给它起个好评标题。
   它需要这台机器上有 `python`:没有就如实附一行"测试没跑",不拦活,也不装成跑过了。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

# preview 与 apply 之间的凭据只存在于**本进程**:落盘的 nonce 等于把"用户点过头"
# 变成一份别人可以预先造好的文件 —— 那正是 perms-trust 那条教训要防的姿势。
_previews: dict[str, dict] = {}

AUDIT_LOG = "hook-audit.log"          # 与其余本机账同目录,不进工作区
TEST_HELPER = "hook_run_tests.py"     # apply 时写给用户的一只可见助手

# 助手单独写成源码而不是 cmd 里的一串:cmd.exe 拼不出"把 pytest 失败原文包成 JSON"
# 这件事(引号与管道会先吃掉),而一段看得见、能读、能删的 .py 才是用户可以负责的配方。
_TEST_HELPER_SRC = r'''"""minicode 的测试钩子助手(v0.9.116 由 hooks 配方写入)。

每次改完 .py 被 post_tool 叫一次:在工作区跑 `pytest -q`,把结果包成钩子认的 JSON。
只打印一个 JSON 对象 —— hooks.py 只认"整段 stdout 是 JSON",混进别的话就等于没说。
"""
import json
import subprocess
import sys


def out(payload):
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(0)


try:
    body = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:                      # 读不到载荷就别装成跑过了
    out({"context": "测试钩子读不到载荷(%s),这一步没测" % type(e).__name__})

path = str((body.get("args") or {}).get("path") or "")
if not path.endswith(".py"):
    out({})                                 # 不是 Python 文件:一句话都不必说
root = sys.argv[2] if len(sys.argv) > 2 else "."

try:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=root,
                       capture_output=True, text=True, timeout=110,
                       encoding="utf-8", errors="replace")
except FileNotFoundError as e:
    out({"context": "这台机器上跑不起 python(-m pytest),测试没执行:%s" % e})
except subprocess.TimeoutExpired:
    out({"updated_output": "pytest 超过 110 秒没跑完 —— 这一步不替你判对错。"})
except Exception as e:
    out({"context": "测试钩子自己坏了:%s: %s" % (type(e).__name__, str(e)[:120])})

tail = ((r.stdout or "") + (r.stderr or "")).strip()[-4000:]
if r.returncode == 0:
    out({"context": "pytest 通过"})
else:
    # 失败原文回灌:模型当场修,而不是留着"文件已写好"的假象。
    out({"updated_output": tail, "context": "pytest 退出码 %d" % r.returncode})
'''

TEMPLATES: dict[str, dict] = {
    "git-audit": {
        "title": "git 命令审计(只记不拦,不执行任何东西)",
        "risk": "把命令参数 JSON 追加进本机一份日志文件;不跑工作区里的代码。",
        "effect": "以后每次 bash 里跑以 git 开头的命令,参数都被记一行到 "
                  "~/.yinluxs/hook-audit.log。一条都不拦。",
        "hook": {"kind": "pre_tool", "spec": {
            "command": 'type "{args_file}" >> "{audit_log}"',
            "command_prefix": ["git"],
            "on_error": "continue",
            "timeout": 10,
        }},
    },
    "run-tests": {
        "title": "改完 Python 就跑一次 pytest(会执行工作区代码)",
        "risk": "**这条会执行你工作区里的代码**(pytest 收集到什么就跑什么),"
                "并且要求这台机器上有 `python` 与 pytest;没有就每次附一行"
                "「测试没执行」,不拦活但也没测。",
        "effect": "写完/改完 .py 之后,在**当时那个工作区**跑 `python -m pytest -q`,"
                  "失败原文回灌给模型(当场修),通过就只附一句「pytest 通过」。",
        "hook": {"kind": "post_tool", "spec": {
            "command": 'python "{test_helper}" "{result_file}" "{workspace}"',
            "tools": ["write_file", "edit_file"],
            "paths": ["*.py"],
            "on_error": "continue",
            "timeout": 120,
        }},
        "helper": (TEST_HELPER, _TEST_HELPER_SRC),
    },
}


def _app_dir(ctx) -> Path:
    from minicode.paths import app_path
    return app_path("config.json").parent          # 用户级配置那一个目录


def _slot_value(key: str, ctx) -> str:
    """命令里那几个"apply 时就该定下来"的占位符。

    `{workspace}` 不在这里换 —— 它留给运行期(hooks._render 每次调用现填),
    否则用户切了工作区,钩子还会跑去测老目录。
    """
    if key == "audit_log":
        return str(_app_dir(ctx) / AUDIT_LOG).replace("\\", "/")
    if key == "test_helper":
        return str(_app_dir(ctx) / TEST_HELPER).replace("\\", "/")
    return ""


def _render_spec(spec: dict, ctx) -> dict:
    out = dict(spec)
    cmd = str(out.get("command") or "")
    for key in ("audit_log", "test_helper"):
        if "{" + key + "}" in cmd:
            cmd = cmd.replace("{" + key + "}", _slot_value(key, ctx))
    out["command"] = cmd
    return out


def list_line(ctx) -> str:
    """现在挂着什么 + 有哪几条配方。只读,不改任何东西。"""
    cfg = getattr(ctx, "config", None)
    hooks = getattr(cfg, "hooks", None)
    lines = ["已挂的钩子:"]
    if not isinstance(hooks, dict) or not hooks:
        lines.append("  · 一条都没挂(config.json 的 hooks 字段是空的)")
    else:
        from minicode.hooks import TOP_KEYS
        for kind, raw in hooks.items():
            if kind in TOP_KEYS:
                continue     # `failure_mode` 是全局键不是一类钩子(v0.9.173),下面单独说
            cmd = raw if isinstance(raw, str) else (raw or {}).get("command", "")
            lines.append(f"  · {kind}:{str(cmd)[:120]}")
        mode = str(hooks.get("failure_mode") or "").strip()
        if mode:
            lines.append(f"  (全局失败语义:{mode} —— 钩子自己坏了时按它办;"
                         "单条上写了 failure_mode / on_error 的以单条为准)")
    lines.append("可挂的配方:")
    for key, tpl in TEMPLATES.items():
        lines.append(f"  · {key} —— {tpl['title']}")
    lines.append("先 preview 看一眼(话讲全的那一份),再 apply。没点头就一条都不动。")
    return "\n".join(lines)


def preview(ctx, template: str) -> str:
    """把这条配方将要做的事说清楚,并给一次性凭据。不写任何东西。"""
    key = str(template or "").strip().lower()
    tpl = TEMPLATES.get(key)
    if tpl is None:
        return f"错误: 没有这条配方。可选:{'、'.join(TEMPLATES)}"
    hook = tpl["hook"]
    kind, spec = hook["kind"], _render_spec(hook["spec"], ctx)
    cfg = getattr(ctx, "config", None)
    current = getattr(cfg, "hooks", None)
    clash = current.get(kind) if isinstance(current, dict) else None
    scope = {k: v for k, v in spec.items() if k != "command"}
    lines = [f"配方 {key}:{tpl['title']}",
             "", f"它挂在哪一步:{kind}",
             f"  命令:{spec.get('command')}",
             f"  范围:{json.dumps(scope, ensure_ascii=False)}",
             "", f"会发生什么:{tpl['effect']}",
             f"风险:{tpl['risk']}"]
    if tpl.get("helper"):
        name, src = tpl["helper"]
        lines.append(f"另外会写一只助手到 {_app_dir(ctx) / name}"
                     f"({len(src)} 字符,纯标准库,你可以自己打开看、自己删)。")
    if clash:
        cmd = clash if isinstance(clash, str) else (clash or {}).get("command", "")
        lines.append(f"注意:{kind} 上现在挂着一条 —— {str(cmd)[:120]};"
                     "挂这条会**把它替换掉**(旧 config.json 先备份)。")
    stamp = json.dumps({"hooks": current if isinstance(current, dict) else {}},
                       ensure_ascii=False, sort_keys=True)
    nonce = hashlib.sha256(f"{key}|{stamp}|{time.time()}".encode("utf-8")).hexdigest()[:16]
    _previews[nonce] = {"template": key, "kind": kind, "spec": spec,
                        "helper": tpl.get("helper"), "expires": time.time() + 600}
    lines.append(f"要挂上就说一声,让我用 hooks_apply 提交这个凭据:{nonce}"
                 " —— 十分钟有效,过了就得重新 preview。")
    return "\n".join(lines)


def apply(ctx, template: str, nonce: str) -> str:
    """校验凭据后把这条配方写进用户级 config.json:**先备份,失败就回滚内存那份**。"""
    held = _previews.pop(str(nonce or ""), None)
    if not held:
        return ("错误: 这个凭据对不上任何一次 preview(可能过了十分钟,也可能已经用过一次)。"
                "重新 preview 再来。")
    if time.time() > held["expires"]:
        return "错误: 凭据已过期(十分钟),重新 preview。"
    if str(template or "").strip().lower() != held["template"]:
        return "错误: 凭据对应的配方与现在要挂的不是同一条 —— 这活儿不接,重新 preview。"
    cfg = getattr(ctx, "config", None)
    if cfg is None:
        return "错误: 拿不到配置对象,什么都没改。"
    from minicode.config import save_config
    # 走 app_path("config.json") 而不是 import CONFIG_PATH:后者是 import 时按当时的
    # APP_DIR 算死的,而 conftest 那只 autouse 总闸重定向的是 paths.APP_DIR ——
    # 用固定常量就等于让"测试默认不碰真实配置"这条保护在这里失效。
    path = _app_dir(ctx) / "config.json"
    if not path.is_file():
        return (f"错误: 找不到 {path} —— 配方只加到已有的用户配置上,不替没配过的人"
                "凭空造一份(那是替用户决定他要什么)。")
    old_hooks = dict(getattr(cfg, "hooks", None) or {})
    if not _write_helper(ctx, held.get("helper")):
        return "错误: 助手脚本没写成功,配置一条都没动。"
    backup = path.with_name(path.name + ".bak-before-hook")
    try:
        shutil.copyfile(path, backup)
    except OSError as e:
        return f"错误: 备份旧配置失败({type(e).__name__}),什么都没改。"
    hooks = dict(old_hooks)
    replaced = held["kind"] in hooks
    hooks[held["kind"]] = held["spec"]
    cfg.hooks = hooks
    try:
        save_config(path, cfg)
    except OSError as e:
        cfg.hooks = old_hooks              # 磁盘没写成,内存也不许装着写成了
        return (f"错误: 写 {path.name} 失败({type(e).__name__})。"
                "内存里那份已回滚,磁盘上的旧配置没动过。")
    return (f"已挂上 {held['template']}({held['kind']}"
            + ("，**替换掉了原来那条**" if replaced else "") + ")。\n"
            f"旧配置备份:{backup.name}。这一步不用重启就生效(钩子是每次调用现读的)。"
            "要摘掉就说一声,我把 hooks 里那条删回去(备份仍在)。")


def _write_helper(ctx, helper) -> bool:
    """需要助手的配方先把助手落好,再谈改配置 —— 顺序反了会出现"钩子指着没有的文件"。"""
    if not helper:
        return True
    name, src = helper
    target = _app_dir(ctx) / name
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src, encoding="utf-8")
    except OSError:
        return False
    return True
