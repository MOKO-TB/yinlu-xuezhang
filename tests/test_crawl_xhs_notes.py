"""小红书爬虫的解析逻辑单测:不联网、不依赖 playwright。

覆盖真实踩过的坑:
- 整份 __INITIAL_STATE__ 含 new Map() 残留,整体 json.loads 会炸,
  必须定位 noteDetailMap 后配平括号只解析目标片段;
- 页面里有多处 "noteDetailMap"(如 AiNoteDetailStore),要能跳过坏的;
- 只收 24 位十六进制 id 的笔记;
- 搜索被降级时标题与关键词无重叠的推荐流条目要过滤掉。
"""
import json

from scripts.crawl_xhs_notes import (_balanced_json, filter_relevant,
                                     parse_detail_state, parse_search_items)


def _wrap(state_js: str) -> str:
    return f"<html><script>window.__INITIAL_STATE__={state_js}</script></html>"


def test_balanced_json_skips_brackets_inside_strings():
    s = '{"a":"}][","b":[1,2]} tail'
    assert _balanced_json(s, 0) == '{"a":"}][","b":[1,2]}'


def test_balanced_json_handles_escaped_quote():
    s = r'{"a":"say \"}","b":1}'
    assert _balanced_json(s, 0) == s


def test_parse_detail_state_ignores_broken_store_before_target():
    """AiNoteDetailStore 里的 noteDetailMap 是坏片段,不能让它拖垮解析。"""
    js = ('{"note":{"noteDetailMap":{"abc":{"note":{"noteId":"abc","desc":"正文",'
          '"title":"标题","user":{"nickname":"作者"},"time":1700000000000,'
          '"interactInfo":{"likedCount":7},"tagList":[{"name":"标签"}],'
          '"imageList":[1,2]}}}},'
          '"AiNoteDetailStore":{"noteDetailMap":new Map()}}')
    out = parse_detail_state(_wrap(js))
    assert out["desc"] == "正文"
    assert out["author"] == "作者"
    assert out["likes"] == "7"
    assert out["tags"] == ["标签"]
    assert out["images"] == 2


def test_parse_detail_state_returns_empty_without_state():
    assert parse_detail_state("<html>nothing</html>") == {}
    assert parse_detail_state("") == {}


def test_parse_search_items_keeps_only_note_ids():
    payload = {"data": {"items": [
        {"id": "6a0590000000000037037c39", "xsec_token": "t1",
         "note_card": {"display_title": "真笔记", "user": {"nickname": "a"},
                       "interact_info": {"liked_count": "12"}}},
        {"id": "user-id-not-note", "note_card": {"display_title": "用户卡片"}},
        {"note_card": {"note_id": "ZZZZ", "display_title": "非十六进制"}},
    ]}}
    out = parse_search_items(payload)
    assert [c["id"] for c in out] == ["6a0590000000000037037c39"]
    assert out[0]["title"] == "真笔记"
    assert out[0]["likes"] == "12"
    assert out[0]["xsec_token"] == "t1"


def test_filter_relevant_drops_unrelated_feed():
    cards = [{"title": "肇庆美食探店合集"}, {"title": "我的世界空岛生存"},
             {"title": "植物大战僵尸动画"}]
    out = filter_relevant(cards, "肇庆美食探店")
    assert [c["title"] for c in out] == ["肇庆美食探店合集"]


def test_parse_detail_state_reads_json_style_map():
    """noteDetailMap 为普通对象(非 Map)时同样要能取到笔记。"""
    js = json.dumps({"note": {"noteDetailMap": {
        "id1": {"note": {"noteId": "id1", "desc": "d", "title": "t"}}}}},
        ensure_ascii=False)
    assert parse_detail_state(_wrap(js))["desc"] == "d"
