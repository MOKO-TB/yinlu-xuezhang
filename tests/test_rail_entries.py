# -*- coding: utf-8 -*-
"""v0.9.39 p3:顶栏那 14 个面板入口搬进最左入口栏 —— 这一层守「搬家没搬丢东西」。

用户拿图 p3(Qoder 的左栏)/p4(ZCode)点名:「上面的工具堆太多了没有简洁性」。
拍板方向 = **面板入口移到左侧栏**。这一轮定死的规矩是:**id 一个不改、点击行为一行
不改,只换位置** —— 所以这里断的不是"新界面长什么样",而是四件最容易悄悄坏掉的事:

1. 14 颗按钮确实全在 `<nav class="rail">` 里,顶栏里一颗不剩(搬干净了,不是两处都有);
2. 每一颗仍然接得住**原来那个**动作(桥方法名 / 弹层 id / 那段点击绑定都还在)——
   搬成一颗点不动的死图标是这次最容易犯的错;
3. 带状态的格子仍有 `.cap` 那一行可写:JS 里谁去覆盖整颗按钮的 `textContent`,
   图标和标签会一起没(这是"图标 + 字"两行结构引入的新坑,原来单行按钮没这个问题);
4. 图标之外必须有字(「不是只给个图标猜」),而且这一栏在矮窗口里要能滚到所有入口
   —— 入口看不见就等于功能没了。

真点击的证据在 `scripts/e2e_ui_v0939_p3.py`(真 Chromium 逐个点过去);这里只守源码
文本,快、不需要浏览器,两边对的是**同一份 14 个的清单**(第 5 节把两边钉在一起)。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
E2E = (ROOT / "scripts" / "e2e_ui_v0939_p3.py").read_text(encoding="utf-8")

# 顺序 = 界面上从上到下的顺序(入口栏里就是这一份顺序)
# v0.9.39 搬进来 14 颗;v0.9.89 加过「体检」(v0.9.100 随体检面板一起删);
# v0.9.112 加三颗:🖥 终端、⏱ 自动化(用户看图 p5 点名要)、◀ 收起左侧栏(Ctrl+B)。
# v0.9.113:「收起左侧栏」按图 p1/p2 搬到**对话区左上角**(见下面 TOPBAR_ICON_IDS),
# 入口栏因此回到 16 颗 —— 它是唯一的"收起入口"家,两处都有一颗就会打架。
# 加一颗要同时改这里、下面两处计数、以及 scripts/e2e_ui_v0939_p3.py 那份真点击
# 清单 —— 这个测试就是为了让漏改当场红。
RAIL_IDS = ["btnMap", "btnSchedule", "btnMood", "btnPlan", "btnActivity",
            "btnSkills", "btnMemory", "btnUsage",
            "btnTerminal", "btnAutomate",       # v0.9.112:终端 / 自动化
            "btnMyData", "btnSandbox",
            "btnExport", "btnUndo", "btnCompact", "btnTheme"]

# 顶栏左上角那一颗(它不是面板入口,是"这一列要不要在"的开关,所以归顶栏)
TOPBAR_ICON_IDS = ["btnSideToggle"]
# 无边框窗口的自绘三颗:只在 win_state 说"这窗真没系统标题栏"时才出现
WIN_CTRL_IDS = ["winMin", "winMax", "winClose"]

# 每一颗**原来**去的地方(与 e2e_ui_v0939_p3.py 真点击断的是同一份口径):
# 弹层类写弹层 id,动作类写桥方法名 —— 都能在这份文件里追回真代码。
HANDLES = {
    "btnMap": "window.CampusMap",              # 地图:window.CampusMap.toggle(true)
    "btnSchedule": "scheduleOverlay",
    "btnMood": "moodOverlay",
    "btnPlan": "planOverlay",
    "btnActivity": "act-open",                 # 右侧「任务监控」栏:给 .app 加那一类
    "btnSkills": "skillsOverlay",
    "btnMemory": "consolidate_memory",
    "btnUsage": "usageOverlay",
    "btnMyData": "myDataOverlay",
    "btnSandbox": "set_sandbox",
    "btnExport": "export_session",
    "btnUndo": "api.undo()",
    "btnCompact": "compact_now",
    "btnTheme": "applyTheme",
    # v0.9.112 三颗新的:弹层类写弹层 id,收侧栏那颗写它翻的那个类名。
    "btnTerminal": "termOverlay",
    "btnAutomate": "autoOverlay",
    "btnSideToggle": "side-closed",
}

# 顶栏这轮之后**应该**只剩状态,不该再有导航
# "tok" 曾在列:v0.9.04 计数搬进任务面板后它成了永远空着的死元素,v0.9.65 删除
# (上轮前端审计的实锤发现)。将来顶栏若放新状态件,记得加回这份清单。
TOPBAR_STATUS_IDS = ["wsPill", "modelPill", "permPill"]
# v0.9.94:perm 从原生 <select id="perm"> 换成自定义浮层(深色下 option 白底白字,
# 见图 p3)—— 状态件还在顶栏,只是换了 id 与交互形态;老形态的 id 随之退役。


def _block(tag: str, opener: str) -> str:
    """取 `opener` 那段 HTML 直到它自己的闭合标签(按同名标签数嵌套,不含闭合标签本身)。

    不用 HTML parser:这份文件里既有模板字符串又有 SVG,任何解析器都会把"入口栏"和
    脚本里的字符串搅在一起;而这里要量的就是**源码文本**这一层。
    """
    start = HTML.index(opener)
    pos, depth = start, 0
    pat = re.compile(rf"<{tag}\b|</{tag}>")
    while True:
        m = pat.search(HTML, pos)
        assert m, f"找不到 </{tag}> 的闭合(起点:{opener})"
        if m.group().startswith("</"):
            depth -= 1
            if depth == 0:
                return HTML[start:m.start()]
        else:
            depth += 1
        pos = m.end()


@pytest.fixture(scope="module")
def rail() -> str:
    return _block("nav", '<nav class="rail"')


@pytest.fixture(scope="module")
def topbar() -> str:
    return _block("div", '<div class="topbar">')


def _css(rule: str) -> str:
    """取某条 CSS 规则的声明块(只看 `{...}` 里那几行)。"""
    m = re.search(re.escape(rule) + r"\{([^}]*)\}", HTML)
    assert m, f"CSS 里找不到 {rule}"
    return m.group(1)


# ---------------- 1. 搬干净了 ----------------

def test_rail_holds_every_entry_exactly_once(rail: str):
    for bid in RAIL_IDS:
        assert rail.count(f'id="{bid}"') == 1, f"入口栏里 {bid} 不止一个或根本没有"
        # 整份文件里也只能有一个:两处都留 = 同一个面板两个入口,状态会打架
        assert HTML.count(f'id="{bid}"') == 1, f"{bid} 在别处又出现了一次(搬没干净)"


def test_topbar_keeps_only_status_and_no_navigation(topbar: str, rail: str):
    for bid in RAIL_IDS:
        assert f'id="{bid}"' not in topbar, f"{bid} 还留在顶栏里(应只在入口栏)"
        assert f'id="{bid}"' in rail, f"{bid} 没进入口栏"
    for sid in TOPBAR_STATUS_IDS:
        assert f'id="{sid}"' in topbar, f"顶栏少了状态件 {sid}(这轮不动它)"
    # v0.9.113:顶栏多了「收起左侧栏」那一颗 + 无边框的三颗窗口控件。
    # 这两组都不是**面板入口**(一个管这一列在不在,三个管这个窗本身),
    # 所以入口仍然一颗不许进顶栏 —— 上限从 6 抬到 10,再多就是往顶栏塞导航。
    for bid in TOPBAR_ICON_IDS + WIN_CTRL_IDS:
        assert f'id="{bid}"' in topbar, f"顶栏少了 {bid}"
        assert f'id="{bid}"' not in rail, f"{bid} 搬完还留在入口栏(两处都会翻状态)"
    assert len(re.findall(r'id="([A-Za-z0-9_]+)"', topbar)) <= 10, \
        "顶栏控件数又涨回去了 —— 这轮要解决的正是「上面的工具堆太多」"


def test_rail_is_the_first_child_of_the_app_grid():
    after = HTML[HTML.index('<div class="app">'):]
    assert after.index('<nav class="rail"') < after.index('<aside class="sidebar"'), \
        "入口栏排在侧栏后面 = 视觉上不是最左那条"


# ---------------- 2. 每一颗仍然点得动 ----------------

@pytest.mark.parametrize("bid,handle", sorted(HANDLES.items()))
def test_entry_still_reaches_its_original_action(bid: str, handle: str):
    assert handle in HTML, f"{bid} 原来接的是 {handle!r},现在这份文件里找不到了"


@pytest.mark.parametrize("bid", sorted(RAIL_IDS))
def test_entry_has_a_live_click_binding(bid: str):
    """搬位置不改行为:每颗都还得有一条**真**绑定,而且只有一条(点一次只干一次事)。"""
    assert HTML.count(f'$("{bid}").addEventListener("click"') == 1, \
        f"{bid} 的点击绑定不是一条(丢了就是死图标,多了会连点两次)"


def test_the_moved_collapse_button_is_still_live_and_still_explained():
    """「收起栏」搬到对话区左上角:只剩图标没有字了,那 title/aria 必须补上这堂课。

    入口栏那条「图标之外必须有字」的规矩在这里换了一种守法 ——
    顶栏那一行要给工作区名与模型胶囊留位置,塞不下两个字,所以解释写在
    title(悬停)与 aria-label(读屏)里,并且图标本身要跟着实际状态翻明暗。
    """
    assert HTML.count('$("btnSideToggle").addEventListener("click"') == 1
    m = re.search(r'<button class="tb-ico"[^>]*id="btnSideToggle"[^>]*>', HTML)
    assert m, "对话区左上角没有那颗收起栏按钮"
    assert 'title="' in m.group(0) and 'aria-label="' in m.group(0), "只剩一个图标没人猜得中"
    assert 'b.classList.toggle("on", !!closed)' in HTML, "按钮不跟着实际收没收起翻"
    assert 'id="sideCap"' not in HTML, "旧的那份文字标签还在,状态写两处迟早对不上"


def test_overlay_entries_target_their_own_dialog():
    """七个弹层入口各自的目标弹层仍在,且仍是被 `.classList.add("show")` 打开。"""
    for ov in ["scheduleOverlay", "moodOverlay", "planOverlay",
               "skillsOverlay", "usageOverlay", "myDataOverlay"]:
        assert HTML.count(f'id="{ov}"') == 1, ov
        assert f'$("{ov}").classList.add("show")' in HTML, f"{ov} 不再被 show 打开"


# ---------------- 3. 两行结构的可写性 ----------------

def test_every_entry_has_one_cap_line(rail: str):
    """"图标 + 字"两行结构:每颗按钮都得留一个 `.cap` 给真状态写。"""
    buttons = re.findall(r"<button\b[^>]*>(.*?)</button>", rail, flags=re.S)
    assert len(buttons) == len(RAIL_IDS) == 16, "入口栏的格子数与这份清单不一致"
    for bid, body in zip(RAIL_IDS, buttons):
        assert body.count('class="cap"') == 1, f"{bid} 的标签行不是一份,状态没地方写"


def test_state_writers_only_touch_the_cap_line():
    """沙箱/记忆这两处会写动态文案:只准写 `.cap` —— 写整颗按钮会把图标一起抹掉。"""
    assert ('btn.querySelector(".cap").textContent = '
            'sandboxInfo.enabled ? "沙箱开" : "沙箱关";') in HTML
    assert ('btn.querySelector(".cap").textContent = '
            'total ? `记忆 ${total}` : "记忆";') in HTML
    for bid in ["btnSandbox", "btnMemory", "btnTheme", "btnActivity", "btnPlan"]:
        assert f'$("{bid}").textContent' not in HTML, f"{bid} 被整颗覆盖文字了"
    # 主题那一格写的也是真状态:切到哪一档,字就写着"下一步能切去的那一档"
    assert '$("themeCap").textContent = dark ? "浅色" : "深色"' in HTML
    assert 'btn.classList.toggle("on", !!sandboxInfo.enabled)' in HTML, \
        "沙箱那颗不再有'开着的'样子(字对了,颜色不跟着变)"


# ---------------- 4. 看得见、点得到 ----------------

@pytest.mark.parametrize("bid", sorted(RAIL_IDS))
def test_every_entry_is_labeled_in_words_not_only_an_icon(rail: str, bid: str):
    """「不是只给个图标猜」:每颗都要有 title(悬停解释)+ 一眼看得见的字。"""
    m = re.search(rf'<button\b[^>]*id="{bid}"[^>]*>(.*?)</button>', rail, flags=re.S)
    assert m, f"入口栏里没有 {bid}"
    assert 'title="' in m.group(0), f"{bid} 没有 title,只剩一个图标"
    assert re.search(r'<span class="cap"[^>]*>[^<\s][^<]*</span>', m.group(1)), \
        f"{bid} 的标签行是空的(图标没人猜得中)"


def test_rail_is_scrollable_when_the_window_is_short():
    """入口栏在矮窗口里放不下:能滚是唯一可接受的解(裁掉 = 入口没了)。"""
    assert "overflow-y:auto" in _css(".rail")


def test_rail_track_is_wide_enough_for_its_tiles():
    """那一列必须装得下格子(含描边与内边距),否则图标会被横向截断。

    按最坏情况算(这份页面的 `.rail button` 没写 box-sizing,所以 padding/border
    是加在 width 之外的)。
    """
    track = int(re.search(r"\.app\{[^}]*grid-template-columns:\s*(\d+)px", HTML).group(1))
    decl = _css(".rail button")
    cell = int(re.search(r"width:\s*(\d+)px", decl).group(1))
    border = int(re.search(r"border:\s*(\d+)px", decl).group(1))
    pad_x = int(re.search(r"padding:\s*\d+px\s+(\d+)px", decl).group(1))
    assert cell + 2 * (border + pad_x) <= track, \
        f"格子 {cell}+2*({border}+{pad_x}) 塞不进 {track}px 那一列"


def test_opening_the_monitor_column_still_adds_exactly_one_track():
    """右侧那栏占的是网格最后一列;p3 加的是最左一列,所以「多一列」这件事不能变。"""
    base = re.search(r"\.app\{[^}]*grid-template-columns:([^;]+);", HTML).group(1)
    opened = re.search(r"\.app\.act-open\{[^}]*grid-template-columns:([^;]+);", HTML).group(1)
    n_base = len(base.split())
    assert len(opened.split()) == n_base + 1, f"关 {n_base} 列 → 开应 {n_base + 1} 列"
    # 而且新那一列排在最后(入口栏在最左不能被它挤掉)
    assert opened.split()[-1] != base.split()[0], "act-open 改的是首列?入口栏会被挤窄"


def test_sandbox_entry_is_hidden_until_the_bridge_reports_it():
    """沙箱那颗只有后端说"这台有沙箱"才出现:默认必须藏,不许亮一颗假开关。"""
    assert re.search(r'<button id="btnSandbox"[^>]*style="display:none"', HTML), \
        "沙箱入口不再默认隐藏(没接本机桥的机器上会多一颗死开关)"
    assert 'btn.style.display = "none"' in HTML and 'btn.style.display = "block"' in HTML


# ---------------- 5. 两场测试对同一份清单 ----------------

@pytest.mark.parametrize("bid", sorted(RAIL_IDS))
def test_the_browser_round_actually_clicks_this_entry(bid: str):
    """源码断言只证明"结构在";真点击在 e2e 那一场。两边必须是同一份清单。"""
    assert f"#{bid}" in E2E, f"e2e_ui_v0939_p3.py 没有真点过 {bid}"


def test_user_manual_documents_the_rail(rail: str):
    """同一份新界面规矩还有下半句:**装好的包里那份《使用说明.txt》也要跟着改**。

    它是 `scripts/build_exe.py` 里的一个字面量,同学照着它找按钮。所以这一轮之后它
    必须①写了左栏这事,②**不再把搬走的入口指到顶栏** —— 指错了不会有任何报错,
    只有照着点的人点不到。顶栏确实还留着的那几处(📁 工作区、模型管理)不算漂,
    所以这里只看「顶栏「……」」这种指路的写法,不看句子里提到"数据目录"这类词。
    """
    src = (ROOT / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    assert "入口栏" in src, "《使用说明》里没写这条竖栏"
    caps = [c.strip() for c in re.findall(
        r'<span class="cap"[^>]*>([^<]+)</span>', rail)]
    assert len(caps) == 16, f"从界面取到的标签数不是 16:{caps}"
    for label in re.findall(r"顶栏「([^」]*)」", src):
        hit = [c for c in caps if c in label]
        assert not hit, f"《使用说明》还把「{label}」指到顶栏,它已经在入口栏里了"
    # 「收起栏」这一颗 v0.9.113 起**就在顶栏左上角**:《使用说明》不许再把它指回入口栏
    for label in re.findall(r"入口栏「([^」]*)」", src):
        assert "收起" not in label, f"《使用说明》还说「{label}」在入口栏"


def test_handoff_documents_the_move():
    """新界面规矩:改了界面要写进交接文档(§6 那条 R 项)。"""
    doc = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
    assert "入口栏" in doc, "HANDOFF 里找不到这轮搬家的记录"
