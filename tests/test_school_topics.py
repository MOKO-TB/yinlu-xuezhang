"""主题爬取脚本的解析逻辑单测:不联网、不依赖 crawl4ai。

覆盖真实踩过的坑:标题带"-学校-部门"后缀、列表页链接文本前带日期噪声、
发布日期在正文容器之外(所以只能从整页 HTML 取)、导航/页脚清理。
"""
import json
import re

from minicode.tools.crawl import extract_body, strip_boilerplate
from scripts.crawl_school_topics import (_attachment_names,
                                         _attachment_names_from_body,
                                         _attachment_only_page,
                                         _body_is_boilerplate_only, _clean_title,
                                         _doc_ext_from_url,
                                         _find_publish_at, _has_image,
                                         _has_video, _image_page,
                                         _interleave, _is_error_page,
                                         _listing_urls, _prune_entries,
                                         _rebuild_archive_index,
                                         _strip_share_noise, _video_page)

SUFFIX = ["-广州应用科技学院", "-广州大学松田学院"]
CUT = ["上一条", "友情链接", "版权所有"]


def test_clean_title_strips_site_suffix():
    assert _clean_title("关于做好2026年国家励志奖学金评审工作的通知-广州应用科技学院-学生处",
                        SUFFIX) == "关于做好2026年国家励志奖学金评审工作的通知"


def test_clean_title_strips_listing_date_noise():
    assert _clean_title("09-11 2026 2026年下半年全国计算机等级考试考生须知", []) \
        == "2026年下半年全国计算机等级考试考生须知"
    assert _clean_title("14 2020-04 关于开办第47期入党积极分子党课培训班的通知", []) \
        == "关于开办第47期入党积极分子党课培训班的通知"
    assert _clean_title("2026年09月10日 庆祝教师节！榜样登台", []) == "庆祝教师节！榜样登台"


def test_find_publish_at_from_page_html():
    """发布日期在正文容器之外,必须能从整页 HTML 里取到(取错成截止日期更糟)。"""
    html = '<p><span>发布日期：2026-09-10</span></p><p>请于2026年9月21日前交材料</p>'
    assert _find_publish_at(html) == "2026-09-10"
    assert _find_publish_at('<span>发布时间：2026-09-11</span>') == "2026-09-11"


def test_find_publish_at_falls_back_to_listing_text():
    assert _find_publish_at("", "09-11 2026 2026年下半年全国计算机等级考试考生须知") == "2026-09-11"
    assert _find_publish_at("", "2026年09月10日 庆祝教师节！榜样登台") == "2026-09-10"
    assert _find_publish_at("", "关于做好2026年国家励志奖学金评审工作的通知") == ""


def test_extract_body_drops_nav_footer_and_keeps_title():
    md = """[![logo](https://x/logo.png)](https://x/index.htm)
  * ### [首页](https://x/index.htm)
  * ### [通知公告](https://x/tzgg.htm)
![](https://x/banner.png)
#  通知公告
当前位置: [首页](https://x/index.htm) > 正文
# 关于做好2026年国家励志奖学金评审工作的通知
发布日期：2026-09-10
各二级学院：
**一、申请范围**
在校家庭经济困难、品学兼优二年级及以上学生。
上一条：没有了！
地址：广州校区：广东省广州市增城区
版权所有 © 广州应用科技学院"""
    body = extract_body(md, "关于做好2026年国家励志奖学金评审工作的通知-广州应用科技学院-学生处", CUT)
    assert body.startswith("# 关于做好2026年国家励志奖学金评审工作的通知")
    assert "申请范围" in body and "发布日期" in body
    assert "版权所有" not in body and "友情链接" not in body
    assert "首页" not in body


def test_strip_boilerplate_keeps_plain_text_lines():
    md = "  * [招生](https://x/zs.htm)\n正文第一段\n![](https://x/i.png)\n更多内容"
    out = strip_boilerplate(md, ())
    assert out == "正文第一段\n更多内容"


def test_listing_urls_pagination():
    assert _listing_urls({"url": "https://x/xsc/tzgg.htm", "pages": 1}) == \
        ["https://x/xsc/tzgg.htm"]
    assert _listing_urls({"url": "https://x/xsc/tzgg.htm", "pages": 3}) == [
        "https://x/xsc/tzgg.htm", "https://x/xsc/tzgg/1.htm", "https://x/xsc/tzgg/2.htm"]


def test_listing_urls_explicit_list_pages():
    """新闻网翻页是倒序(/120.htm 才是第 2 页),配置里可直接写明翻页地址。"""
    seed = {"url": "https://x/news/xxyw.htm",
            "list_pages": ["https://x/news/xxyw/120.htm", "https://x/news/xxyw/119.htm"]}
    assert _listing_urls(seed) == ["https://x/news/xxyw.htm",
                                   "https://x/news/xxyw/120.htm",
                                   "https://x/news/xxyw/119.htm"]


def test_interleave_round_robins_columns():
    """轮转取人:老栏目人多时不能把别的栏目的最新条目挤掉。"""
    buckets = [[{"url": f"a{i}"} for i in range(3)], [{"url": "b0"}]]
    assert [c["url"] for c in _interleave(buckets)] == ["a0", "b0", "a1", "a2"]


class FakeLinkResult:
    def __init__(self, links):
        self.links = {"internal": links}


def test_attachment_only_page_lists_files():
    """管理办法类页面正文只在附件里(PDF 下载要验证码),保留为说明条目。"""
    result = FakeLinkResult([
        {"href": "/system/_content/download.jsp?wbfileid=X", "text": "广应科〔2022〕278号关于印发《选修课程管理办法》的通知"},
        {"href": "/other.htm", "text": "无关链接"},
        {"href": "/system/_content/download.jsp?wbfileid=X", "text": "广应科〔2022〕278号关于印发《选修课程管理办法》的通知"},
    ])
    names = _attachment_names(result)
    assert names == ["广应科〔2022〕278号关于印发《选修课程管理办法》的通知"]
    body = _attachment_only_page("广州应用科技学院公共通识类选修课程管理办法（修订）",
                                 "2022-09-14", names)
    assert body.startswith("# 广州应用科技学院公共通识类选修课程管理办法（修订）")
    assert "2022-09-14" in body and "附件" in body and names[0] in body
    assert len(body) < 400                      # 说明条目要短,别把检索位撑满


def test_rebuild_archive_index_from_frontmatter(tmp_path):
    """index.json 按目录实际内容重建,单主题补爬不会把它覆盖成局部清单。"""
    d = tmp_path / "school-qa"
    d.mkdir()
    (d / "奖助学金-001-x.md").write_text(
        "---\nurl: https://x/a.htm\ntitle: 励志奖学金通知\ntopic: 奖助学金\n"
        "publish_at: 2026-09-10\ncrawled_at: 2026-09-12 15:00\n---\n\n正文\n",
        encoding="utf-8")
    (d / "选课与学分-001-y.md").write_text(
        "---\nurl: https://x/b.htm\ntitle: 选修课程管理办法\ntopic: 选课与学分\n"
        "publish_at: 2022-09-14\ncrawled_at: 2026-09-12 15:00\n---\n\n正文\n",
        encoding="utf-8")
    assert _rebuild_archive_index(d, "测试学校") == 2
    index = json.loads((d / "index.json").read_text(encoding="utf-8"))
    assert index["count"] == 2 and index["site"] == "测试学校"
    assert [p["topic"] for p in index["pages"]] == ["奖助学金", "选课与学分"]
    assert index["pages"][0]["url"] == "https://x/a.htm"


def test_prune_removes_only_entries_all_topics_exclude():
    cfg = {"topics": [
        {"id": "学风与学习方法", "exclude": ["管理人员", "实验室"]},
        {"id": "学科竞赛", "exclude": []},                    # 没写排除词 = 不排除任何条目
        {"id": "就业与考公考编", "exclude": ["挑战杯"]},
    ]}
    entries = [
        {"title": "教学管理人员能力提升培训班", "tags": ["学风与学习方法"]},      # 删
        {"title": "机器人工程实验室", "tags": ["学风与学习方法"]},              # 删
        {"title": "华为认证备考指南", "tags": ["学风与学习方法"]},              # 留
        {"title": "挑战杯校赛通知", "tags": ["学科竞赛"]},                     # 留:该主题没排除词
        {"title": "挑战杯也是学科竞赛内容", "tags": ["学科竞赛", "就业与考公考编"]},  # 留:有主题要它
        {"title": "挑战杯在就业主题里是噪音", "tags": ["就业与考公考编"]},        # 删
    ]
    kept, removed = _prune_entries(cfg, entries)
    assert [e["title"] for e in removed] == [
        "教学管理人员能力提升培训班", "机器人工程实验室", "挑战杯在就业主题里是噪音"]
    assert len(kept) == 3


class _FakeResult:
    def __init__(self, html: str):
        self.html = html


def test_has_video_detects_embedded_player():
    """视频聚焦栏目正文为空,内容全在播放器里——必须靠页面结构识别。"""
    assert _has_video(_FakeResult(
        '<video controls id="_vsb_player"><source '
        'src="/virtual_attach_file.vsb?afc=abc"></video>'))
    assert _has_video(_FakeResult('<a href="clip.mp4">下载</a>'))
    assert _has_video(_FakeResult('<script>src="live.m3u8"</script>'))


def test_has_video_false_for_plain_article():
    """普通图文文章不应被误判成视频条目。"""
    assert not _has_video(_FakeResult("<p>各二级学院:请做好评审工作</p>"))
    assert not _has_video(_FakeResult(""))


def test_video_page_keeps_title_and_points_to_source():
    text = _video_page("肇庆校区 2023年学生社团嘉年华晚会", "2025-08-27")
    assert text.startswith("# 肇庆校区 2023年学生社团嘉年华晚会")
    assert "2025-08-27" in text
    assert "打开来源网址" in text


def test_video_page_without_publish_date():
    text = _video_page("某活动纪实", "")
    assert text.splitlines() == ["# 某活动纪实", text.splitlines()[1]]
    assert text.splitlines()[1].startswith("本条为视频内容")


def test_has_image_detects_qr_guide_page():
    """热水充值/报修这类页面正文为 0 字,内容全在二维码图片里(图在正文容器内)。"""
    assert _has_image(_FakeResult(
        '<div id="vsb_content"><img src="/virtual_attach_file.vsb?afc=zUm"></div>'))
    assert _has_image(_FakeResult(
        '<div class="v_news_content"><img src="/__local/2/3B/a_QR.png"></div>'))


def test_has_image_ignores_plain_text_page():
    assert not _has_image(_FakeResult("<p>各二级学院:</p>"))
    assert not _has_image(_FakeResult(""))


def test_has_image_ignores_header_logo():
    """页眉 logo 每页都有,不能因为整页有 <img> 就当成指引页。"""
    assert not _has_image(_FakeResult(
        '<div class="header"><img src="/__local/logo.png"></div>'
        '<div id="vsb_content"><p>广州应用科技学院学生公寓管理办法</p></div>'))


def test_image_page_keeps_link_for_user():
    text = _image_page("学生宿舍热水充值", "2025-08-27",
                       "https://www.gzasc.edu.cn/info/1134/1481.htm")
    assert text.startswith("# 学生宿舍热水充值")
    assert "查看地址: https://www.gzasc.edu.cn/info/1134/1481.htm" in text


def test_video_page_includes_watch_address():
    text = _video_page("肇庆校区社团嘉年华", "2025-09-23",
                       "https://www.gzasc.edu.cn/news/info/1010/40469.htm")
    assert "观看地址: https://www.gzasc.edu.cn/news/info/1010/40469.htm" in text


def test_is_error_page_blocks_404_shell():
    """官网失效链接也返回 HTTP 200,靠标题识别,否则会把 404 提示页入库。"""
    assert _is_error_page("404错误提示")
    assert _is_error_page("系统提示 - 广州应用科技学院")
    assert not _is_error_page("关于做好2026年国家奖学金评审工作的通知")


def test_strip_share_noise_removes_qr_widget():
    """右侧悬浮分享图标会在条目开头堆成一串二维码图片,必须清掉。"""
    raw = (
        "[ ![](https://x/qrcode_wechat.webp) ](javascript:void(0);) "
        "[ ![](https://x/qrcode_douyin.webp) ](javascript:void(0);) "
        "[ ](javascript:void(0);)\n"
        "# 校园生活\n"
        "### [学生宿舍热水充值 ](https://www.gzasc.edu.cn/info/1134/1481.htm)\n"
        "![](https://x/qrcode_weishi.webp)"
    )
    out = _strip_share_noise(raw)
    assert "qrcode" not in out
    assert "javascript" not in out
    assert "学生宿舍热水充值" in out          # 真正的服务入口链接要保留
    assert out.startswith("# 校园生活")


def test_strip_share_noise_keeps_normal_content():
    raw = "# 关于做好2026年国家奖学金评审工作的通知\n发布日期:2026-09-08\n各二级学院:"
    assert _strip_share_noise(raw) == raw


def test_has_video_ignores_qr_image_page():
    """热水充值/报修页的二维码也是 .vsb 附件,不能因此被当成视频页。"""
    qr_page = _FakeResult(
        '<div id="vsb_content"><img src="/virtual_attach_file.vsb?afc=zUm"></div>')
    assert not _has_video(qr_page)
    assert _has_image(qr_page)


def test_body_is_boilerplate_only_flags_footer_page():
    """热水充值页的 markdown 只剩标题+页脚宣传语,别把页脚当正文入库。"""
    body = (
        "# 学生宿舍热水充值\n发布时间:2025年09月05日\nShare: [ ](javascript:void(0);)\n"
        "## 最新动态\n适者大成,筑梦启航!欢迎考生报考广州应用科技学院\n"
        "与优秀同行,向理想奔赴,共赴美好未来\n#### 地址:\n"
        "广州市增城区朱村街朱村大道东432号\n#### 联系方式:\n"
        "电话:020-82850018、0758-2630111\n招生专线:020-82856988"
    )
    assert _body_is_boilerplate_only(body)


def test_body_is_boilerplate_only_keeps_real_article():
    """正常通知不能因为带发布日期就被判成页脚页。"""
    body = (
        "# 关于做好2026年国家奖学金评审工作的通知\n发布日期:2026-09-08\n"
        "各二级学院:根据学校2026年秋季学期学生资助工作安排,为做好我校2026年"
        "国家奖学金评审工作,现就有关事项通知如下:一、申请范围:在校品学兼优、"
        "综合表现优秀的二年级及以上学生。二、基本申请条件:具有中华人民共和国"
        "国籍,遵守宪法和法律,诚实守信,道德品质优良。"
    )
    assert not _body_is_boilerplate_only(body)


def test_attachment_names_from_body_parses_vsb_pdf():
    """附件链接文本为空时,按 e=.pdf 合一个名字,不让导师办法页整页丢掉。"""
    body = ("# 广州应用科技学院学生公寓管理办法(修订)\n发布日期:2022-10-17\n"
            "[广州应用科技学院学生公寓管理办法(修订)]"
            "(https://www.gzasc.edu.cn/virtual_attach_file.vsb?afc=abc&e=.pdf)\n"
            "[](https://www.gzasc.edu.cn/virtual_attach_file.vsb?afc=def&e=.docx)")
    names = _attachment_names_from_body(body)
    assert names[0].startswith("广州应用科技学院学生公寓管理办法")
    assert names[1] == "附件(.docx)"


def test_strip_share_noise_removes_bare_js_tokens():
    """去掉 markdown 结构后残留的裸 (javascript:void(0);) 也要清掉。"""
    raw = ("(javascript:void(0);) (javascript:void(0);) [ ](javascript:void(0);)\n"
           "# 学生宿舍热水充值\n发布时间:2025年09月05日")
    out = _strip_share_noise(raw)
    assert "javascript" not in out
    assert "# 学生宿舍热水充值" in out


def test_boilerplate_only_after_js_cleanup():
    """真实抓到的热水充值页正文(去噪后)应被判成"没有文章正文"。"""
    raw = ("(javascript:void(0);) (javascript:void(0);) (javascript:void(0);)\n"
           "# 学生宿舍热水充值\n发布时间:2025年09月05日\nShare: [ ](javascript:void(0);)\n"
           "## 最新动态\n适者大成,筑梦启航!欢迎考生报考广州应用科技学院\n"
           "与优秀同行,向理想奔赴,共赴美好未来\n#### 地址:\n"
           "广州市增城区朱村街朱村大道东432号\n肇庆市鼎湖区莲花镇丰乐路20号\n"
           "#### 联系方式:\n电话:020-82850018、0758-2630111\n"
           "招生专线:020-82856988、82856989、0758-2610666")
    cleaned = _strip_share_noise(raw)
    assert _body_is_boilerplate_only(cleaned)
    assert _has_image(_FakeResult(""), "![]("
                      "https://www.gzasc.edu.cn/virtual_attach_file.vsb?afc=x)")


def test_strip_share_noise_handles_escaped_js_tokens():
    """真实抓下来的 markdown 里括号是转义的 javascript:void\\(0\\);,也得清掉。"""
    raw = ("(javascript:void\\(0\\);) (javascript:void\\(0\\);) "
           "[ ](javascript:void\\(0\\);)\n"
           "# 学生宿舍热水充值\n发布时间:2025年09月05日\n"
           "Share: [ ](javascript:void\\(0\\);)\n"
           "## 最新动态\n适者大成,筑梦启航!欢迎考生报考广州应用科技学院\n"
           "与优秀同行,向理想奔赴,共赴美好未来\n#### 地址:\n"
           "广州市增城区朱村街朱村大道东432号\n"
           "#### 联系方式:\n电话:020-82850018、0758-2630111\n"
           "招生专线:020-82856988、82856989")
    out = _strip_share_noise(raw)
    assert "javascript" not in out
    assert _body_is_boilerplate_only(out)


def test_attachment_names_skip_markdown_images():
    """二维码是 markdown 图片(![]),不是附件文件,不能被当成附件名。"""
    body = ("# 学生宿舍热水充值\n"
            "![](https://www.gzasc.edu.cn/virtual_attach_file.vsb?afc=qr)")
    assert _attachment_names_from_body(body) == []


def test_doc_ext_from_url_distinguishes_attachment_from_media():
    """同一个 virtual_attach_file.vsb 路径既送附件也送二维码/视频,靠后缀区分。"""
    assert _doc_ext_from_url(
        "https://x/virtual_attach_file.vsb?afc=ab&oid=1&nid=2&e=.pdf") == ".pdf"
    assert _doc_ext_from_url(
        "https://x/virtual_attach_file.vsb?afc=ab&e=.docx") == ".docx"
    assert _doc_ext_from_url("https://x/uploadfiles/a.docx") == ".docx"
    assert _doc_ext_from_url("https://x/download.jsp?fileid=9") == ".file"
    # 二维码/照片/视频不是附件,否则热水充值页会被写成"仅附件"
    assert _doc_ext_from_url(
        "https://x/virtual_attach_file.vsb?afc=ab&e=.png") == ""
    assert _doc_ext_from_url(
        "https://x/virtual_attach_file.vsb?afc=ab&e=.mp4") == ""
    assert _doc_ext_from_url("https://x/images/qrcode_wechat.webp") == ""


def test_attachment_names_ignore_qr_media_link():
    """正文里的二维码链接(即使写成普通链接)不能算附件。"""
    body = ("# 学生宿舍热水充值\n"
            "[扫码充值](https://www.gzasc.edu.cn/virtual_attach_file.vsb?afc=q&e=.png)\n"
            "[查看办法](https://www.gzasc.edu.cn/virtual_attach_file.vsb?afc=d&e=.pdf)")
    assert _attachment_names_from_body(body) == ["查看办法"]
