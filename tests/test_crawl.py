"""web_crawl 工具单测:注入假 crawler,不依赖网络与 crawl4ai。"""
import asyncio
import json

from minicode.tools import crawl


class FakeMD:
    def __init__(self, raw):
        self.raw_markdown = raw
        self.fit_markdown = None


class FakeResult:
    def __init__(self, url, title="", md="", links=None, success=True, error=""):
        self.url = url
        self.title = title
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
    path = crawl._write_kb(ctx.workspace, "www.gzasc.edu.cn", pages, "广州应用科技学院官网")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == "crawl-www.gzasc.edu.cn.json"
    assert data[0]["credibility"] == "A"
    assert data[0]["source"] == "广州应用科技学院官网"
    assert data[0]["url"] == A
    assert len(data[0]["content"]) == crawl.KB_CONTENT_LIMIT
