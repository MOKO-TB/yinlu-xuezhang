"""权限确认框里的「为什么要做这一步」解析。

用户点「允许 / 拒绝」之前需要三样信息:
1. why    —— 现在为什么要做这件事(模型填的 reason > 同一轮里模型写的说明文字);
2. digest —— 这条命令/这次改动到底干什么,翻译成人话;
3. risks  —— 影响面(改文件、删文件、联网、能不能撤销)。

digest 是启发式解析,覆盖常见命令形态;认不出来就老实说"执行一条脚本/命令",
不编造意图。为什么放在一个独立模块:终端与 GUI 两套界面都要用同一份措辞,
而且这块逻辑最需要单测。
"""
from __future__ import annotations

import re

MAX_WHY = 200

# (正则, 人话解读) —— 从具体到宽泛,取第一个命中的
_CMD_RULES: list[tuple[str, str]] = [
    (r"\bpytest\b|python[0-9.]*\s+-m\s+pytest", "运行测试"),
    (r"\bgit\s+status\b|\bgit\s+diff\b|\bgit\s+log\b", "查看代码仓库状态与改动"),
    (r"\bgit\s+(commit|push|pull|merge|rebase|checkout|reset|add)\b", "执行 git 版本操作"),
    (r"\b(pip|uv|npm|pnpm|yarn)\s+(install|add|i)\b|\b(pip|uv)\s+install\b", "安装依赖包(会联网下载)"),
    (r"\b(curl|wget)\b|invoke-webrequest|irm\s", "从网络下载内容"),
    (r"\b(rm|del|erase)\b|remove-item", "删除文件或目录"),
    (r"\b(mv|move|cp|copy|xcopy|robocopy)\b", "复制或移动文件"),
    (r"\b(mkdir|md)\b|new-item\s+-itemtype\s+directory", "创建目录"),
    (r"\b(grep|rg|findstr|select-string)\b", "在文件内容里搜索关键词"),
    (r"\b(ls|dir|find|tree|get-childitem)\b", "列出目录、查找文件"),
    (r"\b(cat|head|tail|less|more|type|get-content)\b", "查看文件内容"),
    (r"\bpython[0-9.]*\b|\bpy\b\s", "运行一段 Python 脚本"),
    (r"\bnode\b|\bnpx\b", "运行 Node 脚本"),
    (r"^\s*echo\b", "输出一段文本"),
]

_READONLY_TOOLS = {"read_file", "glob", "grep", "search_knowledge", "use_skill",
                   "library_search", "map_find_place", "map_show_list",
                   "map_highlight_building", "map_drop_pin", "map_clear",
                   "browser_status"}


def _clean(text: str) -> str:
    s = re.sub(r"`{1,3}", "", str(text or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _narration_tail(narration: str) -> str:
    """取说明文字里最后一段完整的话——它通常正对着当前这一步。"""
    parts = [p for p in re.split(r"\n{2,}", str(narration or "")) if p.strip()]
    return _clean(parts[-1] if parts else "")


def command_digest(command: str) -> str:
    """把一条 shell 命令翻译成一句人话(认不出来就如实说)。"""
    cmd = str(command or "").strip()
    if not cmd:
        return "执行一条空命令"
    low = cmd.lower()
    steps = len(re.findall(r"&&|;|\|\s*\w", cmd))
    for pattern, text in _CMD_RULES:
        if re.search(pattern, low):
            out = text
            if "python" in low and "json" in low:
                out = "用 Python 读取并解析数据文件"
            if steps:
                out += "(一条命令里含多个步骤)"
            return out
    return "执行一条 shell 命令(无法自动判断具体目的,请看下方原命令)"


def risk_notes(tool_name: str, args: dict) -> list[str]:
    """这次操作的影响面提示。"""
    name = str(tool_name or "").rsplit("·", 1)[-1]
    notes: list[str] = []
    if name == "bash":
        cmd = str(args.get("command", ""))
        low = cmd.lower()
        if re.search(r"rm\b|remove-item|\bdel\b|erase\b", low):
            notes.append("含删除操作,删除后无法恢复")
        if re.search(r">>?\s*[^&\s|]|tee\b|sed\s+-i|out-file|set-content|add-content", low):
            notes.append("会写入或覆盖文件")
        if re.search(r"curl|wget|invoke-webrequest|https?://|\bpip\b.*install|\bnpm\b.*install", low):
            notes.append("会联网访问外部服务")
        if re.search(r"git\s+(push|reset|clean|checkout)|--force\b", low):
            notes.append("会改动代码仓库状态,可能覆盖他人提交或本地改动")
        if not notes:
            notes.append("看起来是只读操作,不会改动文件")
        notes.append("bash 产生的改动不在「撤销」的跟踪范围内")
    elif name == "write_file":
        notes.append("会写入文件,原内容可用「撤销」回滚")
    elif name == "edit_file":
        notes.append("会修改文件内容,可用「撤销」回滚")
    elif name in _READONLY_TOOLS:
        notes.append("只读,不会改动任何文件")
    elif name == "browser_open":
        mode = str(args.get("mode", "") or "auto")
        if mode == "launch":
            notes.append("会用一个独立的无窗口浏览器实例(不带你现有的登录态)")
        else:
            # auto 也可能落到用户自己的浏览器:确认框不能只按字面参数说话。
            # (纯文本框,别写 markdown 强调符,会照原样显示)
            notes.append("会启动/使用本机浏览器联网访问页面")
            notes.append(
                "将使用你自己浏览器的登录态(页面内容以你的身份取得)" if mode == "attach"
                else "若你正开着远程调试,可能会改用你自己的浏览器(带你的登录态)")
    elif name == "browser_eval":
        notes.append("会在打开的页面里执行这段 JavaScript(可触发跳转/点击)")
    elif name == "browser_click":
        notes.append(f"会在打开的页面里点击「{args.get('target', '')}」,可能引起页面跳转")
    elif name == "browser_fill":
        keys = list(args.get("fields").keys())[:6] \
            if isinstance(args.get("fields"), dict) else []
        notes.append("会在打开的页面的表单里填写字段"
                     + (f"({'、'.join(str(k) for k in keys)})" if keys else ""))
        if args.get("submit"):
            notes.append("填完会自动点提交按钮,可能把表单发送出去")
    elif name == "browser_screenshot":
        notes.append("会往工作区写入截图文件(需手动删,「撤销」不跟踪它)")
    elif name == "browser_close":
        notes.append("只关掉引路学长自己打开的标签页,不会动你其他的页面")
    elif name == "web_search":
        notes.append("会联网访问搜索引擎并抓取结果页")
    elif name == "kb_suggest":
        notes.append("会把一条问答追加到应用目录的候选文件(不改动正式知识库)")
    elif name == "reminder":
        notes.append("会改动本机的提醒清单文件(reminders.json)")
    elif name == "schedule":
        notes.append("action=save 会把课表写进应用目录的 schedule.json(只存本机,不上传);"
                     "action=clear 会删掉它")
    elif name == "py_script":
        notes.append("会用内置 Python 运行时执行这个脚本(能读写工作区文件、可能联网)")
        notes.append("脚本产生的改动不在「撤销」的跟踪范围内")
    return notes


def action_reason(tool_name: str, args: dict, reason: str = "",
                  narration: str = "") -> str:
    """当前这一步的目的:模型填的 reason 优先,其次同一轮里的说明文字。"""
    for candidate in (reason, _narration_tail(narration)):
        text = _clean(candidate)
        if text and text not in ("（无）", "(无)"):
            return text[:MAX_WHY] + ("…" if len(text) > MAX_WHY else "")
    name = str(tool_name or "").rsplit("·", 1)[-1]
    if name == "bash":
        return "模型没有留下这一步的说明,请对照下面的命令自行判断"
    return f"模型没有留下这一步的说明,这一步是 {name} 操作"


def permission_why(tool_name: str, args: dict, reason: str = "",
                   narration: str = "") -> dict:
    """权限确认框要展示的完整解析。"""
    name = str(tool_name or "").rsplit("·", 1)[-1]
    digest = command_digest(args.get("command", "")) if name == "bash" else ""
    return {
        "why": action_reason(tool_name, args, reason, narration),
        "digest": digest,
        "risks": risk_notes(tool_name, args),
    }
