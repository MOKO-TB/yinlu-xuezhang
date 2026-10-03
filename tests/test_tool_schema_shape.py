"""发给端点的工具 schema 必须是「严格网关读得懂」的形状。

缘起(同学实测):模型在界面上「测试」是通的,对话却发不出去 ——
`Error code: 400 - LITELLM_ERROR: Invalid Field [failed to parse request body]`。
查下来是 `render_chart.data` 写了 JSON Schema 的并集类型 `["array","object"]`:
OpenAI 协议允许,按 pydantic 校验请求体的网关不允许,而它一坏就是**整个请求体**坏 ——
56 只工具里任何一只要么全带上,要么这一轮没有工具。测试请求不带 tools,所以看着一切正常。

所以这里两头都钉:①我们自己的目录里不许有并集(闸);②对端 schema 不归我们管的 MCP 工具,
出口处收平(防线)。
"""
import json

from minicode.tools.base import Tool, _flatten_schema_types
from minicode.tools.registry import build_default_registry

LEGAL = {"string", "number", "integer", "boolean", "object", "array"}


def _walk(node, path, found):
    """递归收集所有 `type` 取值,并数一下走过了多少个节点(防这条测试空转)。"""
    if isinstance(node, dict):
        for key, val in node.items():
            if key == "type":
                found.append((path, val))
            _walk(val, path + "/" + str(key), found)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, "%s[%d]" % (path, i), found)


def _bad_types(node, path=""):
    found = []
    _walk(node, path, found)
    return [(p, t) for p, t in found if not isinstance(t, str) or t not in LEGAL]


def _tool(name="demo", parameters=None):
    return Tool(name=name, description="d", parameters=parameters or {
        "type": "object", "properties": {}}, handler=lambda ctx, **kw: "ok")


# ---------------- ① 闸:内置目录自己得干净 ----------------

def test_builtin_registry_has_no_union_types():
    reg = build_default_registry()
    assert len(reg) >= 40, "注册表读空了,这条闸就在空转"
    seen_props = 0
    for t in reg:
        schema = t.to_openai_schema()["function"]["parameters"]
        bad = _bad_types(schema)
        assert not bad, "%s 的 schema 里有严格网关读不懂的类型:%s" % (t.name, bad)
        props = (schema.get("properties") or {})
        seen_props += len(props)
    assert seen_props >= 100, "走到的参数比预期少得多,检查这条闸是不是没真走进去"


def test_render_chart_data_stays_an_array():
    """当初坏掉的那一只:并集收成 array,而 handler 照旧收得下字典与二元组。"""
    reg = {t.name: t for t in build_default_registry()}
    data = reg["render_chart"].to_openai_schema()["function"]["parameters"]["properties"]["data"]
    assert data["type"] == "array"
    assert data["type"] == "array", "别再写回 [array, object]"
    # handler 那侧的宽容没动:字典形式仍然能用(只是不再向模型推荐这种写法)
    from minicode.charts import normalize_data
    assert normalize_data({"甲": 1, "乙": 2}) == [("甲", 1.0), ("乙", 2.0)]
    assert normalize_data([["甲", 1], {"label": "乙", "value": 2}]) == [("甲", 1.0), ("乙", 2.0)]


# ---------------- ② 防线:对端 schema 收平,且不碰原对象 ----------------

def test_union_type_from_a_foreign_schema_is_flattened():
    t = _tool(parameters={"type": "object", "properties": {
        "data": {"type": ["array", "object"]},
        "when": {"type": ["string", "null"]},
        "rows": {"type": "array", "items": {"type": ["object", "string"]}},
    }})
    out = t.to_openai_schema()["function"]["parameters"]
    assert out["properties"]["data"]["type"] == "array"
    assert out["properties"]["when"]["type"] == "string"
    assert out["properties"]["rows"]["items"]["type"] == "object"


def test_flattening_picks_the_type_that_carries_the_data():
    assert _flatten_schema_types({"type": ["object", "array"]})["type"] == "array"
    assert _flatten_schema_types({"type": ["null", "integer"]})["type"] == "integer"
    assert _flatten_schema_types({"type": ["weird"]})["type"] == "string"


def test_clean_schema_is_not_copied_and_source_is_not_mutated():
    """没得改就返回同一个对象(每轮 56 只,少一份拷贝是一份);有得改也不许动 Tool 自己那份。"""
    clean = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert _flatten_schema_types(clean) is clean

    dirty = {"type": "object", "properties": {"a": {"type": ["string", "null"]}}}
    t = _tool(parameters=dirty)
    fixed = t.to_openai_schema()["function"]["parameters"]
    assert fixed["properties"]["a"]["type"] == "string"
    assert dirty["properties"]["a"]["type"] == ["string", "null"], "把共享的 parameters 改掉了"
    assert t.parameters is dirty


def test_the_walker_actually_finds_a_bad_shape():
    """正向对照:这条闸不是「永远通过」——喂一个坏形状它得报。"""
    assert _bad_types({"type": ["array", "object"]})
    assert _bad_types({"properties": {"a": {"type": "weird"}}})
    assert not _bad_types({"type": "object", "properties": {"a": {"type": "string"}}})
    assert json.dumps(_flatten_schema_types({"type": ["array", "object"]}))
