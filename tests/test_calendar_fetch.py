"""校历自动发现的口径测试(v0.9.190)。

三件事:只认学校域名(工具收 URL,不设这道闸就是 SSRF 入口)、发现层能从
分页里找标题带「校历」的条目、读回来的带日期行是**原文**而不是解析出来的
结构化日期(解析越准,错一天的风险越大 —— 这一层刻意不解析)。
HTTP 走 httpx.MockTransport,不发真网络。
"""
import httpx
import pytest

from minicode import calendarsrc


def _client(pages):
    """pages: {url: html};handler 记下请求过的 URL。"""
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        html = pages.get(str(req.url))
        if html is None:
            return httpx.Response(404, content=b"nope")
        return httpx.Response(200, content=html.encode("utf-8"),
                              headers={"content-type": "text/html; charset=utf-8"})

    return httpx.Client(transport=httpx.MockTransport(handler),
                        follow_redirects=True), calls


def test_only_the_school_domain_is_allowed():
    ok, why = calendarsrc.check_school_url("https://www.gzasc.edu.cn/info/1052/1.htm")
    assert ok is True
    for bad in ("https://evil.example/a.htm", "http://127.0.0.1/a",
                "file:///C:/x", "https://gzasc.edu.cn.evil.example/a"):
        ok, why = calendarsrc.check_school_url(bad)
        assert ok is False, bad
    assert "只认学校官网域名" in calendarsrc.check_school_url("https://evil.example/")[1]


LIST_HTML = """
<html><body>
<a href="info/1052/1.htm">2026年08月24日 广应科2026-2027学年校历，正式发布</a>
<a href="info/1052/2.htm">2026年10月 某条新闻</a>
<a href="xxyw/76.htm">2</a>
</body></html>
"""


def test_find_scans_list_and_pagination_and_filters_by_title():
    client, calls = _client({calendarsrc.LIST_PAGE: LIST_HTML})
    res = calendarsrc.find_calendar_notices(pages=2, client=client)
    assert res["ok"] is True
    assert len(res["items"]) == 1
    it = res["items"][0]
    assert it["title"].startswith("广应科2026-2027学年校历")   # 日期前缀已剥掉
    assert it["url"].endswith("/info/1052/1.htm")
    assert it["raw_title"].startswith("2026年08月24日")


def test_find_stops_as_soon_as_it_finds_one():
    """新的校历总在最近几页:找到就不翻后面(别为了"更全"把 20 页都扫一遍)。"""
    list2 = '''<html><body>
    <a href="info/1052/1.htm">2026年08月24日 校历发布</a>
    <a href="xxyw/76.htm">2</a><a href="xxyw/75.htm">3</a>
    </body></html>'''
    client, calls = _client({calendarsrc.LIST_PAGE: list2})
    calendarsrc.find_calendar_notices(pages=10, client=client)
    assert len(calls) == 1, calls


def test_find_says_honestly_when_nothing_matches():
    html = '<html><body><a href="info/1.htm">某条新闻</a></body></html>'
    client, _ = _client({calendarsrc.LIST_PAGE: html})
    res = calendarsrc.find_calendar_notices(pages=2, client=client)
    assert res["ok"] is True and res["items"] == []


def test_find_reports_when_the_site_is_unreachable():
    client, _ = _client({})
    res = calendarsrc.find_calendar_notices(pages=2, client=client)
    assert res["ok"] is False and res["why"]


NOTICE_HTML = """
<html><head><title>广应科2026-2027学年校历，正式发布-广州应用科技学院</title></head>
<body><div class="v_news_content">
<p>2026-2027学年第一学期</p>
<p>秋季学期：2026年8月31日-2027年1月17日，共20周</p>
<p>9月下旬，全国计算机等级考试</p>
<p>第19-20周为考试周</p>
<p>寒假：2027年1月18日-2月28日，共6周</p>
<img src="/a.png"><img src="/b.png">
</div></body></html>
"""


def test_read_returns_text_and_raw_date_lines_without_parsing():
    client, _ = _client({"https://www.gzasc.edu.cn/info/1052/25525.htm": NOTICE_HTML})
    got = calendarsrc.read_notice("https://www.gzasc.edu.cn/info/1052/25525.htm",
                                  client=client)
    assert got["ok"] and "2026-2027学年校历" in got["title"]
    texts = [c["text"] for c in got["candidates"]]
    assert any("2026年8月31日-2027年1月17日" in t for t in texts)
    assert any("9月下旬" in t for t in texts), "没具体日子的行也要原样列出,不能丢"
    assert any("第19-20周" in t for t in texts)
    assert got["images"] == 2


def test_read_refuses_a_url_outside_the_school_domain():
    with pytest.raises(calendarsrc.CalendarFetchError, match="只认学校官网域名"):
        calendarsrc.read_notice("https://evil.example/x.htm")


def test_the_tool_output_points_at_calendar_edit_and_warns_not_to_invent():
    client, _ = _client({"https://www.gzasc.edu.cn/info/1052/25525.htm": NOTICE_HTML})
    orig = httpx.Client
    httpx.Client = lambda **kw: client
    try:
        text = calendarsrc.calendar_fetch(None, "read",
                                          "https://www.gzasc.edu.cn/info/1052/25525.htm")
    finally:
        httpx.Client = orig
    assert "带日期的行" in text and "calendar_edit" in text
    assert "不要编" in text


def test_pagination_is_enumerated_because_the_site_hides_later_pages():
    """首页只露 2~5 页与尾页,而校历可能在更后面 —— 编号递减,按最大号往下枚举。"""
    html = ('<html><body><a href="xxyw/76.htm">2</a><a href="xxyw/75.htm">3</a>'
            '<a href="xxyw/74.htm">4</a><a href="xxyw/73.htm">5</a>'
            '<a href="xxyw/1.htm">尾页</a></body></html>')
    urls = calendarsrc._pagination_urls(html, pages=7)
    assert urls[0].endswith("/xxyw/76.htm")
    assert any(u.endswith("/xxyw/72.htm") for u in urls), urls
    assert len(urls) == 6
