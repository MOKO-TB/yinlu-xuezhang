"""web_search 工具测试:解析 / 引擎兜底 / 失败文案(不联网,假 HTML)。"""
import pytest

from minicode.tools import crawl

BING_HTML = """
<html><body>
<ol id="b_results">
  <li class="b_algo"><h2><a href="https://gzasc.edu.cn/xiaoli">广东应用科技学院校历</a></h2>
    <div class="b_caption"><p>2026 年 10 月 1 日至 7 日放假调休。</p></div></li>
  <li class="b_algo"><h2><a href="https://example2.cn/b">第二条</a></h2>
    <p>另一个摘要。</p></li>
  <li class="b_algo"><h2><a href="javascript:void(0)">无链接的条目会被跳过</a></h2></li>
</ol></body></html>
"""

BAIDU_HTML = """
<html><body>
<div class="result c-container"><h3><a href="https://www.baidu.com/link?url=abc">百度结果一</a></h3>
  <span class="c-abstract">百度摘要内容。</span></div>
<div class="result c-container"><h3><a href="https://direct.example.cn/c">直链结果</a></h3>
  <div class="c-abstract">另一个摘要。</div></div>
</body></html>
"""


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    """_resolve_redirect 一律原样返回:解析测试不真的发请求。"""
    monkeypatch.setattr(crawl, "_resolve_redirect", lambda url: url)


def test_bing_results_parsed(monkeypatch):
    calls = []

    def fake_get(url, timeout=crawl.SEARCH_TIMEOUT):
        calls.append(url)
        return 200, BING_HTML

    monkeypatch.setattr(crawl, "_http_get", fake_get)
    out = crawl.web_search(None, "广应科校历", top_k=5)
    assert "必应" in out and len(calls) == 1
    assert "1. 广东应用科技学院校历" in out
    assert "https://gzasc.edu.cn/xiaoli" in out
    assert "2026 年 10 月 1 日至 7 日放假调休" in out
    assert "以官方信息为准" in out


def test_baidu_fallback_when_bing_empty(monkeypatch):
    urls = []

    def fake_get(url, timeout=crawl.SEARCH_TIMEOUT):
        urls.append(url)
        if "bing.com" in url:
            return 200, "<html><body><ol id='b_results'></ol></body></html>"
        return 200, BAIDU_HTML

    monkeypatch.setattr(crawl, "_http_get", fake_get)
    out = crawl.web_search(None, "something")
    assert "百度" in out
    assert any("baidu.com/s" in u for u in urls)
    assert "百度结果一" in out and "直链结果" in out


def test_both_engines_empty_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(crawl, "_http_get", lambda url, timeout=15: (200, "<html></html>"))
    out = crawl.web_search(None, "冷门查询")
    assert out.startswith("错误:") and "browser_open" in out


def test_http_failure_gives_actionable_error(monkeypatch):
    def boom(url, timeout=15):
        raise ConnectionError("network down")

    monkeypatch.setattr(crawl, "_http_get", boom)
    out = crawl.web_search(None, "查询")
    assert out.startswith("错误:") and "必应" in out and "百度" in out


def test_empty_query_rejected():
    assert crawl.web_search(None, "   ").startswith("错误:")


def test_top_k_clamped(monkeypatch):
    rows = "".join(
        f'<li class="b_algo"><h2><a href="https://e.cn/{i}">t{i}</a></h2><p>s</p></li>'
        for i in range(20))
    monkeypatch.setattr(crawl, "_http_get",
                        lambda url, timeout=15: (200, f"<html><body>{rows}</body></html>"))
    out = crawl.web_search(None, "查询", top_k=999)
    assert out.count("网址:") <= crawl.SEARCH_TOPK_LIMIT
