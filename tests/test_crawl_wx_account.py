"""定向公众号爬虫的纯函数单测:不联网、不依赖 crawl4ai。

覆盖真实踩过的坑:
- 搜狗卡片的公众号名已从 a.account 迁到 span.all-time-y2(两个都要兼容)
- 搜索卡片链接是易变的一次性签名链,不能入库;用户可点的是文章里的
  规范永久链接 mp.weixin.qq.com/s?__biz=..&mid=..&idx=..&sn=..&chksm=..
- **chksm 不能少**:实测缺 chksm 的链接只返回空壳页(约 17KB 无正文)
- 正文互链被 JS 双重转义(\x26amp;),正则不能把反斜杠当终止符
- 视频号(data-mpvid)没有公开网页地址,只能标注"在原文里看"
"""
from scripts.crawl_wx_account import (article_key, canonical_url, clean_url,
                                      entry_id, extract_canonical_candidates,
                                      extract_signature_link,
                                      extract_video_ids, find_perm_links,
                                      is_relevant, kb_entry, parse_article,
                                      parse_canonical, parse_published,
                                      parse_sogou_results, pick_canonical,
                                      relink_entry, same_title,
                                      sogou_fallback_url, title_fingerprint,
                                      topic_of, video_links)

BIZ = "MzAwNDIxMjI3MQ=="
MID, IDX = "2654095988", "1"
SN = "c235ae9259e7cd81c6a715aa749576b7"
CHKSM = "81eceef2fed22895a49e8dcfec1f06c568288b700aa84591ed7a21e9cc53e57b"
assert len(CHKSM) == 64, len(CHKSM)
CANON = canonical_url(BIZ, MID, IDX, SN, CHKSM)

SN2 = "abc123" + "0" * 26                       # 32 位(另一篇文章)
CHKSM2 = "80e9f9b9" + "c" * 56                   # 64 位

# ---------------------------------------------------------------- 搜狗搜索页
# 新结构:span.all-time-y2 存公众号名;时间在 script 里给 epoch
SOGOU_NEW = """
<html><body>
<ul class="news-list">
<li class="news-box">
  <div class="txt-box">
    <h3><a href="/link?url=dn9a_TOKEN_ONE">期待拉满!广应科<em>肇庆校区</em>风味美食,上新了!</a></h3>
    <p class="txt-info">01食堂美食篇 饭堂目前只有一座(二期在建两座新饭堂)</p>
    <div class="s-p">
      <span class="all-time-y2">广州应用科技学院</span>
      <span class="s2"><script>document.write(timeConvert('1691146820'))</script>2023-8-4</span>
    </div>
  </div>
</li>
<li class="news-box">
  <div class="txt-box">
    <h3><a href="/link?url=dn9a_TOKEN_TWO">广州应用科技学院(肇庆校区)校园指南</a></h3>
    <p class="txt-info">学校简介 肇庆校区位于肇庆新区北岭山脉南麓</p>
    <div class="s-p"><span class="all-time-y2">寻遍广应科</span>
      <span class="s2">2023-7-26</span></div>
  </div>
</li>
</ul>
</body></html>
"""

# 旧结构:a.account(老页面,必须继续兼容)
SOGOU_OLD = """
<ul class="news-list">
<li><div class="txt-box">
  <h3><a href="/link?url=OLD_TOKEN">广应科肇庆校区第二条公交专线也要来啦!</a></h3>
  <div class="txt-info">途经肇庆东站、鼎湖山</div>
  <div class="s-p"><a class="account">广应科在线</a><span class="s2">2024-09-01</span></div>
</div></li>
</ul>
"""

INTERMEDIATE = (
    '<a href="https://mp.weixin.qq.com/s?src=11\\x26amp;timestamp=1789222569'
    '\\x26amp;ver=6962\\x26amp;signature=gMpyqvm9QTHjAHGyst2EAJZbvK3V7ksg"></a>'
)

# ---------------------------------------------------------------- 文章页
ARTICLE = f"""
<html><head>
<meta property="og:title" content="预告丨广州应用科技学院肇庆校区落成典礼暨揭牌仪式时间确定" />
<meta property="og:url" content="https://mp.weixin.qq.com/s?__biz={BIZ}&amp;mid={MID}&amp;idx={IDX}&amp;sn={SN}&amp;chksm={CHKSM}" />
</head><body>
<div id="activity-name">预告丨广州应用科技学院肇庆校区落成典礼暨揭牌仪式时间确定</div>
<a id="js_name">广州应用科技学院</a>
<em id="publish_time">2021-07-06</em>
<div id="js_content">
  <p>2020年9月9日开工奠基</p>
  <p>历经418个风雨兼程的日日夜夜</p>
  <p><a target="_blank" href="http://mp.weixin.qq.com/s?__biz={BIZ}\\x26amp;mid=2654079737\\x26amp;idx=1\\x26amp;sn={SN2}\\x26amp;chksm={CHKSM2}">肇庆校区正式开工奠基</a></p>
  <p><a href="https://mp.weixin.qq.com/s?__biz=OTHERBIZ&amp;mid=1&amp;idx=2&amp;sn={'a' * 32}&amp;chksm={'b' * 64}">它校文章</a></p>
  <p><mpvideosnap data-mpvid="wxv_1347231138791735299"></mpvideosnap></p>
  <p><iframe data-src="//v.qq.com/iframe/player.html?vid=abc12345678&amp;width=500"></iframe></p>
</div>
<script>
var biz = "{BIZ}" || "";
var sn = "" || "" || "";
var mid = "{MID}" || "";
var idx = "{IDX}" || "";
</script>
</body></html>
"""


def test_parse_sogou_results_new_structure():
    items = parse_sogou_results(SOGOU_NEW)
    assert len(items) == 2
    first = items[0]
    assert "风味美食" in first["title"]
    assert first["href"].startswith("https://weixin.sogou.com/link?url=dn9a")
    assert "饭堂" in first["digest"]
    assert first["account"] == "广州应用科技学院"
    # epoch 1691146820 → 2023-08-04(比页面上的 "2023-8-4" 更规整)
    assert first["published"] == "2023-08-04"
    assert items[1]["account"] == "寻遍广应科"
    assert items[1]["published"] == "2023-07-26"


def test_parse_sogou_results_keeps_old_account_selector():
    items = parse_sogou_results(SOGOU_OLD)
    assert len(items) == 1
    assert items[0]["account"] == "广应科在线"
    assert items[0]["published"] == "2024-09-01"


def test_parse_sogou_results_ignores_non_link_cards():
    assert parse_sogou_results("<ul class='news-list'><li>空</li></ul>") == []


def test_clean_url_strips_fragment_and_round_trips():
    dirty = (f"http://mp.weixin.qq.com/s?__biz={BIZ}\\x26amp;mid={MID}"
             f"\\x26amp;idx={IDX}\\x26amp;sn={SN}\\x26amp;chksm={CHKSM}#rd")
    clean = clean_url(dirty)
    assert "\\x26amp;" not in clean and "&amp;" not in clean
    assert "#rd" not in clean                     # og:url 常带 #rd 尾巴
    assert clean.startswith("https://mp.weixin.qq.com/s?")
    assert parse_canonical(clean) == (BIZ, MID, IDX, SN, CHKSM)


def test_canonical_url_round_trips_through_parse():
    assert parse_canonical(CANON) == (BIZ, MID, IDX, SN, CHKSM)
    assert CANON.startswith("https://mp.weixin.qq.com/s?__biz=")
    assert "chksm=" in CANON and "sn=" in CANON


def test_parse_canonical_requires_chksm():
    """实测:缺 chksm 的链接只返回空壳页,不能给用户点。"""
    no_chksm = (f"https://mp.weixin.qq.com/s?__biz={BIZ}&mid={MID}"
                f"&idx={IDX}&sn={SN}")
    assert parse_canonical(no_chksm) == ("", "", "", "", "")
    # 带 chksm 才认
    assert parse_canonical(no_chksm + "&chksm=" + CHKSM)[0] == BIZ


def test_parse_canonical_trims_overlong_sn_and_chksm():
    """从 HTML 里挖出来的 chksm 常带尾巴(实测 76 位)。多一个字符就打不开,
    所以只取前 64 位;sn 同理取前 32 位。"""
    dirty = (f"https://mp.weixin.qq.com/s?__biz={BIZ}&mid={MID}&idx={IDX}"
             f"&sn={SN}{'a' * 12}&chksm={CHKSM}{'b' * 12}")
    assert parse_canonical(dirty) == (BIZ, MID, IDX, SN, CHKSM)
    assert canonical_url(*parse_canonical(dirty)) == CANON
    # 非十六进制的脏尾巴要整段丢弃,不能只看长度
    bad = (f"https://mp.weixin.qq.com/s?__biz={BIZ}&mid={MID}&idx={IDX}"
           f"&sn={SN}&chksm={'#' * 64}")
    assert parse_canonical(bad) == ("", "", "", "", "")


def test_normalize_links_rewrites_dirty_urls():
    from scripts.crawl_wx_account import normalize_links
    dirty = (f"https://mp.weixin.qq.com/s?__biz={BIZ}&mid={MID}&idx={IDX}"
             f"&sn={SN}&chksm={CHKSM}{'b' * 12}")
    kb = [{"id": "x", "title": "t", "content": "原文链接:" + dirty,
           "url": dirty, "_canonical": True}]
    assert normalize_links(kb) == 1
    assert kb[0]["url"] == CANON
    assert CANON in kb[0]["content"] and "chksm=" + CHKSM + "b" not in kb[0]["content"]
    assert normalize_links(kb) == 0            # 幂等


def test_parse_canonical_rejects_incomplete_and_short_links():
    assert parse_canonical("https://mp.weixin.qq.com/s/_2kC-fXw7UjneZSrsC9CVQ") \
        == ("", "", "", "", "")
    assert parse_canonical(f"https://mp.weixin.qq.com/s?__biz={BIZ}&mid=1&idx=1") \
        == ("", "", "", "", "")
    assert parse_canonical("https://example.com/x") == ("", "", "", "", "")
    # 签名链(一次性)不是永久链接,不能入库
    assert parse_canonical(
        "https://mp.weixin.qq.com/s?src=11&timestamp=1&signature=abc") \
        == ("", "", "", "", "")


def test_article_key_requires_all_parts():
    assert article_key(BIZ, "1", "2") == f"{BIZ}|1|2"
    assert article_key("", "1", "2") == ""


def test_extract_signature_link_decodes_escape_and_is_marked_volatile():
    link = extract_signature_link(INTERMEDIATE)
    assert link.startswith("https://mp.weixin.qq.com/s?src=11")
    assert "timestamp=1789222569" in link and "signature=gMpy" in link
    assert "&amp;" not in link and "\\x26amp;" not in link
    assert extract_signature_link("<html></html>") == ""


def test_find_perm_links_filters_by_biz_and_dedupes():
    links = find_perm_links(ARTICLE, BIZ)
    assert len(links) == 1
    assert parse_canonical(links[0])[:3] == (BIZ, "2654079737", "1")
    # 不限制 biz 时把别号文章也算进来
    assert len(find_perm_links(ARTICLE)) == 2


def test_parse_article_extracts_identity_content_and_videos():
    art = parse_article(ARTICLE)
    assert "落成典礼" in art["title"]
    assert art["account"] == "广州应用科技学院"
    assert art["published"] == "2021-07-06"
    assert art["biz"] == BIZ and art["mid"] == MID and art["idx"] == IDX
    # 页面自带 og:url → 本文规范永久链接(可直接给用户点)
    assert art["canonical"] == CANON
    assert "肇庆校区" in art["content"]
    assert art["videos"]["channels"] == ["wxv_1347231138791735299"]


def test_parse_article_empty_page():
    art = parse_article("<html><body>环境异常</body></html>")
    assert art["title"] == "" and art["content"] == ""
    assert art["canonical"] == "" and art["links"] == []


def test_video_id_extraction_ignores_static_template_vid():
    """页面 JS 模板里有静态 vid=wx0clsqx...,不算真视频。"""
    template = 'vid = "" || "" || "wx0clsqxat6lzly601";'
    assert extract_video_ids(template)["channels"] == []


def test_video_id_extraction_separates_channels_from_tencent():
    """data-mpvid 是视频号;腾讯视频只从 v.qq.com 内嵌里认。"""
    vids = extract_video_ids(
        '<mpvideosnap data-mpvid="wxv_1347231138791"></mpvideosnap>'
        '<iframe data-src="//v.qq.com/iframe/player.html?vid=abc12345678">')
    assert vids["channels"] == ["wxv_1347231138791"]
    assert vids["tencent"] == ["abc12345678"]
    assert video_links(vids) == ["https://v.qq.com/x/page/abc12345678.html"]
    # 视频号没有公开网页地址
    assert video_links({"channels": ["wxv_1"], "tencent": []}) == []


def test_published_epoch_beats_text():
    html = "<script>timeConvert('1691146820')</script>"
    assert parse_published("2023-8-4", html) == "2023-08-04"
    assert parse_published("2021-07-06", "") == "2021-07-06"
    assert parse_published("", "") == ""
    # 都没有时退回页面里的 var ct
    assert parse_published("", 'var ct = "1625579184"') == "2021-07-06"


def test_is_relevant_requires_zhaoqing_signal():
    kws = ["肇庆", "大旺", "四会", "鼎湖", "北岭", "星湖"]
    # 标题命中地名 → 相关
    assert is_relevant("肇庆校区新生攻略", "", kws)
    assert is_relevant("决战肇庆！武林争霸夺魁", "", kws)
    # 正文命中地名 + 校区语境 → 相关
    assert is_relevant("校园指南", "肇庆校区位于肇庆新区北岭山脉", kws)
    assert is_relevant("新校区启用", "学校在四会建有实训基地", kws)
    # 正文只提地名但没有校园语境 → 不算(避免地名碰瓷)
    assert not is_relevant("校内通知", "会议由肇庆市某公司赞助举办", kws)
    # 完全无关
    assert not is_relevant("国家奖学金通知", "校内各单位", kws)
    # 「广应科」「桞田」不再当关键词:该号每篇都含它们
    assert not is_relevant("论剑峨眉！他代表广大桞田出战", "桞田学院校友", kws)


def test_topic_of_picks_expected_bucket():
    assert topic_of("肇庆校区新生报到须知", "") == "新生入学"
    assert topic_of("第二条公交专线开通", "途经肇庆东站") == "交通出行"
    assert topic_of("食堂美食上新了", "") == "校园生活"
    assert topic_of("图书馆", "肇庆校区图书馆开馆") == "校区设施"
    assert topic_of("普通通知", "", default="肇庆校区") == "肇庆校区"


def test_title_fingerprint_ignores_whitespace_and_zero_width():
    assert title_fingerprint(" 广应科\u200b肇庆 校区 ") == \
        title_fingerprint("广应科肇庆校区")
    assert title_fingerprint("A") != title_fingerprint("B")


def test_kb_entry_has_clickable_canonical_url_and_video_note():
    art = parse_article(ARTICLE)
    entry = kb_entry(art, art["canonical"], "校区设施", "A",
                     ["广州应用科技学院 肇庆校区"])
    assert entry["url"] == CANON
    assert entry["_canonical"] is True
    assert entry["credibility"] == "A"
    assert entry["publish_at"] == "2021-07-06"
    assert "原文链接:" + CANON in entry["content"]
    assert "视频号" in entry["content"]          # 视频号只能在原文里看
    assert "v.qq.com" in entry["content"]        # 腾讯视频给可点链接
    assert "搜狗微信入口" not in entry["content"]
    assert "肇庆校区" in entry["tags"]
    assert entry["id"].startswith("wxgz-")
    assert entry_id("x") == entry_id("x") and entry_id("x") != entry_id("y")


def test_kb_entry_marks_sogou_fallback_link_as_not_canonical():
    art = parse_article(ARTICLE)
    fallback = sogou_fallback_url(art["title"])
    assert fallback.startswith("https://weixin.sogou.com/weixin?type=2&query=")
    entry = kb_entry(art, fallback, "肇庆校区", "B")
    assert entry["_canonical"] is False
    assert "搜狗微信入口" in entry["content"]     # 明确告知用户多一步
    # 存了文章身份,补链时能按 (mid, idx) 精确匹配
    assert entry["_mid"] == MID and entry["_idx"] == IDX


# ------------------------------------------------- 规范链接补全(百度)

BAIDU_HTML = (
    '<html><body><div class="result">'
    '<a href="https://mp.weixin.qq.com/s?__biz=OTHER&amp;mid=9999&amp;idx=1'
    '&amp;sn=' + 'a' * 32 + '&amp;chksm=' + 'b' * 64 + '">另一篇</a>'
    '<a href="http://mp.weixin.qq.com/s?__biz=' + BIZ + '&amp;mid=' + MID +
    '&amp;idx=' + IDX + '&amp;sn=' + SN + '&amp;chksm=' + CHKSM + '&amp;scene=6">目标</a>'
    '<a href="https://mp.weixin.qq.com/s/incomplete">不完整</a>'
    '</div></body></html>'
)


def test_extract_canonical_candidates_keeps_only_complete_links():
    cands = extract_canonical_candidates(BAIDU_HTML)
    assert len(cands) == 2                 # 缺 chksm 的 /s/ 短链被丢
    assert all(parse_canonical(u)[0] for u in cands)
    assert all("&amp;" not in u for u in cands)


def test_pick_canonical_matches_by_mid_only():
    cands = extract_canonical_candidates(BAIDU_HTML)
    # 只认 mid 对得上的那一条(标题相近的文章很多)
    hit = pick_canonical(cands, MID, IDX)
    assert parse_canonical(hit) == (BIZ, MID, IDX, SN, CHKSM)
    assert pick_canonical(cands, "111111", IDX) == ""
    assert pick_canonical(cands, "") == ""      # 没有 mid 就不猜


def test_same_title_tolerates_minor_differences():
    assert same_title("肇庆校区新生攻略", "肇庆校区新生攻略")
    assert same_title("肇庆校区新生攻略！", "肇庆校区新生攻略")
    assert not same_title("肇庆校区新生攻略", "广州校区新生攻略")
    assert not same_title("", "肇庆校区新生攻略")
    assert not same_title("肇庆校区", "肇庆校区新生攻略与周边美食全指南合集")


def test_relink_entry_swaps_url_and_drops_note():
    art = parse_article(ARTICLE)
    art["canonical"] = ""
    entry = kb_entry(art, sogou_fallback_url(art["title"]), "肇庆校区", "A")
    assert entry["_canonical"] is False
    relink_entry(entry, CANON)
    assert entry["url"] == CANON
    assert entry["_canonical"] is True
    assert "weixin.sogou.com" not in entry["content"]
    assert "原文链接:" + CANON in entry["content"]
    assert "搜狗微信入口" not in entry["content"]
