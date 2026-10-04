"""计划核对单(v0.9.114,用户点名的新功能①)。

`plan` 写完不再只是"回一段文字请你自己去确认":桌面版当场弹**整张单子**,
出口三个 —— 批准 / 改一条 / 重排。机制与 `ask_user` 分家是用户原话
("那是问路,这是批单"),所以这里钉的四件事都是"这道门有没有真的挡住人":
① 没接卡的一侧(命令行/网页版)照旧放行,闸门不许卡住不需要它的人;
② 等不到回答按**未确认**收口 —— 绝不生成一个"用户批准了";
③ 「改一条」写回 plan.md 的是**改后的那一句**,模型收到的是新单子;
④ 三颗出口各回各的话,句子由后端组(前端拼一份就是两份真相)。
"""
from pathlib import Path

import pytest

from minicode import paths, planning


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


class Ctx:
    def __init__(self, workspace, ui=None):
        self.workspace = Path(workspace)
        self.ui = ui
        self.root = self.workspace / planning.PLAN_REL.parent
        self.root.mkdir(parents=True, exist_ok=True)


class CardUI:
    """假界面:plan_review 被叫了几次、按预设的那份回答。"""

    def __init__(self, verdict=None, boom=False):
        self.calls = []
        self.verdict = verdict if verdict is not None else {"mode": "approve"}
        self.boom = boom

    def push(self, *a, **k):
        pass

    def plan_review(self, plan):
        self.calls.append(plan)
        if self.boom:
            raise RuntimeError("卡弹不出来")
        return self.verdict


class NoCardUI:
    """命令行那一侧:压根没有 plan_review 这个方法。"""

    def push(self, *a, **k):
        pass


STEPS = [{"n": 1, "title": "读现有代码", "detail": "", "status": "pending",
          "done": False, "depends_on": [], "note": "", "verify": ""},
         {"n": 2, "title": "改解析", "detail": "verify: pytest -q", "status": "pending",
          "done": False, "depends_on": [1], "note": "", "verify": "pytest -q"}]


def _write(tmp_path, ui):
    ctx = Ctx(tmp_path, ui)
    text = planning.plan(ctx, "write", goal="把成绩导入修好",
                         steps=[{"title": "读现有代码"},
                                {"title": "改解析", "verify": "pytest -q",
                                 "depends_on": [1]}])
    return ctx, text


# ---------------------------------------------------------------- ① 没卡的一侧不许被卡住

def test_without_a_card_the_plan_is_written_and_nothing_blocks(tmp_path):
    ctx, text = _write(tmp_path, NoCardUI())
    assert "计划已写入" in text
    assert (tmp_path / "plans" / "plan.md").exists()
    assert "核对" not in text, "命令行那一侧不该看到桌面卡片的话术"


def test_a_card_that_cannot_open_says_so_instead_of_approving_itself(tmp_path):
    _ctx, text = _write(tmp_path, CardUI(boom=True))
    assert "核对单没弹成" in text and "别直接开工" in text


# ---------------------------------------------------------------- ② 三种出口

def test_approve_is_the_only_way_the_model_hears_yes(tmp_path):
    _ctx, text = _write(tmp_path, CardUI({"mode": "approve"}))
    assert "批准这张计划" in text


def test_no_answer_is_recorded_as_unconfirmed_never_as_approved(tmp_path):
    _ctx, text = _write(tmp_path, CardUI({"mode": "unanswered"}))
    assert "未确认" in text and "先把计划讲给用户" in text
    assert "批准" not in text, "没等到回答却写「批准」= 替用户点头"


def test_revise_carries_the_classmates_words_verbatim(tmp_path):
    _ctx, text = _write(tmp_path, CardUI({"mode": "revise",
                                          "note": "第二步应该先补测试"}))
    assert "重排" in text and "第二步应该先补测试" in text
    assert "别开工" in text or "别直接开工" in text


def test_revise_without_a_note_tells_it_to_ask_instead_of_guessing(tmp_path):
    _ctx, text = _write(tmp_path, CardUI({"mode": "revise", "note": "  "}))
    assert "先问一句哪里不对" in text, "让人猜着改版,改完还得再来一次"


# ---------------------------------------------------------------- ③ 改一条真的写回盘

def test_an_edit_rewrites_that_one_step_on_disk(tmp_path):
    ctx, text = _write(tmp_path, CardUI({"mode": "edit", "step": 2,
                                         "text": "先写一条会红的测试,再改解析"}))
    body = (ctx.workspace / "plans" / "plan.md").read_text(encoding="utf-8")
    assert "先写一条会红的测试,再改解析" in body
    assert "改后的这一版" in text
    assert "第 2 步" in text


def test_an_edit_keeps_the_other_steps_and_the_verify_line(tmp_path):
    """只动被点那一步的标题:状态、依赖、别步的验收判据都不该被顺手抹掉。"""
    ctx, _text = _write(tmp_path, CardUI({"mode": "edit", "step": 1,
                                          "text": "读 planning 与 scores 两处"}))
    parsed = planning.parse_plan(
        (ctx.workspace / "plans" / "plan.md").read_text(encoding="utf-8"))
    assert [s["title"] for s in parsed["steps"]] == \
        ["读 planning 与 scores 两处", "改解析"]
    assert parsed["steps"][1]["depends_on"] == [1]
    assert parsed["steps"][1]["verify"] == "pytest -q"


def test_an_edit_to_a_step_that_is_not_there_writes_nothing(tmp_path):
    ctx, text = _write(tmp_path, CardUI({"mode": "edit", "step": 9, "text": "换个说法"}))
    body = (ctx.workspace / "plans" / "plan.md").read_text(encoding="utf-8")
    assert "换个说法" not in body
    assert "第 9 步不在这张计划里" in text


def test_an_empty_edit_is_refused_because_an_empty_step_cannot_be_done(tmp_path):
    ctx, text = _write(tmp_path, CardUI({"mode": "edit", "step": 1, "text": "   "}))
    body = (ctx.workspace / "plans" / "plan.md").read_text(encoding="utf-8")
    assert "读现有代码" in body            # 原样没动
    assert "没落盘" in text


def test_the_card_shows_the_goal_and_every_step_with_its_criterion(tmp_path):
    ui = CardUI({"mode": "approve"})
    _write(tmp_path, ui)
    assert len(ui.calls) == 1
    got = ui.calls[0]
    assert got["goal"] == "把成绩导入修好"
    assert [s["n"] for s in got["steps"]] == [1, 2]
    assert got["steps"][1]["verify"] == "pytest -q"


# ---------------------------------------------------------------- ④ 桥那一条独立通道

def test_the_bridge_keeps_a_separate_lock_and_event_for_the_card():
    """与提问卡共用一条锁 = 点了核对单的批准会被当成对上一个提问的答复。"""
    src = Path("minicode/gui_bridge.py").read_text(encoding="utf-8")
    assert "_plan_lock" in src and "_plan_event" in src
    assert "_ask_lock" in src
    body = src.split("def plan_review(self", 1)[1].split("def _plan_review_trace", 1)[0]
    assert "self._plan_lock" in body and "self._ask_lock" not in body


def test_stop_and_close_wake_the_card_too():
    src = Path("minicode/gui_bridge.py").read_text(encoding="utf-8")
    seg = src.split("def abandon_pending", 1)[1].split("def ", 1)[0]
    assert "_plan_event.set()" in seg, "停止/关窗叫不醒核对单 = 线程挂着,下一切都弹不出"


def test_the_four_exits_are_one_list_not_two_copies():
    """后端认哪几种回答、前端发哪几种回答,必须是同一份名字表。

    分家写两处的表现很难查:前端多一个按钮、后端回 `未知的回答方式`,
    而卡片已经关掉了 —— 用户只看到"点了没反应"(与图 p7 同一类症状)。
    """
    assert planning.REVIEW_MODES == ("approve", "edit", "revise", "unanswered")
    src = Path("minicode/gui_bridge.py").read_text(encoding="utf-8")
    # 门面(PublicApi)与桥里各有一个同名方法:要量的是桥那一个(取最后一次出现)
    seg = src.rsplit("def plan_review_reply", 1)[1].split("\n    def ", 1)[0]
    assert "planning.REVIEW_MODES" in seg, "桥自己列了一份模式清单 = 第二处真相"
    fe = Path("frontend/index.html").read_text(encoding="utf-8")
    for mode in planning.REVIEW_MODES:
        assert f'mode: "{mode}"' in fe, f"前端没有发「{mode}」这一种出口"


def test_the_card_is_a_real_button_row_in_the_frontend():
    fe = Path("frontend/index.html").read_text(encoding="utf-8")
    assert 'id="planReviewOverlay"' in fe
    assert 'case "plan_review"' in fe
    for bid in ("prApprove", "prRevise", "prClose"):
        assert f'$("{bid}").addEventListener("click"' in fe, bid
    # 「稍后再说」必须回话:只关卡不回话 = 后端那条等待一直挂着
    assert '$("prClose").addEventListener("click", () => prReply({ mode: "unanswered" }))' in fe
    assert "plan_review_reply" in fe
    # 三处出口都收卡:卡片关着却还在等,是最难查的一种"没反应"
    assert '$("planReviewOverlay").classList.remove("show")' in fe
