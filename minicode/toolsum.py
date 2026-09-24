"""工具调用的「一句话说法」与「成没成」唯一口径(v0.9.37 / v0.9.39 Z1)。

为什么单独一个模块:同一件事在三个地方要写 —— 终端一行 `● bash …`、聊天里的工具
卡片、右侧任务监控的时间线。原来 gui_ui 与 ui 各有一份 `_arg_summary`,已经差在
截断长度上;再加第三份就是「聊天里说改了 a.py、监控栏里那行却写着别的」。

这里只放**纯函数**:给工具名 + 参数 dict,回一句给人看的话。不碰磁盘、不碰界面,
所以时间线能直接拿会话消息里的原始参数复算出同一句话 —— 这也是「实时表」能对上
号的钥匙:`tool_start` 推的是这句,时间线扫消息算的也是这句,两边同函数同参数
才会同结果。

`VERBS` 是另一件同样的事:工具名是英文的,监控栏要写中文动作。没收录的工具
(尤其 MCP 那种 `server·tool` 命名的)原样显示名字,**不猜它是干什么的**。
"""
from __future__ import annotations

# 子代理的工具名带类型前缀(explore·read_file),展示时摘掉前缀,类型另说
SUBAGENT_PREFIX_SEP = "·"

# 工具名 -> 监控栏里那个动作词。刻意只收**确定**的:不确定就不写,
# 宁可显示 `py_script` 也不要编一个"跑了脚本"结果它其实是别的东西。
VERBS: dict[str, str] = {
    "read_file": "读文件", "write_file": "写文件", "edit_file": "改文件",
    "glob": "找文件", "grep": "搜代码", "bash": "跑命令", "py_script": "跑脚本",
    "web_search": "搜网页", "web_crawl": "抓网页", "page_parse": "解析表格",
    "browser_open": "打开页面", "browser_eval": "页面取数",
    "browser_click": "点击页面", "browser_fill": "填表单",
    "browser_screenshot": "截图", "browser_snapshot": "取页面快照",
    "browser_close": "关浏览器", "browser_status": "看浏览器状态",
    "search_knowledge": "查知识库", "kb_suggest": "记候选知识",
    "memory": "记忆", "plan": "计划", "task": "派子代理",
    "schedule": "查课表", "get_weather": "查天气", "campus_calendar": "查校历",
    "reminder": "待办", "mood": "情绪日记", "gpa_calc": "算绩点",
    "library_search": "查馆藏", "library_guide": "馆藏指引",
    "map_find_place": "地图找地点", "map_highlight_building": "地图标注",
    "map_show_list": "地图列表", "map_drop_pin": "地图打点", "map_clear": "清地图",
    "desktop_notify": "桌面通知", "use_skill": "用技能",
}


def short_name(name: str) -> str:
    """去掉子代理前缀后的工具名(`explore·read_file` → `read_file`)。"""
    return str(name or "").rsplit(SUBAGENT_PREFIX_SEP, 1)[-1]


def agent_type(name: str) -> str:
    """子代理工具名的类型前缀(`explore·read_file` → `explore`);普通工具为空。"""
    text = str(name or "")
    return text.rsplit(SUBAGENT_PREFIX_SEP, 1)[0] if SUBAGENT_PREFIX_SEP in text else ""


def verb(name: str, args: dict | None = None) -> str:
    """这一行该显示成什么动作。MCP 工具(`server·tool`)与没收录的名字原样回。"""
    raw = str(name or "")
    if SUBAGENT_PREFIX_SEP in raw and short_name(raw) not in VERBS:
        return raw                                  # MCP:整串就是它的名字
    return VERBS.get(short_name(raw), short_name(raw) or "工具调用")


def summarize(name: str, args: dict | None, limit: int = 160) -> str:
    """把参数摊成一行摘要(路径/网址/命令优先,其余按 k=v 列)。"""
    args = args if isinstance(args, dict) else {}
    key = short_name(name)
    if key == "bash":
        s = str(args.get("command", ""))
    elif key in ("write_file", "edit_file", "read_file"):
        s = str(args.get("path", ""))
    elif key == "browser_open":
        s = str(args.get("url", ""))                # 卡片上直接看到要开哪个网址
    elif key == "browser_eval":
        s = "JS: " + " ".join(str(args.get("js", "")).split())
    elif key == "browser_click":
        s = str(args.get("target", ""))
    elif key == "browser_fill":
        keys = list((args.get("fields") or {}).keys())
        s = "填表单: " + "、".join(str(k) for k in keys[:6]) + ("…" if len(keys) > 6 else "")
    elif key in ("browser_screenshot", "browser_close", "browser_status"):
        s = str(args.get("path", "")) or "当前页面"
    else:
        s = "  ".join(f"{k}={str(v)[:40]}" for k, v in args.items())
    return str(s or "")[:limit]


# ------------------------------------------------------------ 这一步成没成

# 结果文本以这些开头 = 这一步**没成**。`Agent._execute` 里每个失败出口都写成这个
# 句式(参数不对 → `错误: …`;抛异常 → `工具执行出错: …`;用户点拒绝 → `用户拒绝了…`),
# 所以判断只需要看开头。
#
# 为什么提到这里(v0.9.39 Z1 第一步):同一句 `result.startswith(("错误", "工具执行出错",
# "用户拒绝"))` 原来在 `gui_ui.tool_done`、`ui.tool_done` 各抄了一份字面量,加上
# `activity.FAILED_PREFIXES` 共三份。三份各自抄的后果不是难看,是**会不一致**:
# 新增一种失败措辞(或改一句文案)时,聊天里工具卡片打 ✓、监控栏那行写失败、终端 ✗ ——
# 而没有任何一处会报错。前端只信 `tool_done` 推过去的 `ok` 字段,所以 Python 这一侧
# 就是唯一真相,这里收口之后它只剩一个来源。
#
# 边界照实说:这套判断认的是**文案**,不是结构化的失败标记。工具自己 return 一句
# 「错误: …」以外的失败描述(比如「做了一半」),这里仍算成功 —— 那是文案纪律,
# 由 `tests/test_tool_outcome.py` 拿真的 Agent 跑一遍逐条对。
FAILED_PREFIXES = ("错误", "工具执行出错", "用户拒绝")

ERROR_PREVIEW = 300          # 失败时给人看的那段原文截多长(终端与界面同一个数)


def is_failure(result) -> bool:
    """这一次工具调用的结果算不算「没成」。非字符串不算(宁可标对,不标错)。"""
    return isinstance(result, str) and result.startswith(FAILED_PREFIXES)


def error_preview(result, limit: int = ERROR_PREVIEW) -> str:
    """失败时该展示的那段原文;成功一律给空串(界面据此决定要不要留错误行)。"""
    return str(result or "")[:limit] if is_failure(result) else ""
