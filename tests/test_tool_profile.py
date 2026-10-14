"""工具面按需加载(v0.9.173)测试:三层投影、索引行、tool_search、以及"默认关"。

钉五件事:①`all`(默认)下一切与今天**逐字一致**;②三层切法不漏不重(常驻 ∪ 可加载
= 注册表,名字全都在册);③索引行只用工具自己的描述**第一句**(不另写一份摘要),
截断了要说;④`tool_search` 真能取回、取回后下一次请求就带上、**且不可逆**;
⑤对外形态永远 `all`(否则白名单里那几只可加载工具会静默消失)。
"""
from minicode import config as config_mod
from minicode import tool_profile
from minicode.tools.base import Tool
from minicode.tools.registry import build_default_registry

# `_config_from_raw` 是 config.py 里那个 raw→Config 的唯一入口(load_config 也走它),
# 这里直接用它,免得为了造一份配置去写临时文件。
_raw = config_mod._config_from_raw


def _tool(name, desc, read_only=True):
    return Tool(name=name, description=desc, read_only=read_only,
                handler=lambda ctx, **k: "", parameters={"type": "object",
                                                        "properties": {}})


# --------------------------------------------------------- ① 三层切法

def test_the_three_layers_partition_the_registry():
    tools = build_default_registry()
    resident, loadable = tool_profile.split(tools)
    assert len(resident) + len(loadable) == len(tools), "三层必须是不漏不重的切法"
    assert {t.name for t in resident} | {t.name for t in loadable} == {t.name for t in tools}
    assert not ({t.name for t in resident} & {t.name for t in loadable})


def test_every_loadable_name_really_exists():
    """名单里写错一个名字,那只工具会**永远常驻**(它永远不会被判成可加载),
    而这条错在哪一次运行里都看不出来 —— 所以拿注册表反过来核。"""
    names = {t.name for t in build_default_registry()}
    missing = sorted(set(tool_profile.LOADABLE) - names)
    assert not missing, f"LOADABLE 里有注册表里不存在的名字:{missing}"


def test_the_door_is_never_loadable():
    """`tool_search` 是那扇门,它自己必须常驻 —— 门被收进可加载层就是死锁。"""
    assert tool_profile.is_loadable("tool_search") is False
    resident, _loadable = tool_profile.split(build_default_registry())
    assert "tool_search" in {t.name for t in resident}


def test_general_work_tools_stay_resident():
    """通用干活工具一只都不许下沉 —— 移错一只的表现是"编码回合里能力突然没了"。"""
    for name in ("read_file", "write_file", "edit_file", "apply_patch", "bash",
                 "glob", "grep", "py_script", "test_runner", "symbol_search",
                 "browser_open", "browser_screenshot", "memory", "plan",
                 "use_skill", "view_image", "session_message", "diagnostics"):
        assert tool_profile.is_loadable(name) is False, f"{name} 不该被下沉"


# --------------------------------------------------------- ② 索引行

def test_the_index_line_is_the_tools_own_first_sentence():
    t = _tool("schedule", "登记与查看个人课表(空档/换楼/作息表/回滚)。后面这一句不该进索引。")
    line = tool_profile.index_line(t)
    assert line == "- schedule: 登记与查看个人课表(空档/换楼/作息表/回滚)。"
    # 换行也算断句(描述里有的是多行的)
    assert tool_profile.first_sentence("第一句\n第二句") == "第一句"


def test_a_clipped_index_line_says_it_was_clipped():
    line = tool_profile.index_line(_tool("x", "很长" * 200))
    assert "索引行截断" in line, "截了不说 = 索引看着完整,模型会按半句猜边界"


def test_the_index_text_teaches_how_to_get_them_back():
    text = tool_profile.index_text(build_default_registry())
    assert "tool_search" in text, "只列一张'存在但拿不到'的清单比不列更糟"
    assert "- schedule:" in text and "- mood:" in text
    for name in ("read_file", "bash"):
        assert f"- {name}:" not in text, "常驻的工具不该出现在索引里(它们本来就在)"


def test_no_loadable_tools_means_no_index_section():
    assert tool_profile.index_text([_tool("read_file", "读文件。")]) == ""


# --------------------------------------------------------- ③ tool_search

def test_an_empty_query_returns_everything_loadable():
    picked, said = tool_profile.search(build_default_registry(), "")
    assert len(picked) == len(tool_profile.LOADABLE)
    assert "已全部取回" in said


def test_a_query_matches_on_name_or_description():
    picked, said = tool_profile.search(build_default_registry(), "课表")
    names = {t.name for t in picked}
    assert "schedule" in names and "星期" not in named(said)
    picked2, _ = tool_profile.search(build_default_registry(), "mood")
    assert {t.name for t in picked2} == {"mood"}


def named(text):
    return text


def test_a_query_with_no_hit_says_so_and_lists_them():
    """**不许猜一只塞给它** —— 猜错的表现是模型拿着不对的工具硬用,比"没有"难查。"""
    picked, said = tool_profile.search(build_default_registry(), "量子纠缠")
    assert picked == []
    assert "没有可加载的工具匹配" in said
    assert "- schedule:" in said, "没命中就把清单摆出来,让它照着名字再搜"


def test_separators_split_the_query_like_everywhere_else():
    picked, _ = tool_profile.search(build_default_registry(), "课表、情绪")
    names = {t.name for t in picked}
    assert {"schedule", "mood"} <= names


# --------------------------------------------------------- ④ 投影(接在 Agent 上)

def _agent(tmp_path, tool_loading):
    from minicode.agent import Agent
    from minicode.config import Config, Provider
    from minicode.permission import ALLOW
    from minicode.session import Session
    from minicode.tools.base import ToolContext

    class _Gate:
        def __init__(self):
            self.ask_kind = self.reject_reason = self.plan_lock_reason = ""

        def check(self, tool, args):
            return ALLOW

        def new_turn(self):
            pass

    class _UI:
        def __getattr__(self, name):
            return lambda *a, **k: None

    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t", tool_loading=tool_loading)
    ctx = ToolContext(workspace=tmp_path, config=cfg)
    s = Session(system_prompt="s", log_dir=None)
    a = Agent(llm=None, registry=build_default_registry(), session=s,
              gate=_Gate(), ui=_UI(), ctx=ctx, max_steps=2)
    return a, ctx, s, cfg


def test_all_is_byte_for_byte_today(tmp_path):
    a, ctx, s, cfg = _agent(tmp_path, "all")
    assert a._tools_for_request() == a.registry, "默认关时一个字节都不许变"


def test_lazy_hides_the_loadable_layer_behind_the_door(tmp_path):
    a, ctx, s, cfg = _agent(tmp_path, "lazy")
    got = {t.name for t in a._tools_for_request()}
    assert "tool_search" in got, "门必须还在"
    assert "read_file" in got and "bash" in got
    assert "schedule" not in got and "mood" not in got
    assert a.registry and len({t.name for t in a.registry}) == 73, \
        "**注册表一个字都不许动** —— 投影只是给模型看的那一份"


def test_searching_brings_them_in_and_it_is_irreversible(tmp_path):
    a, ctx, s, cfg = _agent(tmp_path, "lazy")
    tool = next(t for t in build_default_registry() if t.name == "tool_search")
    out = tool.handler(ctx, query="课表")
    assert "schedule" in out
    assert "schedule" in {t.name for t in a._tools_for_request()}, "取回后下一次请求就带上"
    # 不可逆:再搜别的不该把它收回去
    tool.handler(ctx, query="情绪")
    got = {t.name for t in a._tools_for_request()}
    assert {"schedule", "mood"} <= got, "加载过的不许收回(收回去 = 那个能力忽然没了)"


def test_the_tool_says_it_when_there_is_no_session_to_load_into(tmp_path):
    """没有会话级集合时(不在会话里跑),**不许回一句"已取回"**而其实没生效。"""
    from minicode.tools.base import ToolContext
    ctx = ToolContext(workspace=tmp_path, config=None)
    tool = next(t for t in build_default_registry() if t.name == "tool_search")
    out = tool.handler(ctx, query="课表")
    assert "没有加载集合" in out and "**没有**被加进工具面" in out


def test_the_tool_reads_the_live_registry_not_a_frozen_snapshot(tmp_path):
    """注册表会被热重载与对外形态换掉;冻一份的表现是"新工具搜不到"。"""
    from minicode.tools.base import ToolContext
    ctx = ToolContext(workspace=tmp_path, config=None)
    ctx.tool_loaded = set()
    ctx.tool_catalog = lambda: [_tool("schedule", "刚刚改过的描述里有 zzz 这个词。"),
                                _tool("mood", "情绪日记。")]
    tool = next(t for t in build_default_registry() if t.name == "tool_search")
    assert "schedule" in tool.handler(ctx, query="zzz"),         "搜的是**此刻**那一份注册表,不是装配时冻住的那一份"


# --------------------------------------------------------- ⑤ 对外形态永远 all

def test_the_public_profile_forces_all(tmp_path, monkeypatch):
    """微信版那条路要**永远 all** —— 白名单里恰好有几只在可加载层,分层一开它们
    就会从访客眼前消失,而取回它们的那扇门不在白名单里(静默没能力)。"""
    from minicode import app_setup, web_profile
    monkeypatch.setattr(app_setup, "build_system_prompt", lambda *a, **k: "sys")
    cfg_raw = {"tool_loading": "lazy", "providers": [
        {"id": "t", "name": "T", "base_url": "https://x/v1", "api_key": "k",
         "model": "m"}], "active_provider": "t"}
    cfg = _raw(cfg_raw)
    agent, _s = app_setup.build_runner_agent(cfg, tmp_path, ui=_NullUI(), llm=None)
    assert agent._tool_loading == "lazy"
    web_profile.apply_public_profile(agent, _s, tmp_path)
    assert agent._tool_loading == "all", "对外形态必须回到 all"
    assert "tool_search" not in {t.name for t in agent.registry}
    # 白名单里那几只可加载工具都还在(没有被分层挡在门外)
    names = {t.name for t in agent.registry}
    assert {"campus_calendar", "gpa_calc", "library_search"} <= names


class _NullUI:
    def __getattr__(self, name):
        return lambda *a, **k: None


def test_the_system_prompt_carries_the_index_only_in_lazy(tmp_path, monkeypatch):
    from minicode import app_setup
    monkeypatch.setattr(app_setup, "build_system_prompt", lambda *a, **k: "SYS")
    base = {"providers": [{"id": "t", "name": "T", "base_url": "https://x/v1",
                           "api_key": "k", "model": "m"}], "active_provider": "t"}
    for mode, want in (("all", False), ("lazy", True)):
        cfg = _raw({**base, "tool_loading": mode})
        _agent_obj, s = app_setup.build_runner_agent(
            cfg, tmp_path, ui=_NullUI(), llm=None)
        head = s.messages[0]["content"]
        assert ("不在本轮工具面里的能力" in head) is want, mode
        if want:
            assert "- schedule:" in head, "索引段要真带上那几行"
            assert "tool_search" in head, "还得告诉它怎么取回来"
