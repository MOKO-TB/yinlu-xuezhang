"""教务向导「接管那张页」这一步的针(v0.9.112 修的那条 P0)。

用户报的是:已经登录上教务系统了,点「② 读取成绩」还是读不到,红字写着
`BrowserError: 还没有打开的页面,先 browser_open 或用 adopt_page 接管一页。`

根因不在教务系统、也不在登录态 —— 在**会话按线程分格**:桥的每一次 js_api 调用
跑在自己的线程上,而 `browser._session_key()` 是 `browser:<线程 ident>`。
①「打开登录页」那条调用留下的会话根本不属于②这条线程,于是②直接问
"我这张页"永远拿到 None。课表那条路每一步都先 `_anchor()`(接管)所以没暴露,
成绩这条路按设计**不能导航**(替同学跳回登录页会把他刚查出来的那张表丢掉),
而"不能导航"被写成了"什么都不做" —— 于是每一步都撞这句错。

所以这里钉的是:①读之前一定先接管;②接管不到要说"页没找到"而不是"没有成绩";
③按第①步真正开过的站点找(学校换域名也认得回来);④**attach 模式绝不兜底接管
"任意一页"** —— 那会把用户正在看的别的网站当成教务页来读,读错东西比读不到更糟。
"""
import json
from pathlib import Path

import pytest

from minicode.scores_import import ScoresFlow
from minicode.schedule_import import HOST_HINT, LOGIN_URL, ImportFlow

ROOT = Path(__file__).resolve().parent.parent

DUMP = {"tables": [{"id": "DataGrid1", "rows": [
    ["课程", "成绩", "学分", "学年"],
    ["高等数学", "89", "4", "2025-2026-1"],
    ["大学物理", "76", "3", "2025-2026-1"],
]}]}


class RecBrowser:
    """记账式假浏览器:adopt 收到过哪些 hint、有没有人去问页面上的表。"""

    MAX_WAIT_MS = 1
    FRAME_MAX_CHARS = 1000
    BrowserUnavailable = type("BrowserUnavailable", (Exception,), {})

    def __init__(self, adopt_hits=None, dump=None):
        self.hints = []
        self.adopt_hits = adopt_hits or {}       # hint → 命中的页(None = 找不到)
        self.dump = dump
        self.reads = 0
        self.opened = []

    def last_note(self):
        # 真模块有这一只(复用/换配置那句话从这里出来);替身缺它,第①步那句
        # 回话里新加的一段就等于没测。
        return ""

    def ensure_visible(self):
        return True

    def open_page(self, url, *a, **k):
        self.opened.append(url)
        return {"url": url, "title": "教务"}

    def adopt_page(self, url_like="", **k):
        self.hints.append(url_like)
        return self.adopt_hits.get(url_like)

    def read_frames(self, script, url_like=None):
        self.reads += 1
        import minicode.site_parse as sp
        if self.dump is not None and script is sp.TABLE_JS:
            return [{"value": json.dumps(self.dump, ensure_ascii=False)}]
        if self.dump is not None and script is sp.TEXT_JS:
            return [{"value": "高等数学 88 5"}]
        return []

    def evaluate(self, *a, **k):
        return "http://x/grade"


PAGE = {"url": "http://x/grade", "title": "成绩查询"}


def test_read_adopts_before_it_touches_the_page():
    fb = RecBrowser(adopt_hits={HOST_HINT: PAGE}, dump=DUMP)
    got = ScoresFlow(browser_mod=fb).read()
    assert fb.hints and fb.reads, "先接管、后取材 —— 顺序就是这条 bug 的全部"
    assert got["ok"] and got["stage"] == "ready" and got["summary"]["entries"] == 2


def test_a_missing_page_says_so_instead_of_reading_nothing():
    """接管不到时**根本不该去问页面** —— 一问就是那句 BrowserError,
    而把它 catch 成"没有成绩"是一句假话(同学的表明明在屏幕上)。"""
    fb = RecBrowser(adopt_hits={})
    got = ScoresFlow(browser_mod=fb).read()
    assert not got["ok"] and got["stage"] == "no_page"
    assert fb.reads == 0, "没接管到页还去读 = 又撞一次那句错"
    assert "没找到还开着的教务页" in got["msg"] and "成绩" in got["msg"]


def test_the_host_opened_in_step_one_is_the_first_thing_we_look_for():
    """学校换域名时内置的 HOST_HINT 会落空:第①步真正开过的那个站点优先。"""
    fb = RecBrowser(adopt_hits={"jwmust.gxun.edu.cn": PAGE}, dump=DUMP)
    flow = ScoresFlow(browser_mod=fb)
    flow.open_login("https://jwmust.gxun.edu.cn/login")
    got = flow.read()
    assert fb.hints[0] == "jwmust.gxun.edu.cn", f"找页顺序错了:{fb.hints}"
    assert got["ok"]


def test_attach_mode_never_grabs_an_arbitrary_tab():
    """attach 接的是用户日常浏览器 —— 按"任意一页"接管可能把他正在看的网站
    当成教务页来读。读错东西比读不到更糟,所以那条兜底只在 launch 允许。"""
    fb = RecBrowser(adopt_hits={})
    flow = ScoresFlow(mode="attach", browser_mod=fb)
    assert flow.anchor_existing() is None
    assert "" not in fb.hints, f"attach 模式不许兜底接管任意一页:{fb.hints}"


def test_launch_mode_may_take_the_only_page_our_instance_has():
    """launch 那个实例是向导自己起的独立 profile,里面不该有别人的标签页 ——
    这时候"只有一页就用它"是救回导入的合理兜底(且排在按站点找之后)。"""
    fb = RecBrowser(adopt_hits={"": PAGE}, dump=DUMP)
    got = ScoresFlow(mode="launch", browser_mod=fb).read()
    assert fb.hints[-1] == "" and fb.hints[0] == HOST_HINT, f"顺序:{fb.hints}"
    assert got["ok"]


def test_the_model_fallback_anchors_too():
    """「让学长用模型整理一次」走的是同一张页 —— 页没接管到就发文字给模型,
    等于发一段空文字过去,换回一份编出来的成绩单。"""
    called = []
    fb = RecBrowser(adopt_hits={})
    got = ScoresFlow(browser_mod=fb).read_with_model(lambda *_: called.append(1) or "")
    assert not got["ok"] and got["stage"] == "no_page"
    assert not called and fb.reads == 0


def test_the_schedule_flow_still_opens_a_page_when_nothing_can_be_adopted():
    """课表那条路的"接管不到就自己开一张"是既有行为(它下一步本来就要导航去
    学期课表地址),这条针钉的是"修成绩没把课表那条路改窄"。"""
    fb = RecBrowser(adopt_hits={})
    flow = ImportFlow(browser_mod=fb)
    flow._anchor()
    assert LOGIN_URL in fb.opened


@pytest.mark.parametrize("needle", [
    "anchor_existing", "_anchor_hints", "no_page", "_last_host",
])
def test_the_bridge_side_names_are_the_ones_the_frontend_calls(needle):
    """前端按 stage=no_page 换按钮文案,后端这几个名字漂了就是"界面还在等一个不会来的值"。"""
    src = (ROOT / "minicode" / "schedule_import.py").read_text(encoding="utf-8")
    assert needle in src


def test_the_wizard_offers_to_reopen_the_window_not_to_retry_forever():
    """界面上那条:页没了还摆一个「再读一次」,是引导人再点一次同样的失败。"""
    fe = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'stage === "no_page"' in fe
    assert "① 重新打开登录页" in fe
    assert "jwOpenLogin() : jwRead(false)" in fe
    # 页没接管到时也不给「让学长用模型整理一次」—— 没东西可整理
    assert 'r.stage !== "no_page"' in fe
