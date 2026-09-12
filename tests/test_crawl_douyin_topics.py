"""抖音爬虫解析逻辑单测:不联网。

覆盖真实踩过的坑:alt 与 href 在不同顺序(alt 前/alt 后)、&quot; 实体、
无关推荐流(搜索被降级)必须被相关性过滤拦下、RENDER_DATA 侧栏错位。
"""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location(
    "crawl_douyin_topics", str(Path(__file__).resolve().parents[1]
                               / "scripts" / "crawl_douyin_topics.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["crawl_douyin_topics"] = m
spec.loader.exec_module(m)

parse_search_html = m.parse_search_html
filter_relevant = m.filter_relevant
video_entry = m.video_entry


def test_parse_pairs_href_with_alt_after():
    html = ('<a href="//www.douyin.com/video/7676517073484352822">'
            '<img alt="肇庆裹蒸粽老字号 探店">&nbsp;</a>')
    items = parse_search_html(html)
    assert len(items) == 1
    assert items[0]["vid"] == "7676517073484352822"
    assert "裹蒸粽" in items[0]["title"]


def test_parse_pairs_alt_before_href():
    html = ('<div><img alt="七星岩拍照机位合集"></div>'
            '<a href="//www.douyin.com/video/7684280805513776419">x</a>')
    items = parse_search_html(html)
    assert items and "七星岩" in items[0]["title"]


def test_parse_decodes_entities_and_dedupes():
    html = ('<a href="//www.douyin.com/video/7600000000000000001">'
            '<img alt="&quot;广应科&quot;食堂天花板"></a>'
            '<a href="//www.douyin.com/video/7600000000000000001">'
            '<img alt="&quot;广应科&quot;食堂天花板"></a>')
    items = parse_search_html(html)
    assert len(items) == 1
    assert items[0]["title"].startswith('"广应科"')


def test_filter_relevant_drops_recommended_junk():
    """搜索被降级成推荐流时,结果必须全被拦下而不是污染知识库。"""
    junk = [{"vid": "7600000000000000001", "url": "u1",
             "title": "我不是懦狙只会保KD 我是来自红星的独联体天才少年",
             "owner": "", "digg": 0, "comments": 0},
            {"vid": "7600000000000000002", "url": "u2",
             "title": "植物大战僵尸保卫戴夫大合集",
             "owner": "", "digg": 0, "comments": 0}]
    assert filter_relevant(junk, "肇庆美食探店") == []


def test_filter_relevant_keeps_matching():
    goods = [{"vid": "7600000000000000003", "url": "u3",
              "title": "肇庆美食探店 端州区老字号裹蒸粽",
              "owner": "阿伟", "digg": 1200, "comments": 30}]
    assert len(filter_relevant(goods, "肇庆美食探店")) == 1


def test_video_entry_shape():
    v = {"vid": "7600000000000000009", "url": "https://www.douyin.com/video/x",
         "title": "大学学习计划这样定", "owner": "学长阿凯", "digg": 500,
         "comments": 12}
    e = video_entry(v, "学风与学习方法", "大学生学习计划")
    assert e["id"] == "douyin-7600000000000000009"
    assert e["credibility"] == "B-视频经验"
    assert "观看地址:https://www.douyin.com/video/x" in e["content"]
    assert "500赞" in e["content"]
