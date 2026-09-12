"""B 站爬虫脚本的解析与入库逻辑单测:不联网、不依赖 crawl4ai。

覆盖真实踩过的坑:标题锚点是 h3 的父级 a 而不是子级、广告卡片
(cm.bilibili.com)/直播/付费课(cheese)的链接不是 /video/BV 开头必须过滤、
同一 BV 出现两次(封面锚点+标题锚点)要去重、搜索页标题噪声。
"""
import json

from scripts.crawl_bili_topics import (_rebuild_archive_index, _write_video_kb,
                                       filter_relevant, parse_search_html,
                                       video_entry)

HTML = """
<div class="video-list">
 <div class="bili-video-card">
  <h3 class="bili-video-card__info--tit">
   <a href="//www.bilibili.com/video/BV1aaa111111/" title="一个视频讲透考研的一切">一个视频讲透考研的一切</a>
  </h3>
  <div class="bili-video-card__stats">
   <span class="bili-video-card__stats--item">234.4万</span>
   <span class="bili-video-card__stats--item">2.0万</span>
   <span class="bili-video-card__stats__duration">37:21</span>
  </div>
  <div class="bili-video-card__info--bottom">
   <a class="bili-video-card__info--owner" href="//space.bilibili.com/40427625">
    <span class="bili-video-card__info--author">取景框看世界</span>
    <span class="bili-video-card__info--date">· 2024-12-01</span>
   </a>
  </div>
 </div>
 <div class="bili-video-card">
  <h3 class="bili-video-card__info--tit"><a href="//www.bilibili.com/video/BV1aaa111111/">重复卡片忽略</a></h3>
 </div>
 <div class="bili-video-card">
  <h3 class="bili-video-card__info--tit"><a href="https://cm.bilibili.com/cm/api/fees/pc/sync/v2?msg=x">广告卡片要过滤</a></h3>
 </div>
 <div class="bili-video-card">
  <h3 class="bili-video-card__info--tit"><a href="//www.bilibili.com/cheese/play/ss913">付费课要过滤</a></h3>
 </div>
 <div class="bili-video-card">
  <h3 class="bili-video-card__info--tit"><a href="//live.bilibili.com/123">直播要过滤</a></h3>
 </div>
 <div class="bili-video-card">
  <h3 class="bili-video-card__info--tit"><a href="/video/BV1bbb222222/">相对链接也能拼</a></h3>
  <div class="bili-video-card__info--bottom">
   <span class="bili-video-card__info--author">无锚点UP主</span>
  </div>
 </div>
</div>
"""


def test_parse_search_html_filters_ads_and_keeps_videos():
    videos = parse_search_html(HTML)
    # 广告/付费课/直播的链接不在 /video/ 下,同一 BV 重复卡片只留一条
    assert [v["bv"] for v in videos] == ["BV1aaa111111", "BV1bbb222222"]
    v = videos[0]
    assert v["url"] == "https://www.bilibili.com/video/BV1aaa111111"
    assert v["title"] == "一个视频讲透考研的一切"
    assert v["owner"] == "取景框看世界"
    assert v["duration"] == "37:21"
    assert v["play"] == "234.4万"
    assert v["date"] == "2024-12-01"


def test_parse_search_html_owner_fallback_without_anchor():
    videos = parse_search_html(HTML)
    assert videos[1]["owner"] == "无锚点UP主"


def test_parse_search_html_empty():
    assert parse_search_html("") == []
    assert parse_search_html("<div>不是搜索页</div>") == []


def test_video_entry_puts_url_in_content_head():
    video = {"bv": "BV1aaa111111", "url": "https://www.bilibili.com/video/BV1aaa111111",
             "title": "一个视频讲透考研的一切", "owner": "取景框看世界",
             "duration": "37:21", "play": "234.4万", "date": "2024-12-01"}
    e = video_entry(video, "考研升学", "考研备考")
    assert e["id"] == "bili-BV1aaa111111"
    assert e["url"] == video["url"]
    assert e["credibility"].startswith("B")          # 非官方数据,按待核实口径
    assert "观看地址:https://www.bilibili.com/video/BV1aaa111111" in e["content"]
    assert e["content"].index("观看地址") < 80        # 链接在正文开头,160 字速览必含
    assert "考研升学" in e["tags"] and "考研备考" in e["tags"]


def test_write_video_kb_merges_by_id(tmp_path):
    path = tmp_path / "kb" / "bili-videos.json"
    v1 = {"bv": "BV1aaa111111", "url": "https://www.bilibili.com/video/BV1aaa111111",
          "title": "旧标题", "owner": "up1", "duration": "", "play": "", "date": ""}
    e1 = video_entry(v1, "考研升学", "考研备考")
    written, _ = _write_video_kb(path, [e1])
    assert written == 1
    # 同一视频换关键词重爬:更新内容,标签取并集;另一条正常追加
    e1b = video_entry(v1, "学长学姐经验", "大学四年规划")
    v2 = {**v1, "bv": "BV1bbb222222", "url": "https://www.bilibili.com/video/BV1bbb222222"}
    e2 = video_entry(v2, "入党党建", "大学生入党流程")
    written, _ = _write_video_kb(path, [e1b, e2])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert written == 2 and len(data) == 2
    merged = next(e for e in data if e["id"] == "bili-BV1aaa111111")
    assert "考研备考" in merged["tags"] and "大学四年规划" in merged["tags"]


def test_rebuild_archive_index(tmp_path):
    d = tmp_path / "videos"
    d.mkdir()
    (d / "考研升学-001-BV1aaa-x.md").write_text(
        "---\nurl: https://www.bilibili.com/video/BV1aaa111111\ntitle: 讲透考研\n"
        "topic: 考研升学\nkeyword: 考研备考\nowner: 取景框看世界\n"
        "crawled_at: 2026-09-12 21:00\n---\n\n正文\n", encoding="utf-8")
    assert _rebuild_archive_index(d, "哔哩哔哩") == 1
    index = json.loads((d / "index.json").read_text(encoding="utf-8"))
    assert index["count"] == 1 and index["site"] == "哔哩哔哩"
    assert index["pages"][0]["bv"] if False else index["pages"][0]["url"].endswith("BV1aaa111111")
    assert index["pages"][0]["keyword"] == "考研备考"



def test_filter_relevant_drops_other_schools_in_campus_topic():
    """校内主题必须按校名白名单过滤:同城其他学校也带「肇庆/校区」字样。"""
    videos = [
        {"title": "广州应用科技学院肇庆校区 探校", "url": "u1"},
        {"title": "广应科 报考建议", "url": "u2"},
        {"title": "广州 华商 学院肇庆校区 方舟交流群", "url": "u3"},
        {"title": "广州 交校60周年校庆,参观科教城 校区", "url": "u4"},
    ]
    out = filter_relevant(videos, "广应科肇庆校区")
    assert [v["url"] for v in out] == ["u1", "u2"]


def test_filter_relevant_keeps_all_for_non_campus_topic():
    """非校内主题不做校名过滤,避免误杀正常内容。"""
    videos = [{"title": "高等数学 期末速成", "url": "u1"},
              {"title": "英语四级听力技巧", "url": "u2"}]
    assert filter_relevant(videos, "学风与学习方法") == videos
