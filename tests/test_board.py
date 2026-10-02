"""跨会话看板 B1(v0.9.101 ③):只聚合不重算,以及"读不到 ≠ 没有"那三条红线。"""
from minicode import board


def _src(*rows):
    return lambda: list(rows)


def test_every_row_traces_to_a_real_source():
    """喂进去什么就报什么:这一屏没有自己算出来的数(要真正能用的,不能只用于装饰)。"""
    rows = [board.row("running", "会话甲", "3 条", source="会话"),
            board.row("waiting", "第 2 步被上游卡住", source="计划"),
            board.row("done", "定时任务「周复盘」", "上次 10-01 20:00", source="定时任务")]
    d = board.aggregate({"会话": _src(rows[0]), "计划": _src(rows[1]),
                         "定时任务": _src(rows[2])})
    titles = [r["title"] for g in d["groups"].values() for r in g]
    assert sorted(titles) == sorted(r["title"] for r in rows)
    assert d["counts"] == {"running": 1, "waiting": 1, "done": 1}
    assert d["empty"] is False
    for g in d["groups"].values():
        for r in g:
            assert r["source"] in ("会话", "计划", "定时任务")


def test_broken_source_becomes_an_honest_row_not_an_empty_board():
    """一个来源抛了 → 那一行改口「没读到」,别的照常;绝不显示成「没有任务」。"""
    def boom():
        raise RuntimeError("计划文件坏了")

    d = board.aggregate({"会话": _src(board.row("running", "还在跑的那个")),
                         "计划": boom})
    assert d["ok"] is True
    assert d["failed_sources"] == ["计划"]
    waiting = d["groups"]["waiting"]
    assert len(waiting) == 1 and "没读到" in waiting[0]["title"]
    assert "计划文件坏了" in waiting[0]["sub"]
    assert d["empty"] is False


def test_scan_limit_is_spoken_not_hidden():
    """悄悄截断是最坏的一种谎:上限必须出现在载荷里,界面写"只列最近 N 个"。"""
    d = board.aggregate({"会话": _src()}, scan=7)
    assert d["scan"] == 7
    assert "只列最近 7 个会话" in d["scan_note"] and "不是全部" in d["scan_note"]


def test_row_cap_reports_the_dropped_count():
    rows = [board.row("running", f"任务 {i}") for i in range(board.ROW_LIMIT + 3)]
    d = board.aggregate({"会话": _src(*rows)})
    assert len(d["groups"]["running"]) == board.ROW_LIMIT
    assert d["more"]["running"] == 3
    assert d["counts"]["running"] == board.ROW_LIMIT + 3     # 总数照实,不因截断而少报


def test_empty_board_says_so_without_placeholder():
    d = board.aggregate({"会话": _src(), "后台命令": _src()})
    assert d["empty"] is True
    assert all(not g for g in d["groups"].values())


def test_boundary_about_running_is_in_the_payload():
    """`running` 的真源在桥内存里、跨进程看不见 —— 这句边界必须跟着数据一起出去。"""
    d = board.aggregate({"会话": _src()})
    assert "内存" in d["boundary"] and "退出重开" in d["boundary"]


def test_unknown_state_row_is_dropped_not_invented():
    d = board.aggregate({"会话": _src({"state": "nope", "title": "x"})})
    assert d["counts"] == {"running": 0, "waiting": 0, "done": 0}


def test_titles_are_clipped_not_dropped():
    """长标题截到 80 字:截要截得看得见,不能因为一行太长就把那一整条丢掉。"""
    d = board.aggregate({"会话": _src(board.row("running", "长" * 200))})
    got = d["groups"]["running"][0]
    assert len(got["title"]) == 80 and got["title"].startswith("长")


# ------------------------------------------- 跨工作区维度(B2,v0.9.102)

def test_rows_default_to_global_and_passthrough_respected():
    """来源没标 ws 的行按「全局」记(定时任务这类全局一份的账,不该被算进任何
    副本);来源明确标了的,原样透传。"""
    out = board.aggregate({"定时任务": lambda: [board.row(
        "done", "定时任务「x」", "上次 2026-10-01")]})
    assert out["groups"]["done"][0]["ws"] == "全局"
    out2 = board.aggregate({"会话": lambda: [dict(board.row(
        "running", "改登录页", ""), ws="demo__fix")]})
    assert out2["groups"]["running"][0]["ws"] == "demo__fix"


def test_ws_grouping_data_ready_for_frontend():
    """前端按「同一态内 ws 去重 >1 才插组头」渲染;这里验的是数据面:
    多工作区混跑时两种 ws 都在,单工作区时只有一种。"""
    rows = [dict(board.row("running", "A"), ws="repo__a"),
            dict(board.row("running", "B"), ws="repo__b"),
            dict(board.row("running", "C"), ws="repo__a")]
    out = board.aggregate({"会话": lambda: rows})
    g = out["groups"]["running"]
    assert {r["ws"] for r in g} == {"repo__a", "repo__b"}
    single = board.aggregate({"会话": lambda: [rows[0]]})
    assert {r["ws"] for r in single["groups"]["running"]} == {"repo__a"}
