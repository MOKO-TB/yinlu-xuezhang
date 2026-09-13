"""微信公众号爬虫的解析逻辑单测:不联网、不依赖 crawl4ai。

覆盖真实踩过的坑:搜狗签名链接是一次性的(入库必须换成文章页里的
永久短链 mp.weixin.qq.com/s/xxx)、httpx 直接抓搜索页会命中 antispider、
环境异常页的识别。
"""
import re

from scripts.crawl_wx_topics import (extract_permanent_link,
                                     extract_real_link, parse_sogou_results,
                                     parse_wx_article)

# ---------------------------------------------------------------- 搜狗搜索页

SOGOU_HTML = """
<html><body>
<ul class="news-list">
<li class="news-box">
  <div class="txt-box">
    <h3><a href="/link?url=dn9a_-gY295K0Rci_xozVXfdMkSQTLW6cwJThYulHEtVjXrGTiVgS5mMERfTavslk9"
           uigs="article_title_0">期待拉满!广应科<em>肇庆校区</em>风味美食,上新了!</a></h3>
    <div class="txt-info">01食堂美食篇 饭堂目前只有一座(二期在建两座新饭堂)</div>
    <div class="s-p"><a class="account">广州应用科技学院</a>
      <span class="s2">2026-09-01</span></div>
  </div>
</li>
<li class="news-box">
  <div class="txt-box">
    <h3><a href="/link?url=ANOTHER_TOKEN_123">广州应用科技学院(肇庆校区)校园指南</a></h3>
    <div class="txt-info">学校简介 广州应用科技学院肇庆校区位于肇庆新区北岭山脉南麓</div>
    <div class="s-p"><a class="account">寻遍广应科</a><span class="s2">2026-08-20</span></div>
  </div>
</li>
</ul>
</body></html>
"""

# ---------------------------------------------------------------- 搜狗中间页
# 注意:真实页面里整个 URL 是一整行,不能换行
INTERMEDIATE_HTML = (
    '<script>...</script>\n'
    '<a href="https://mp.weixin.qq.com/s?src=11\\x26amp;timestamp=1789222569'
    '\\x26amp;ver=6962\\x26amp;signature=gMpyqvm9QTHjAHGyst2EAJZbvK3V7ksgBhrOkd0lEpVewO6E08exPngE3hrlWcukquJnnLAnMSLpnIm4-fXrDD"></a>\n'
)

# ---------------------------------------------------------------- 微信文章页

WX_ARTICLE_HTML = """
<html><body>
<div id="activity-name">入学指南都赋予爱！广应科为普通专升本新生编印两版！</div>
<a id="js_name" class="wx_tap_link">广州应用科技学院</a>
<em id="publish_time">2026-08-25</em>
<div id="js_content">
  <p>第一章 校园概况</p>
  <p>肇庆校区位于肇庆新区,首期已建成投入使用。</p>
  <p>第二章 交通出行</p>
  <p>校区已开通两条公交专线,直达肇庆东站。</p>
</div>
<script>
var msg_link = "https://mp.weixin.qq.com/s/_2kC-fXw7UjneZSrsC9CVQ";
</script>
</body></html>
"""


def test_parse_sogou_results_gets_title_link_digest_account():
    items = parse_sogou_results(SOGOU_HTML)
    assert len(items) == 2
    first = items[0]
    assert "风味美食" in first["title"]
    assert first["href"].startswith("https://weixin.sogou.com/link?url=dn9a")
    assert "饭堂" in first["digest"]
    assert first["account"] == "广州应用科技学院"
    assert first["published"] == "2026-09-01"


def test_extract_real_link_decodes_amp():
    link = extract_real_link(INTERMEDIATE_HTML)
    assert link.startswith("https://mp.weixin.qq.com/s?src=11")
    assert "timestamp=1789222569" in link
    assert "signature=gMpyqv" in link
    assert "&amp;" not in link and "\\x26amp;" not in link


def test_extract_permanent_link_only_from_msg_link():
    """第一个 /s/ 短链可能是"争议提示"横幅的固定链接,不能用;
    只有 msg_link 变量里的才是本文永久链接。"""
    assert extract_permanent_link(WX_ARTICLE_HTML) == \
        "https://mp.weixin.qq.com/s/_2kC-fXw7UjneZSrsC9CVQ"
    # 横幅链接(不在 msg_link 里)必须被忽略
    banner = ('<a href="https://mp.weixin.qq.com/s/BANNER_FIXED_123">了解更多</a>'
              ' var msg_link = "";')
    assert extract_permanent_link(banner) == ""
    assert extract_permanent_link("<html></html>") == ""


def test_parse_wx_article_extracts_title_account_content():
    art = parse_wx_article(WX_ARTICLE_HTML)
    assert "入学指南" in art["title"]
    assert art["account"] == "广州应用科技学院"
    assert "公交专线" in art["content"]
    assert "校园概况" in art["content"]       # 段落结构保留
    assert len(art["content"]) > 50


def test_parse_wx_article_empty_page():
    art = parse_wx_article("<html><body>环境异常</body></html>")
    assert art["content"] == "" and art["title"] == ""


def test_real_link_is_one_off_signature():
    """签名链接含 timestamp+signature,提醒后续维护者它是易变的。"""
    link = extract_real_link(INTERMEDIATE_HTML)
    assert "timestamp=" in link and "signature=" in link
