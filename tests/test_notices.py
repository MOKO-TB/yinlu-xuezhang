"""campus_notices(v0.9.46):时效性工具 —— 现拉官网通知 / 快照回退 / 诚实边界。

网络一律 stub:解析吃真博达站群形状的假 HTML,拉取在 handler 层被换掉,
断网机器上也能跑。
"""
import pytest

from minicode import notices, proactive

LIST_HTML = """
<html><body><div class="list">
  <a href="/jwc/info/1066/6852.htm"><div>09-24 2026</div>
    <div>关于2026年国庆节放假安排的通知 全校师生:根据学校安排,10月1日至7日放假</div></a>
  <a href="/jwc/info/1066/6901.htm"><div>09-26 2026</div>
    <div>2026年下半年全国计算机等级考试考生须知 9月19-21日举行</div></a>
  <a href="/jwc/xwdt/tzgg_2.htm"><div>下一页</div><div>翻页不是通知</div></a>
</div></body></html>
"""


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    from minicode import paths
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


def _write_state(**kw):
    state = {"enabled": True, "interval_hours": 6, "last_check": 0,
             "sources": [dict(s) for s in proactive.DEFAULT_SOURCES], "seen": {}}
    state.update(kw)
    proactive.save_state(state)
    return state


# ---------------------------------------------------------------- 日期提取

def test_norm_notice_date_shapes():
    assert proactive._norm_notice_date("09-24 2026") == "2026-09-24"
    assert proactive._norm_notice_date(" 12-01 2026 ") == "2026-12-01"
    assert proactive._norm_notice_date("下一页") == ""        # 没有结构化日期:留空,不编
    assert proactive._norm_notice_date("99-99 2026") == ""   # 非法月日:留空


def test_fetch_latest_carries_dates(monkeypatch):
    class FakeResp:
        status_code = 200
        url = "https://www.gzasc.edu.cn/jwc/xwdt/tzgg.htm"
        text = LIST_HTML

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
    items = proactive.fetch_latest("https://www.gzasc.edu.cn/jwc/xwdt/tzgg.htm")
    assert len(items) == 2                       # 翻页锚点仍被排除
    assert items[0]["date"] == "2026-09-24"
    assert items[1]["date"] == "2026-09-26"
    assert items[0]["url"].endswith("/jwc/info/1066/6852.htm")


# ---------------------------------------------------------------- 工具处理器

def test_latest_lists_titles_links_and_source(monkeypatch):
    monkeypatch.setattr(proactive, "load_state", lambda: _write_state())

    def fake_fetch(url, timeout=12.0):
        if "jwc" in url:
            return [{"date": "2026-09-26", "title": "关于期中教学检查的通知",
                     "url": "https://www.gzasc.edu.cn/jwc/info/1/1.htm"}]
        return [{"date": "", "title": "关于评选奖学金的通知",
                 "url": "https://www.gzasc.edu.cn/xsc/info/2/2.htm"}]

    monkeypatch.setattr(proactive, "fetch_latest", fake_fetch)
    out = notices.campus_notices(None, action="latest")
    assert "现拉于" in out and "2 个栏目" in out
    assert "关于期中教学检查的通知" in out
    assert "[2026-09-26] " in out                # 有结构化日期的带日期
    assert "教务处通知" in out and "学生处通知" in out
    assert "不是正文" in out and "web_crawl" in out   # 诚实边界:详情要再抓


def test_latest_one_source_down_others_still_work(monkeypatch):
    """一个源挂了只跳过它(与看门狗同口径),输出里要点名哪个没拉到。"""
    monkeypatch.setattr(proactive, "load_state", lambda: _write_state())

    def fake_fetch(url, timeout=12.0):
        if "xsc" in url:
            raise RuntimeError("改版了")
        return [{"date": "2026-09-25", "title": "关于考试安排的通知",
                 "url": "https://www.gzasc.edu.cn/jwc/info/1/2.htm"}]

    monkeypatch.setattr(proactive, "fetch_latest", fake_fetch)
    out = notices.campus_notices(None)
    assert "1 个栏目" in out and "1 个栏目这次没拉到" in out
    assert "关于考试安排的通知" in out


def test_latest_failure_falls_back_to_snapshot_and_says_so(monkeypatch):
    """全挂:退快照并**注明是快照不是实时**;绝不编「最近发生的事」。"""
    state = _write_state(last_check=1700000000.0)
    state["seen"] = {"https://www.gzasc.edu.cn/jwc/xwdt/tzgg.htm": {
        "titles": ["关于期末考试安排的通知", "关于图书馆开放时间调整的通知"],
        "updated_at": "2026-09-25 12:00:00"}}
    monkeypatch.setattr(proactive, "load_state", lambda: state)

    def boom(url, timeout=12.0):
        raise RuntimeError("断网")

    monkeypatch.setattr(proactive, "fetch_latest", boom)
    out = notices.campus_notices(None)
    assert "现在拉不到" in out and "断网" in out
    assert "不是现在的实时列表" in out
    assert "关于期末考试安排的通知" in out


def test_latest_failure_and_no_snapshot_says_nothing_to_invent(monkeypatch):
    state = _write_state()
    monkeypatch.setattr(proactive, "load_state", lambda: state)

    def boom(url, timeout=12.0):
        raise RuntimeError("断网")

    monkeypatch.setattr(proactive, "fetch_latest", boom)
    out = notices.campus_notices(None)
    assert "查不到" in out and "不要凭印象编" in out


def test_cached_reads_snapshot_without_network(monkeypatch):
    state = _write_state()
    state["seen"] = {"https://www.gzasc.edu.cn/jwc/xwdt/tzgg.htm": {
        "titles": ["关于评优的通知"], "updated_at": "2026-09-25 09:00:00"}}
    monkeypatch.setattr(proactive, "load_state", lambda: state)

    def fail_fetch(url, timeout=12.0):
        raise AssertionError("cached 模式不该联网")

    monkeypatch.setattr(proactive, "fetch_latest", fail_fetch)
    out = notices.campus_notices(None, action="cached")
    assert "快照" in out and "关于评优的通知" in out

    # 空快照:指路 latest,不装样
    monkeypatch.setattr(proactive, "load_state", lambda: _write_state())
    assert "还没有任何通知" in notices.campus_notices(None, action="cached")


def test_unknown_action_and_limit_clamp(monkeypatch):
    monkeypatch.setattr(proactive, "load_state", lambda: _write_state())
    assert "未知 action" in notices.campus_notices(None, action="bogus")


def test_tool_registered_and_prompt_section():
    """注册表里真有这把工具;提示词段把「时效问题必须现拉」说死。"""
    from minicode.tools.registry import build_default_registry
    names = {t.name for t in build_default_registry()}
    assert "campus_notices" in names
    tool = next(t for t in build_default_registry() if t.name == "campus_notices")
    assert tool.read_only is True
    section = notices.notices_prompt_section()
    assert "campus_notices" in section and "必须先调" in section
    assert "旧语料" in section


# ---------------------------------------------------------------- 相对路径栏目(v0.9.47)

XSC_HTML = """
<html><body><div class="list">
  <a href="info/1274/18641.htm">关于开展2026年国家励志奖学金交叉评审工作的通知</a>
  <a href="/xsc/info/1274/18621.htm"><div>09-20 2026</div>
    <div>关于做好我校2026年退役士兵国家教育资助相关工作的通知 各学院:</div></a>
  <a href="tzgg/1.htm">翻页链接不是通知</a>
</div></body></html>
"""


def test_fetch_latest_accepts_relative_info_links(monkeypatch):
    """学生处栏目改版后条目是相对路径(info/1274/...):
    在原始 href 上判 "/info/" 会把整栏静默滤成 0 条 —— 看门狗与
    campus_notices 双双瞎掉还看不出错。判定必须在 urljoin 之后做。"""
    from urllib.parse import urljoin

    class FakeResp:
        status_code = 200
        url = "https://www.gzasc.edu.cn/xsc/tzgg.htm"
        text = XSC_HTML

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
    items = proactive.fetch_latest("https://www.gzasc.edu.cn/xsc/tzgg.htm")
    assert len(items) == 2                       # 相对/绝对都收,翻页仍排除
    assert items[0]["url"] == urljoin(FakeResp.url, "info/1274/18641.htm")
    assert items[0]["date"] == ""                # 无日期 div:如实留空
    assert items[1]["date"] == "2026-09-20"


def test_prompts_ban_convention_filling():
    """放假天数禁用全国惯例填空:校历段/通知段/知识库段三处都要有这句。"""
    from pathlib import Path
    from minicode import calendar as cal
    sec = cal.calendar_prompt_section()
    assert "全国惯例" in sec and "campus_notices" in sec
    assert "推不出放几天" in sec                  # yearly 投影只有节点没有天数
    assert "https://www.gzasc.edu.cn/" in sec     # 溯源:给官网入口
    nsec = notices.notices_prompt_section()
    assert "放假" in nsec and "绝不用全国惯例" in nsec
    assert "不是今年的不能当今年的安排说" in nsec
    kb_src = (Path(__file__).resolve().parent.parent / "minicode" / "kb.py").read_text(
        encoding="utf-8")
    assert "绝不是今年的安排" in kb_src           # 知识库段的旧通知年份禁令
