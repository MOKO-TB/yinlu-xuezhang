"""web_crawl 工具单测:注入假 crawler,不依赖网络与 crawl4ai。"""
import asyncio
import json
import sys

from minicode.tools import crawl


class FakeMD:
    def __init__(self, raw):
        self.raw_markdown = raw
        self.fit_markdown = None


class FakeResult:
    def __init__(self, url, title="", md="", links=None, success=True, error="",
                 metadata=None):
        self.url = url
        self.title = title
        self.metadata = metadata or {}
        self.markdown = FakeMD(md)
        self.links = links or {}
        self.success = success
        self.error_message = error


class FakeCrawler:
    def __init__(self, pages):
        self.pages = pages          # url -> FakeResult
        self.requested = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def arun(self, url, config=None):
        self.requested.append(url)
        if url not in self.pages:
            return FakeResult(url, success=False, error="404 Not Found")
        return self.pages[url]


A = "https://www.gzasc.edu.cn/"
B = "https://www.gzasc.edu.cn/xxyw.htm"
C = "https://www.gzasc.edu.cn/zs/zkxx.htm"
D = "https://www.gzasc.edu.cn/about.htm"
OTHER = "https://www.example.com/page"
PDF = "https://www.gzasc.edu.cn/file.pdf"


def crawl_now(ctx, pages, **kw):
    """调用被测异步函数:注入 FakeCrawler(共享实例以便断言 requested),返回 (pages, failed, crawler)。"""
    site_dir = ctx.workspace / "crawled" / "www.gzasc.edu.cn"
    crawler = FakeCrawler(pages)
    kw.setdefault("max_pages", 10)
    kw.setdefault("max_depth", 1)
    kw.setdefault("match", "")
    kw.setdefault("delay", 0.2)
    result = asyncio.run(crawl._crawl_site(
        A, site_dir=site_dir, factory=lambda: crawler, run_cfg=None, **kw))
    return *result, crawler


def test_single_page_saves_md_and_index(ctx):
    crawler = FakeCrawler({A: FakeResult(A, title="广州应用科技学院", md="# 首页\n欢迎报考")})
    pages, failed, crawler = crawl_now(ctx, crawler.pages, max_depth=0)
    assert len(pages) == 1 and not failed
    text = (ctx.workspace / "crawled" / "www.gzasc.edu.cn" / "001-index.md").read_text(encoding="utf-8")
    assert "url: https://www.gzasc.edu.cn/" in text
    assert "title: 广州应用科技学院" in text
    assert "# 首页" in text
    assert crawler.requested == [A]          # depth=0 不跟链


def test_bfs_follows_same_host_only_and_skips_binary(ctx):
    crawler = FakeCrawler({
        A: FakeResult(A, "首页", "a", links={
            "internal": [{"href": "/xxyw.htm"}, {"href": OTHER}, {"href": PDF}]}),
        B: FakeResult(B, "学校要闻", "b"),
    })
    pages, failed, crawler = crawl_now(ctx, crawler.pages)
    assert [p["url"] for p in pages] == [A, B]
    assert crawler.requested == [A, B]       # 外域与 pdf 不请求
    assert not failed


def test_match_filters_links(ctx):
    crawler = FakeCrawler({
        A: FakeResult(A, "首页", "a", links={
            "internal": [{"href": "/zs/zkxx.htm"}, {"href": "/about.htm"}]}),
        C: FakeResult(C, "招考信息", "c"),
    })
    pages, _, crawler = crawl_now(ctx, crawler.pages, match="zs")
    assert [p["url"] for p in pages] == [A, C]
    assert crawler.requested == [A, C]


def test_max_pages_caps_crawl(ctx):
    crawler = FakeCrawler({
        A: FakeResult(A, "首页", "a", links={
            "internal": [{"href": "/xxyw.htm"}, {"href": "/zs/zkxx.htm"}, {"href": "/about.htm"}]}),
        B: FakeResult(B, "b", "b"),
        C: FakeResult(C, "c", "c"),
        D: FakeResult(D, "d", "d"),
    })
    pages, _, _ = crawl_now(ctx, crawler.pages, max_pages=2)
    assert len(pages) == 2

def test_failed_page_recorded(ctx):
    pages, failed, _ = crawl_now(ctx, {})    # 全部 404
    assert not pages
    assert failed and "404" in failed[0]["error"]


def test_url_validation(ctx):
    assert crawl.crawl_web(ctx, url="www.gzasc.edu.cn").startswith("错误")
    assert crawl.crawl_web(ctx, url="ftp://www.gzasc.edu.cn/").startswith("错误")
    assert crawl.crawl_web(ctx, url="").startswith("错误")


def test_out_dir_outside_workspace_rejected(ctx):
    out = crawl.crawl_web(ctx, url=A, out_dir=r"C:\Windows\evil")
    assert out.startswith("错误")
    assert not (ctx.workspace / "crawled").exists()


def test_write_kb_entries(ctx):
    pages = [{"url": A, "title": "首页", "file": "001-index.md", "content": "x" * 3000}]
    path, written, skipped = crawl._write_kb(
        ctx.workspace, "www.gzasc.edu.cn", pages, "广州应用科技学院官网",
        category="奖助学金", extra_tags=["奖学金", "奖学金"])     # 重复标签自动去重
    data = json.loads(path.read_text(encoding="utf-8"))
    assert (written, skipped) == (1, 0)
    assert path.name == "crawl-www.gzasc.edu.cn.json"
    assert data[0]["credibility"] == "A"
    assert data[0]["source"] == "广州应用科技学院官网"
    assert data[0]["url"] == A
    assert data[0]["tags"][:3] == ["奖助学金", "奖学金", "官网抓取"]
    assert len(data[0]["content"]) == crawl.KB_CONTENT_LIMIT


def test_write_kb_skips_nav_only_page(ctx):
    """纯导航页(正文太短)只归档不入库,避免污染检索结果。"""
    pages = [
        {"url": A, "title": "招生就业", "file": "f", "content": "* [招生](https://x/zs.htm)"},
        {"url": B, "title": "奖学金通知", "file": "f", "content": "评审条件:" + "详" * 300},
    ]
    path, written, skipped = crawl._write_kb(ctx.workspace, "h.cn", pages, "")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert (written, skipped) == (1, 1)
    assert [e["url"] for e in data] == [B]


def test_page_title_uses_metadata_then_heading(ctx):
    """crawl4ai 0.9 起标题在 metadata 里;没有标题时退回正文首个 # 标题。"""
    r_meta = FakeResult(A, metadata={"title": "广州应用科技学院"})
    assert crawl._page_title(r_meta, "# 其他", A) == "广州应用科技学院"
    r_none = FakeResult(A)
    assert crawl._page_title(r_none, "# 奖学金评审通知\n正文", A) == "奖学金评审通知"
    assert crawl._page_title(r_none, "无标题正文", A) == A


def test_extract_body_drops_nav_and_cuts_at_title():
    md = """[![logo](https://x/logo.png)](https://x/index.htm)
  * ### [首页](https://x/index.htm)
  * ### [通知公告](https://x/tzgg.htm)
#  通知公告
当前位置: [首页](https://x/index.htm) > 正文
# 关于做好2026年国家励志奖学金评审工作的通知
发布日期：2026-09-10
各二级学院：
**一、申请范围**
在校家庭经济困难、品学兼优二年级及以上学生。
上一条：没有了！
版权所有 © 广州应用科技学院"""
    body = crawl.extract_body(md, "关于做好2026年国家励志奖学金评审工作的通知-学生处")
    assert body.startswith("# 关于做好2026年国家励志奖学金评审工作的通知")
    assert "申请范围" in body
    assert "版权所有" not in body and "首页" not in body


def test_html_to_markdown_keeps_text_and_links():
    """内置轻量抓取器(打包版引擎)的 HTML→markdown。"""
    html = """<html><head><title>奖学金通知-学生处</title>
    <style>.a{color:red}</style><script>var x=1;</script></head>
    <body><nav><a href="/index.htm">首页</a></nav>
    <h1>关于做好奖学金评审工作的通知</h1>
    <p>申请条件：<a href="/f/1.pdf">附件1</a>与成绩单。</p>
    <ul><li>平均分80分以上</li><li>单科不低于67分</li></ul>
    <table><tr><td>学院</td><td>名额</td></tr></table></body></html>"""
    title, md, links = crawl.html_to_markdown(html, "https://x.edu.cn/xsc/info/1.htm")
    assert title == "奖学金通知-学生处"
    assert "# 关于做好奖学金评审工作的通知" in md
    assert "平均分80分以上" in md and "单科不低于67分" in md
    assert "[附件1](https://x.edu.cn/f/1.pdf)" in md
    assert "| 学院 | 名额 |" in md
    assert "color:red" not in md and "var x=1" not in md
    assert "首页" not in md                              # 导航整块丢掉
    hrefs = [link["href"] for link in links]
    assert "https://x.edu.cn/index.htm" in hrefs        # 相对链接已转绝对


def test_engine_falls_back_to_native_without_crawl4ai(monkeypatch):
    """打包版没有 crawl4ai,应自动切到内置抓取器而不是报错。"""
    monkeypatch.setitem(sys.modules, "crawl4ai", None)   # 触发 ImportError
    engine, factory, run_cfg = crawl._engine()
    assert engine == "native" and run_cfg is None
    assert isinstance(factory(), crawl.NativeCrawler)


def test_write_kb_merges_by_url(ctx):
    """定向补爬新栏目时保留旧条目;重爬同一 url 就地更新,不产生重复。"""
    crawl._write_kb(ctx.workspace, "h.cn", [{"url": "https://h.cn/a", "title": "A页",
                                             "file": "f", "content": "旧" * 300}], "")
    crawl._write_kb(ctx.workspace, "h.cn", [
        {"url": "https://h.cn/a", "title": "A页新版", "file": "f", "content": "新" * 300},
        {"url": "https://h.cn/b", "title": "B页", "file": "f", "content": "补" * 300},
    ], "")
    data = json.loads((ctx.workspace / "kb" / "crawl-h.cn.json").read_text(encoding="utf-8"))
    assert len(data) == 2                                    # a 更新 + b 新增,不重复
    a = next(e for e in data if e["url"] == "https://h.cn/a")
    assert a["title"] == "A页新版" and a["content"] == "新" * 300


# ---------------- 采集层四件(v0.9.54):回话真相 / 解析器健康针 / 大小闸 / 时效提醒 ----------------

def test_crawl_web_kb_message_carries_real_credibility(ctx, monkeypatch):
    """to_kb 回话的可信度要按实际写入的等级说(v0.9.54):白名单外域名写入的是
    B-抓取(未核实),回话原来写死 credibility=A —— 模型会照着告诉同学「已按
    A 级可信入库」,与磁盘真相不符(M4 修复时漏掉的半截)。"""
    def fake_engine():
        pages = {A: FakeResult(A, "首页", "x" * 3000),
                 OTHER: FakeResult(OTHER, "示例页", "y" * 3000)}
        return "fake", (lambda: FakeCrawler(pages)), None
    monkeypatch.setattr(crawl, "_engine", fake_engine)
    out = crawl.crawl_web(ctx, A, max_pages=1, max_depth=0, to_kb=True)
    assert "credibility=A" in out                      # 校官网在白名单里,照常标 A

    out2 = crawl.crawl_web(ctx, OTHER, max_pages=1, max_depth=0, to_kb=True)
    assert "B-抓取(未核实)" in out2
    assert "credibility=A)" not in out2
    assert "不要当官方口径转述" in out2                 # 非白名单要提醒口径


def test_bing_parser_still_works_on_recorded_page():
    """搜索解析器的健康对账针(v0.9.54):必应改版会让 _parse_bing 静默返回空,
    两个引擎都空 = 「搜索坏了」却没人知道为什么(proactive 学生处改版的同型)。
    样本页(tests/fixtures/bing_search.html,真实结果页)解析不出结果时这条红
    —— 重新录制 fixture 并更新 _parse_bing 的选择器。"""
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "tests" / "fixtures"
            / "bing_search.html").read_text(encoding="utf-8")
    rows = crawl._parse_bing(html)
    assert len(rows) >= 3, "必应结果页解析不出结果 —— 站方可能已改版,请重录 fixture 并更新 _parse_bing"
    assert all(r["url"].startswith("http") and r["title"] for r in rows[:3])


def test_http_get_truncates_oversized_response(ctx, monkeypatch):
    """响应体超 5MB 截断(v0.9.54):搜索用不着整个响应,异常大页面不该吃满内存
    (与网页版 MAX_BODY_BYTES 同一族的资源闸)。"""
    class FakeStream:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_text(self):
            for _ in range(6):
                yield "x" * (1024 * 1024)              # 共 6MB > 5MB 上限
    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, method, url): return FakeStream()
    import httpx as _httpx
    monkeypatch.setattr(_httpx, "Client", FakeClient)
    status, text = crawl._http_get("https://example.com/big")
    assert status == 200
    assert len(text) < 6 * 1024 * 1024                 # 被截在上限附近,不是全量


def test_native_crawler_aborts_oversized_body(ctx):
    """NativeCrawler 流式读超限即中止:URL 来自模型/用户,异常大响应体不该进内存。"""
    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/html"}
        url = OTHER
        charset_encoding = "utf-8"
        async def aiter_bytes(self):
            for _ in range(7):
                yield b"y" * (1024 * 1024)
        async def aclose(self): pass
    class FakeClient:
        def build_request(self, *a, **k): return object()
        async def send(self, req, stream=True): return FakeResp()
    crawler = crawl.NativeCrawler()
    crawler._client = FakeClient()
    res = asyncio.run(crawler.arun(OTHER))
    assert not res.success and "上限" in res.error_message


def test_web_search_notes_freshness_caveat(ctx, monkeypatch):
    """时效提醒(v0.9.54):搜索结果的发布日期搜索引擎未必标明,尾部固定提醒
    别把没有日期的结果当「今年的」回答。"""
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "tests" / "fixtures"
            / "bing_search.html").read_text(encoding="utf-8")
    monkeypatch.setattr(crawl, "_http_get", lambda url, timeout=15: (200, html))
    out = crawl.web_search(ctx, "四六级报名时间")
    assert "时效类问题" in out and "没有日期的搜索结果" in out
    assert "campus_notices" in out
