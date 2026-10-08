"""校园地图:手绘地图建筑索引、地名解析(四态)与 map_* 工具。

数据是线下交付包(手绘地图 + 36 张单栋标注图)的索引,坐标基于源图 2559×1279。
"""
import json
from pathlib import Path

import pytest

from minicode import campus


class FakeUI:
    """捕获工具推给界面的图形指令 + 满足 Agent 需要的界面方法。"""

    def __init__(self):
        self.events = []

    def push(self, type_, **payload):
        self.events.append({"type": type_, **payload})

    def of(self, op):
        return [e for e in self.events if e.get("op") == op]

    def on_text_delta(self, t): pass

    def on_reasoning_delta(self, t): pass

    def on_retry(self, e, n): pass

    def finish_stream(self): pass

    def confirm_tool(self, tool, args, reason="", note="", can_remember=False): return "yes"

    def tool_start(self, tool, args): pass

    def tool_done(self, tool, result): pass

    def tool_rejected_hard(self, tool, args): pass

    def compacted(self, before, after): pass

    def info(self, text): pass

    def error(self, text): pass

    def turn_interrupted(self): pass

    def llm_error(self, e): pass

    def max_steps_reached(self, n): pass


def make_ctx(tmp_path, config=None):
    from minicode.tools.base import ToolContext

    ctx = ToolContext(workspace=tmp_path, config=config)
    ctx.ui = FakeUI()
    return ctx


# ------------------------------------------------------------ 交付索引本身

def test_index_loads_with_delivery_shape():
    idx = campus.load_index()
    bs = campus.buildings(idx)
    assert len(bs) == 36
    assert len({int(b["no"]) for b in bs}) == 36          # 编号不重复
    for b in bs:
        assert b.get("name") and b.get("category") and b.get("image")
        box = b.get("bbox_on_map") or []
        assert len(box) == 4 and box[2] > box[0] and box[3] > box[1]
        assert len(b.get("center_on_map") or []) == 2
        assert (b.get("aliases") or []), b["name"]
    # 交付包声明的东西必须在
    assert idx.get("how_to_resolve") and idx.get("caveats")
    assert len(idx.get("not_buildings") or []) == 10
    assert len(idx.get("alias_conflicts") or {}) == 20


def test_source_size_matches_delivery_map():
    size = campus.source_size(campus.load_index())
    assert (size["width"], size["height"]) == (2559, 1279)


def test_generated_assets_exist_on_disk():
    """生成的资源要真的在(缺失说明忘了跑 scripts/build_campus_assets.py)。"""
    idx = campus.load_index()
    root = Path(campus.index_path()).parent
    paths = [campus.image_of(idx, b) for b in campus.buildings(idx)]
    assert len(set(paths)) == 36
    for rel in paths:
        assert rel.startswith("img/campus/1280/") and rel.endswith(".webp")
    missing = [p for p in paths if not (root / p).exists()]
    assert not missing, f"缺建筑图: {missing[:3]}"
    assert (root / "img" / "campus" / "map.jpg").exists()


def test_frontend_payload_has_boxes_and_no_index_internals():
    payload = campus.frontend_payload()
    assert payload["base_map"].endswith("map.jpg")
    assert payload["size"] == {"width": 2559, "height": 1279}
    assert len(payload["items"]) == 36
    first = payload["items"][0]
    assert set(first) >= {"no", "name", "category", "bbox", "center", "area",
                          "nearby", "image", "aliases"}
    # 前端搜索框要用俗称做本地匹配,别名必须跟着下发
    assert all(i["aliases"] for i in payload["items"])
    assert payload["not_buildings"] and payload["caveats"]
    assert "how_to_resolve" not in payload      # 规则在后端实现,不必下发


# ------------------------------------------------------------ 归一化

def test_normalize_strips_question_words_and_units():
    n = campus.normalize
    assert n("图书馆怎么走") == n("图书馆")
    assert n("E栋") == n("E座") == n("e栋") == "e"
    assert n("s5在哪儿?") == "s5"
    assert n("  学思苑 A  ") == n("学思苑A")


def test_normalize_keeps_names_comparable():
    # 汉字名去掉"楼/栋/座"后仍然可比:两侧同时去掉,不影响一致性
    assert campus.normalize("J1至善楼") == campus.normalize("j1至善楼")
    assert campus.normalize("图书馆大楼") != campus.normalize("图书馆")


# ------------------------------------------------------------ 四态解析

def test_resolve_unique_hit():
    idx = campus.load_index()
    for q in ("图书馆", "校图书馆", "图书信息中心", "图书馆大楼", "S5", "s5",
              "正校门", "正门", "西门", "体育馆", "综合体育馆", "桃园食堂"):
        res = campus.resolve(idx, q)
        assert res["status"] == "hit", (q, res["status"])
    assert campus.resolve(idx, "校图书馆")["building"]["name"] == "图书馆"
    assert campus.resolve(idx, "西门")["building"]["name"] == "西大门"
    # 交付索引里没有的叫法就是没有 —— 不要自己发明别名
    assert campus.resolve(idx, "旧图")["status"] == "miss"


def test_resolve_real_ambiguity_never_guesses():
    """A栋~E栋 在地图上是两栋楼:必须报歧义,不能挑一个。"""
    idx = campus.load_index()
    for unit in "ABCDE":
        res = campus.resolve(idx, f"{unit}栋")
        assert res["status"] == "ambiguous", unit
        names = {b["name"] for b in res["candidates"]}
        assert len(names) == 2
        assert any(n.startswith("学思苑") for n in names)
        assert any(n.startswith("集贤苑") for n in names)
    res = campus.resolve(idx, "学思苑")
    assert res["status"] == "ambiguous" and len(res["candidates"]) >= 10


def test_resolve_latin_case_and_units():
    idx = campus.load_index()
    assert campus.resolve(idx, "j1")["building"]["name"] == "J1至善楼"
    assert campus.resolve(idx, "J1栋")["building"]["name"] == "J1至善楼"
    assert campus.resolve(idx, "学思苑e栋")["building"]["name"] == "学思苑E"


def test_resolve_not_building_places():
    idx = campus.load_index()
    for q in ("田径场", "篮球场在哪", "丰乐渠", "科技广场", "风雨连廊",
              "广州大道", "掬溪桥"):
        res = campus.resolve(idx, q)
        assert res["status"] == "not_building", (q, res["status"])
        assert res["place"].get("where")


def test_resolve_miss():
    idx = campus.load_index()
    assert campus.resolve(idx, "霍格沃茨大楼")["status"] == "miss"
    assert campus.resolve(idx, "")["status"] == "miss"


# ------------------------------------------------------------ 工具

def test_find_place_hit_pushes_focus_with_image(tmp_path, config):
    ctx = make_ctx(tmp_path, config)
    out = campus.map_find_place(ctx, "校图书馆")
    assert "图书馆" in out
    assert "位置图" in out or "不要编路线" in out
    ev = ctx.ui.of("focus")[0]
    assert ev["items"][0]["name"] == "图书馆"
    assert ev["items"][0]["image"].endswith(".webp")
    assert ev["items"][0]["bbox"]


def test_find_place_ambiguous_asks_user(tmp_path, config):
    ctx = make_ctx(tmp_path, config)
    out = campus.map_find_place(ctx, "E栋")
    assert "不要替用户挑一个" in out or "请先问用户" in out
    ev = ctx.ui.of("focus")[0]
    assert ev["ask"] is True
    assert len(ev["items"]) == 2


def test_find_place_not_building_and_miss(tmp_path, config):
    ctx = make_ctx(tmp_path, config)
    out = campus.map_find_place(ctx, "田径场")
    assert "不是建筑" in out and not ctx.ui.of("focus")
    assert ctx.ui.of("notice")[0]["level"] == "info"

    out = campus.map_find_place(ctx, "三体楼")
    assert "没有" in out and "不要编" in out
    assert ctx.ui.of("notice")[-1]["level"] == "warn"


def test_show_list_filters_by_category(tmp_path, config):
    ctx = make_ctx(tmp_path, config)
    out = campus.map_show_list(ctx, category="食堂")
    assert "桃园饭堂" in out and "图书馆" not in out
    ev = ctx.ui.of("list")[0]
    assert ev["items"] and all(i["category"] == "食堂" for i in ev["items"])
    assert "田径场" in out          # 非建筑也要交代一句


def test_highlight_multiple_and_reports_gaps(tmp_path, config):
    ctx = make_ctx(tmp_path, config)
    out = campus.map_highlight_building(ctx, ["图书馆", "S1格致楼", "不存在楼"])
    ev = ctx.ui.of("focus")[0]
    assert {i["name"] for i in ev["items"]} == {"图书馆", "S1格致楼"}
    assert "不存在楼" in out


def test_highlight_ambiguous_marks_all_and_warns(tmp_path, config):
    ctx = make_ctx(tmp_path, config)
    out = campus.map_highlight_building(ctx, ["A栋"])
    assert "歧义" in out
    assert len(ctx.ui.of("focus")[0]["items"]) == 2


def test_drop_pin_by_name_and_coords(tmp_path, config):
    ctx = make_ctx(tmp_path, config)
    out = campus.map_drop_pin(ctx, name="图书馆", label="集合点")
    ev = ctx.ui.of("pin")[0]
    assert ev["label"] == "集合点" and ev["no"] == 19
    assert "图书馆" in out

    campus.map_drop_pin(ctx, x=100, y=200, label="自定义点")
    assert ctx.ui.of("pin")[1]["no"] == 0
    assert "超出底图范围" in campus.map_drop_pin(ctx, x=9999, y=1)


def test_drop_pin_rejects_ambiguous_and_non_building(tmp_path, config):
    ctx = make_ctx(tmp_path, config)
    assert "歧义" in campus.map_drop_pin(ctx, name="B栋")
    assert "不是建筑" in campus.map_drop_pin(ctx, name="田径场")
    assert not ctx.ui.of("pin")


def test_clear_scope(tmp_path, config):
    ctx = make_ctx(tmp_path, config)
    campus.map_clear(ctx, scope="pin")
    assert ctx.ui.of("clear")[0]["scope"] == "pin"
    campus.map_clear(ctx, scope="乱填")     # 非法值兜底成 all
    assert ctx.ui.of("clear")[1]["scope"] == "all"


def test_tools_work_without_gui(tmp_path, config):
    """终端/CLI 形态没有界面通道:结论照给,不能抛异常。"""
    from minicode.tools.base import ToolContext

    ctx = ToolContext(workspace=tmp_path, config=config)
    assert "图书馆" in campus.map_find_place(ctx, "图书馆")
    assert "桃园饭堂" in campus.map_show_list(ctx, category="食堂")
    assert "图书馆" in campus.map_highlight_building(ctx, ["图书馆"])


def test_registry_exposes_new_map_tools_readonly(config, ctx):
    from minicode.tools.registry import build_default_registry

    tools = {t.name: t for t in build_default_registry()}
    for name in ("map_find_place", "map_show_list", "map_highlight_building",
                 "map_drop_pin", "map_clear"):
        assert name in tools, name
        assert tools[name].read_only is True
    # 没有路网数据,就不该再有画路线的工具
    assert "map_draw_route" not in tools


def test_map_tool_runs_through_agent_and_pushes_event(tmp_path, config):
    """端到端:模型调用 map_find_place → 界面收到 focus 事件。"""
    from minicode.agent import Agent
    from minicode.llm import AssistantTurn, ToolCall
    from minicode.permission import PermissionGate
    from minicode.session import Session
    from minicode.tools.registry import build_default_registry

    class LLM:
        def __init__(self):
            self.turns = [
                AssistantTurn(tool_calls=[ToolCall(
                    id="c1", name="map_find_place",
                    arguments=json.dumps({"query": "E栋"}))]),
                AssistantTurn(text="E栋可能是学思苑E或者集贤苑E,你说的是哪一栋?"),
            ]

        def stream_chat(self, messages, tools, on_text=None, **kw):
            return self.turns.pop(0)

    ctx = make_ctx(tmp_path, config)
    agent = Agent(llm=LLM(), registry=build_default_registry(),
                  session=Session(system_prompt="sys"),
                  gate=PermissionGate(config), ui=ctx.ui, ctx=ctx)
    agent.run_turn("E栋在哪")

    ev = ctx.ui.of("focus")[0]
    assert ev["ask"] is True and len(ev["items"]) == 2
    tool_msg = next(m for m in agent.session.messages if m["role"] == "tool")
    assert "不要" in tool_msg["content"]      # 明确禁止模型自己挑一个


# ------------------------------------------------------------ 歧义答案的记忆(v0.9.43)

@pytest.fixture
def _app(tmp_path, monkeypatch):
    """campus-choices.json 落 APP_DIR:钉到临时目录,测试互不串。"""
    from minicode import paths
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    return tmp_path / "app"


def test_choice_roundtrip_and_bad_file(_app, tmp_path):
    assert campus.recall_choice("E栋") is None          # 没记过
    assert campus.remember_choice("E栋", {"no": 21, "name": "学思苑E"})
    rec = campus.recall_choice("E 栋!")                 # 归一化后同一个键
    assert rec and rec["name"] == "学思苑E" and rec["no"] == 21
    (_app / campus.CHOICES_FILE).write_text("{坏了", encoding="utf-8")
    assert campus.load_choices() == {}                  # 坏文件当空账,不炸


def test_find_place_ambiguous_without_memory_lists_candidates(_app, config, tmp_path):
    ctx = make_ctx(tmp_path, config)
    out = campus.map_find_place(ctx, "E栋")
    assert "上次" not in out and "不要替用户挑一个" in out


def test_find_place_chosen_records_and_focuses(_app, config, tmp_path):
    ctx = make_ctx(tmp_path, config)
    out = campus.map_find_place(ctx, "E栋", chosen="学思苑E")
    assert "已记下" in out and "学思苑E" in out
    ev = ctx.ui.of("focus")[0]
    assert len(ev["items"]) == 1 and ev["items"][0]["name"] == "学思苑E"
    assert campus.recall_choice("E栋")["name"] == "学思苑E"


def test_find_place_chosen_by_alias_also_wins(_app, config, tmp_path):
    """用户说的楼名带俗称(别名)也要认得出是候选之一。"""
    ctx = make_ctx(tmp_path, config)
    out = campus.map_find_place(ctx, "A栋", chosen="学思苑A")
    assert "已记下" in out
    assert campus.recall_choice("A栋")["no"]


def test_find_place_chosen_outside_candidates_refuses(_app, config, tmp_path):
    """chosen 不是候选:不记账、不出单栋图,把候选再摆一遍。"""
    ctx = make_ctx(tmp_path, config)
    out = campus.map_find_place(ctx, "E栋", chosen="图书馆")
    assert "不在" in out and "候选" in out
    assert campus.recall_choice("E栋") is None
    assert len(ctx.ui.of("focus")) == 1                 # 只有候选那次,没有定位图


def test_find_place_recall_offers_previous_choice(_app, config, tmp_path):
    """有记忆的歧义:先把"上次你说是 X"摆出来,候选照旧全标,仍不自动选。"""
    ctx = make_ctx(tmp_path, config)
    campus.map_find_place(ctx, "E栋", chosen="学思苑E")
    ctx2 = make_ctx(tmp_path, config)
    out = campus.map_find_place(ctx2, "E栋")
    assert "上次" in out and "学思苑E" in out
    assert "不要替用户挑一个" in out or "先问" in out
    ev = ctx2.ui.of("focus")[0]
    assert len(ev["items"]) == 2                        # 候选全标,不自动缩成一栋


def test_system_prompt_wires_chosen_roundtrip():
    """歧义确认的动线必须闭环到 chosen(v0.9.46 针):记忆机制的入口在
    map_find_place(chosen=),提示词只教到「问清是哪一栋」的话,模型答完不会把
    答案传回来 —— 记忆形同虚设,还会多一轮往返。改提示词别把这半步删了。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "system_prompt.md").read_text(
        encoding="utf-8")
    assert "chosen" in src, "系统提示词丢了 chosen 回传指引(v0.9.46 加的)"
    assert "重调一次" in src
