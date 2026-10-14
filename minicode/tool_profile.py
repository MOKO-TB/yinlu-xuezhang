"""工具面的**三层投影**(v0.9.173):常驻 / 索引 / 可加载。

**要解决的问题**:模型每一轮拿到的工具定义是注册表原样 —— 71 只、两万多 token,
每一轮都付一遍,而其中"校园生活/地图/馆藏"那一族多数回合根本用不到。固定开销
里最大的一块就在这里。

**三条铁律(与 `web_profile` / `agent_profiles` 同源,这里不另立一套):**

1. **注册表一个字都不动。** 分层只发生在"给模型看的**投影**"上 ——
   `build_default_registry()` 返回的集合与顺序不变,`task` / 权限门 / 子代理
   拿到的仍是完整注册表。删工具与不投影是两件事,前者会让"它明明有这个能力"
   变成真的没有。
2. **默认关。** `config.json` 的 `tool_loading` 默认 `"all"` = **逐字回到今天**。
   理由写在这里,免得下一轮有人"顺手打开":这一改动的核心主张(模型会主动去要它
   需要的工具)**本机验证不了** —— 内置端点被网关拒着(401),没有真端点就数不出
   "该用领域工具时它有没有先 tool_search"。拿"我觉得模型会要"去换核心循环的行为,
   正是这个仓库列在禁忌里的东西。要打开请先用真端点做那一件事:同一个任务,
   `all` 与 `lazy` 各跑 20 遍,数命中。
3. **加载不可逆(本会话内)。** 一旦被加载就不再收回 —— 收回等于"我明明用过它,
   下一轮它却不见了",模型会把"工具没了"误读成"这个能力不存在"。会话结束自然归零。

**索引层是这一整套能成立的前提**:模型不会去要一个它不知道存在的东西 ——
索引段每轮都在(21 只约 500 t),换掉的是那 21 只完整 schema 的约 8,400 t。
"""
from __future__ import annotations

from minicode.tools.base import Tool

# ---------------------------------------------------------------- 三层名单
#
# **可加载层** = 校园生活 / 地图 / 馆藏 / 提醒 / 出图取字 那一族:它们服务的是
# "校园助手"这个人格,**不是每一次编码回合都用得到**;通用干活工具(文件/命令/
# 符号/测试/浏览器/记忆/计划/递话)一律**留在常驻**。
#
# 两处刻意的取舍,写清楚免得被下一轮"顺手挪两只":
# - **浏览器那 8 只留在常驻**(约 1,700 t):它们服务的是"打开页面看一眼再改"这类
#   真编码工作,不是校园生活;挪下去省得多一点,但那是改行为面的取舍,要单独一卷。
# - **`view_image` 留在常驻**:它是"核对我自己刚画出来的东西",与出图同一条工作流
#   —— 出图那只下沉了,看图这只留着,是因为上游(render_chart/browser_screenshot)
#   也在常驻里,只留上游会让"画完看不见"。
LOADABLE: tuple[str, ...] = (
    # 校园日历 / 课表 / 成绩 / 绩点
    "campus_calendar", "calendar_edit", "schedule", "gpa_calc", "scores",
    "campus_notices",
    # 提醒与个人生活
    "reminder", "desktop_notify", "mood",
    # 地图
    "map_find_place", "map_show_list", "map_highlight_building",
    "map_drop_pin", "map_clear",
    # 馆藏与知识候选
    "library_search", "library_guide", "kb_suggest",
    # 出图与取字、天气、日历文件
    "render_chart", "generate_image", "ocr_image", "get_weather", "export_ics",
)

# 索引行:一行 = 名字 + 它自己 description 的第一句。**不另写一份摘要** ——
# 第二份文案迟早与真的那份对不上,而"索引说它会干 A、它其实干 B"是最坏的一种坏。
INDEX_CHARS = 96


def is_loadable(name: str) -> bool:
    return name in LOADABLE


def first_sentence(text: str, limit: int = INDEX_CHARS) -> str:
    """取描述的第一句(换行 / 句号 / 分号断句,**断句符一起留下**),超长截断并写明截过。

    截了不说 = 索引里少半句话而看着像完整的,模型会按那半句猜能力边界。
    """
    # 换行先切:描述里有的是多行写的,第一行就是它给自己定的那一句。
    raw = str(text or "").split("\n", 1)[0]
    body = " ".join(raw.split())
    cut = len(body)
    for mark in ("。", ";", "；"):
        got = body.find(mark)
        if got >= 0:
            cut = min(cut, got + 1)
    head = body[:cut].strip()
    if len(head) > limit:
        return head[:limit] + "…(索引行截断)"
    return head


def index_line(tool: Tool) -> str:
    """一只可加载工具的索引行。"""
    return f"- {tool.name}: {first_sentence(tool.description)}"


def split(tools: list[Tool]) -> "tuple[list[Tool], list[Tool]]":
    """把注册表切成交给模型的(常驻)与只进索引的(可加载)。"""
    resident = [t for t in tools if not is_loadable(t.name)]
    loadable = [t for t in tools if is_loadable(t.name)]
    return resident, loadable


def index_text(tools: list[Tool]) -> str:
    """系统提示词里的索引段(没有可加载工具时回空串,一个字都不加)。

    **必须自己交代怎么把它们调出来** —— 只列一张"存在但拿不到"的清单,
    比不列更糟:模型会以为那些工具坏了。
    """
    _resident, loadable = split(tools)
    if not loadable:
        return ""
    lines = [index_line(t) for t in loadable]
    return ("## 不在本轮工具面里的能力(按需取回)\n\n"
            "下面这些工具**默认不放进每轮的工具定义里**(省固定开销)。它们是真能用"
            "的 —— 用 `tool_search` 把它取回来,取回之后本会话一直有效:\n\n"
            + "\n".join(lines)
            + "\n\n要哪一只就 `tool_search(query=\"它干什么\")`,或直接 "
              "`tool_search(query=\"\")` 看全部并取回。")


def projection(tools: list[Tool], loaded: "set[str] | None",
               mode: str = "lazy") -> list[Tool]:
    """这一次请求真发给模型的工具清单。`mode="all"` = 原样(与今天逐字一致)。"""
    if str(mode or "all").strip().lower() != "lazy":
        return tools
    got = loaded or set()
    return [t for t in tools if (not is_loadable(t.name)) or t.name in got]


def search(tools: list[Tool], query: str) -> "tuple[list[Tool], str]":
    """在**可加载层**里找工具。返回(命中的工具, 给模型的那段话)。

    匹配口径只说三条、不引入第二套(与仓库里别的匹配同一条根):查询为空 =
    列出全部;否则按**查询里的词**在该工具的名字与描述里逐个找;一个词都没命中
    就如实说没命中,并把清单摆出来 —— **不许猜一只塞给它**(猜错的表现是模型
    拿着不对的工具硬用,比"没有"难查得多)。
    """
    _resident, loadable = split(tools)
    want = " ".join(str(query or "").split())
    if not loadable:
        return [], "这一份工具面里没有可加载的工具(分层没开,或者全都已在本轮的工具定义里)。"
    if not want:
        return list(loadable), (f"可加载的工具共 {len(loadable)} 只,已全部取回本会话:\n"
                                + "\n".join(index_line(t) for t in loadable))
    terms = [w for w in want.replace(",", " ").replace("、", " ").split() if w]
    hits, why = [], []
    for t in loadable:
        blob = (t.name + " " + str(t.description or "")).lower()
        score = sum(1 for w in terms if w.lower() in blob)
        if score:
            hits.append((score, t))
        else:
            why.append(t)
    if not hits:
        return [], (f"没有可加载的工具匹配「{want}」。现在可加载的有这 "
                    f"{len(loadable)} 只:\n"
                    + "\n".join(index_line(t) for t in loadable)
                    + "\n\n要用哪一只就照它的名字再搜一次(或者空查询全部取回)。")
    hits.sort(key=lambda p: (-p[0], p[1].name))
    picked = [t for _s, t in hits]
    return picked, (f"按「{want}」取回 {len(picked)} 只(名字与描述里带这些词的):\n"
                    + "\n".join(f"- {t.name}: {first_sentence(t.description, 200)}"
                                for t in picked)
                    + "\n\n取回的这几只从**下一次请求**起就在工具定义里,本会话一直有效。"
                    + (f"\n(另外还有 {len(why)} 只没带上这些词,要的话照名字再搜。)"
                       if why else ""))


def make_handler(build_all):
    """构造 `tool_search` 的 handler。

    **Tool 字面量留在 `catalog_knowledge.py`**(注册表那道"每只工具写在哪份目录里"
    的闸按文件名扫 `Tool(` 字面量,造在这里会被判成"目录与顺序对不上")。
    这里只出逻辑。

    `build_all` 是**现取现用**的那份注册表(一个无参可调用):注册表会被热重载与
    对外形态就地换掉(`refresh_registry` 那两处),把列表在装配时冻住,表现是
    "新加的工具搜不到";而 `ctx.tool_catalog`(Agent 写上去的那个可调用)优先 ——
    它读的永远是**此刻**这一份。
    """

    def handler(ctx, query="") -> str:
        live = getattr(ctx, "tool_catalog", None)
        tools = list((live() if callable(live) else live) or build_all() or [])
        loaded = getattr(ctx, "tool_loaded", None)
        picked, said = search(tools, query)
        if not picked:
            return said
        if isinstance(loaded, set):
            for t in picked:
                loaded.add(t.name)
            return said
        # 没有会话级"已加载"集合 = 这一次取回没有落脚点。**如实说**,
        # 不许回一句"已取回"而其实下一次请求里仍然没有它们。
        return said + ("\n⚠ 这一条会话没有加载集合(不在会话里跑?)—— "
                       "上面这些工具**没有**被加进工具面。")

    return handler


SEARCH_DESCRIPTION = (
    "按需取回工具:有些工具默认不进每轮的工具定义(省固定开销),这一只把它们"
    "取回来 —— 取回之后**本会话一直有效**。query 写它要干什么(如「课表」"
    "「看场馆」「生成图表」),或传空串看全部并一次取回。系统提示词里那份"
    "「不在本轮工具面里的能力」列出的是**真有**的工具,拿不定的先搜一次再说话;"
    "**不许因为「没看到这只工具」就说自己做不到**。"
)
