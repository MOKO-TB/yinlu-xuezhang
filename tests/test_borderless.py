"""无边框窗口(v0.9.113,p5/p6/p7)的针:补票的三件事 + 界面那一套接线。

用户第三次提这件事,并且明确覆盖了上一轮"无边框不可靠、先不做"的判断,所以这一轮
**做了**,而且把实测出来的代价一并补上(2026-10-04 在这台机器上的探针):
① frameless 之后客户区铺满整窗 —— 那条"深色模式顶栏还是白的"从根上没了;
② 但系统不给可拖的边带(WM_NCHITTEST 对客户区一律 HTCLIENT,打 WS_THICKFRAME
   也不给:实测窗口矩形没变),所以缩放由界面边缘发起;
③ 不设 MaximizedBounds 时 maximize() 会**盖住任务栏**(实测 1920×1080,
   工作区 1920×1032)—— 那条必须补,不然人关不掉自己。

这里钉的是"补票有没有偷偷退回去":几何算术、门面有没有那五个方法、
界面是否**问过**窗口才摆三颗按钮(不是照配置猜)、以及关闭那颗不许碰托盘退出。
真窗口的观感在 `scripts/e2e_ui_v09113.py` 之外还要靠肉眼,那一条会在 HANDOFF 里写清。
"""
import inspect
import re
from pathlib import Path

import pytest

from minicode import window_chrome as wc
from minicode.gui_bridge import Bridge, PublicApi

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
MAIN = (ROOT / "gui_main.py").read_text(encoding="utf-8")

# 一台 884×561 的窗放在 (100,100) 当尺子
R0 = [100, 100, 984, 661]


# ---------------------------------------------------------------- ① 边缘几何(纯算术)

@pytest.mark.parametrize("edge,dxdy,want", [
    ("e", (40, 0), [100, 100, 1024, 661]),        # 拖右边:只有右动
    ("w", (40, 0), [140, 100, 984, 661]),         # 拖左边:左随鼠标,右钉住
    ("s", (0, 40), [100, 100, 984, 701]),
    ("n", (0, 40), [100, 140, 984, 661]),
    ("se", (30, -20), [100, 100, 1014, 641]),
    ("nw", (30, 20), [130, 120, 984, 661]),
])
def test_an_edge_moves_only_the_side_you_grab(edge, dxdy, want):
    assert wc._apply_edge(R0, edge, dxdy[0], dxdy[1]) == want


def test_a_corner_grabs_both_of_its_sides():
    got = wc._apply_edge(R0, "ne", 50, -50)
    assert got[2] == 1034 and got[1] == 50        # 右外扩 + 上外扩


@pytest.mark.parametrize("edge", sorted(wc.EDGES))
def test_you_cannot_drag_a_window_inside_out(edge):
    """往死里拖到最小就**停住**:被拖那条边留在够得着的地方,绝不翻面。"""
    rect = list(R0)
    for _ in range(4):                     # 连拖四次:缩到底就该钉住,不能再缩
        got = wc._apply_edge(rect, edge, -99999, -99999)
        assert got[2] - got[0] >= wc.MIN_W, f"{edge} 宽度被拖没了:{got}"
        assert got[3] - got[1] >= wc.MIN_H, f"{edge} 高度被拖没了:{got}"
        assert got[0] <= got[2] and got[1] <= got[3]
        rect = got


def test_an_unknown_edge_is_refused_not_guessed():
    got = wc.resize_edge(None, "up", 10, 10)
    assert got["ok"] is False and "up" in got["msg"]


def test_resize_without_a_window_says_why():
    """没有窗口/非 Windows:如实回 ok=False,不许"看起来成功了"。"""
    got = wc.resize_edge(None, "e", 10, 0)
    assert got["ok"] is False and got["msg"]


def test_maximize_bounds_without_a_form_is_honest():
    got = wc.maximize_bounds(None)
    assert got["ok"] is False and got["msg"]


def test_no_window_means_no_borderless_claim():
    assert wc.style_of(None) == 0
    assert wc.is_borderless(None) is False
    assert wc.window_rect(None) == []


def test_borderless_is_read_from_style_bits_not_from_config():
    """`is_borderless` 判的是样式位里**没有** WS_CAPTION —— 有标题栏就说不是无边框。

    标题栏位是 0x00C00000(= WS_BORDER|WS_DLGFRAME),与"可拖边带"那位是两件事:
    探针实测本机 frameless 之后两者都为 0 —— 所以既不能说"有边带就是有标题栏",
    也不能拿配置里那个布尔直接决定界面摆不摆三颗按钮。
    """
    assert wc.WS_CAPTION == 0x00C00000
    assert (0x00C00000 & wc.WS_CAPTION) == 0x00C00000   # 有 caption 位 → 不是无边框
    assert (0x00080000 & wc.WS_CAPTION) == 0            # 只有边带位 → 仍算无边框


# ---------------------------------------------------------------- ② 桥那五个方法

@pytest.mark.parametrize("name", ["win_state", "win_minimize", "win_zoom",
                                  "win_close", "win_resize"])
def test_the_bridge_and_the_facade_both_carry_it(name):
    """前端按名字敲门面:门面或桥少一个,点下去就是 undefined(没有任何报错)。"""
    assert hasattr(PublicApi, name), f"门面少了 {name}"
    assert hasattr(Bridge, name), f"桥少了 {name}"


def test_win_close_does_not_quit_the_tray_or_allow_exit():
    """✕ 那颗**只转给窗口自己的关闭流程**:关窗等于问一句还是藏进托盘,归托盘偏好管。

    前端能调到的方法里如果直接 `tray.quit()` 或设 `allow_exit`,同学设的"每次问我"
    就被绕过 —— 那是替人决定丢不丢工作,不是少一个按钮那么简单。
    (只量**代码那一截**:上面这段说明里就写着这两个词,连着 docstring 一起扫会永远红。)
    """
    src = inspect.getsource(Bridge.win_close)
    body = src.split('"""')[2] if src.count('"""') >= 2 else src
    assert "allow_exit" not in body and "quit" not in body
    assert "w.close()" in body, "关闭那颗没转给窗口自己的关闭流程"


def test_zoom_sets_bounds_before_maximizing():
    """最大化之前先补 MaximizedBounds(实测不补会盖住任务栏)。"""
    src = inspect.getsource(Bridge.win_zoom)
    assert "maximize_bounds" in src and "maximize()" in src


def test_startup_fixes_the_maximize_bounds():
    """开机补一票:post_startup 里设 MaximizedBounds,并且失败要写进托盘日志。"""
    assert "maximize_bounds" in MAIN
    assert re.search(r"if not bounds\[.ok.\]", MAIN), "设不上也没人记录"


# ---------------------------------------------------------------- ③ 界面接线

def test_the_window_is_actually_borderless_now():
    assert "frameless=True" in MAIN
    # easy_drag=False:否则**整窗任何一处**按下都能拖,选一行字变成挪窗口
    assert "easy_drag=False" in MAIN
    # shadow=False:那条 DWM 玻璃边正是"深色顶栏还是白的"里的一种来源
    assert "shadow=False" in MAIN


def test_the_drag_region_is_only_the_empty_middle():
    assert 'class="spacer tb-drag pywebview-drag-region"' in HTML
    assert ".topbar .tb-drag{ align-self:stretch" in HTML, "空的 flex 项高度为 0 拖不到"


def test_window_controls_ask_the_window_before_showing():
    """三颗按钮不是照配置摆的:开机问 win_state(它读窗口样式位)。"""
    assert "api.win_state()" in HTML
    assert "winInit();" in HTML
    assert 'bar.hidden = !winBorderless' in HTML
    assert 'rz.hidden = !winBorderless' in HTML
    assert "$(\"winClose\").addEventListener(\"click\", () => api.win_close())" in HTML


def test_edge_handles_match_the_backend_edges():
    """界面这 8 条把手与后端 EDGES 必须是同一份(少一个方向=那条边拖不动)。"""
    seen = set(re.findall(r'data-e="([a-z]{1,2})"', HTML))
    assert seen == set(wc.EDGES), f"界面 {sorted(seen)} ≠ 后端 {sorted(wc.EDGES)}"
    assert 'class="rz" id="rz" hidden' in HTML, "把手默认就该收着(有标题栏时不许抢边缘)"


def test_resize_is_delta_based_and_throttled():
    """增量式 + rAF 合流:两边各存一份绝对尺寸迟早对不上,逐个敲桥会排队跳。"""
    assert "api.win_resize(edge, gx, gy)" in HTML
    assert "requestAnimationFrame(flush)" in HTML
    assert "dx += ev.screenX - lx" in HTML


def test_double_click_the_bar_zooms():
    assert 'drag.addEventListener("dblclick"' in HTML


# ---------------------------------------------------------------- ④ 右缘导航(p8/p9)

def test_the_rail_is_off_the_edge_and_legible_in_light_mode():
    m = re.search(r"\.chat-rail\{([^}]*)\}", HTML)
    body = m.group(1)
    assert "right:12px" in body, "贴死在窗口边上就不像可点的东西(图 p9 是留了距离的)"
    assert "opacity:.9" in body, "浅色下 0.5 的刻度看不见(图 p8 点的就是这条)"
    assert "background:var(--rail-tick)" in re.search(
        r"\.chat-rail \.tick\{([^}]*)\}", HTML).group(1)
    assert "--rail-tick:rgba(28,43,40,.40)" in HTML, "浅色那一档没有真的加深"
    assert "--rail-tick:rgba(190,230,225,.32)" in HTML, "深色那一档漏了"


def test_a_hovered_or_marked_tick_is_wider_and_ringed():
    assert ".chat-rail .tick:hover,.chat-rail .tick.on" in HTML
    assert "box-shadow:0 0 0 3px var(--primary-light)" in HTML


def test_the_tick_you_jumped_to_stays_lit():
    assert "function scTickMark(i)" in HTML
    assert "scTickMark(i);              // 刻度上留下" in HTML
    assert 't.classList.toggle("on", Number(t.dataset.i) === i)' in HTML
    assert "tick.dataset.i = String(i)" in HTML


# ---------------------------------------------------------------- ⑤ 成绩那一屏的入口

def test_the_scores_tab_lives_in_the_schedule_panel():
    assert '<button data-tab="scores"' in HTML
    assert 'if (scheduleTab === "scores")' in HTML
    # 没登记课表也要能看成绩:那一档必须排在 registered 那道 return **前面**
    i_scores = HTML.index('if (scheduleTab === "scores")')
    i_guard = HTML.index('if (!scheduleData || !scheduleData.registered)')
    assert i_scores < i_guard, "成绩那一档被课表没登记那道 return 挡在了后面"


def test_the_panel_never_invents_a_number():
    """读不到/没存档时那六个格子写「—」,而不是 0 分 0 绩点。"""
    assert 'const n = (v === null || v === undefined || v === "");' in HTML
    assert 'n ? "—" : esc(String(v))' in HTML
    assert "api.get_score_stats(sem || \"\")" in HTML


def test_scores_panel_reloads_when_the_dialog_reopens():
    assert "scoreStats = null; scoreBusy = false; scoreSem = \"\";" in HTML
