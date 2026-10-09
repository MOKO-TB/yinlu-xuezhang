# -*- coding: utf-8 -*-
"""出站网址策略 N1(v0.9.141):模型能自己填网址的那三条路,由这台机器的主人管。

这一层的定位写在 `minicode/netguard.py` 开头,一句话:**它是策略,不是沙箱**。
所以这一族针有两半:一半钉"该拦的真拦住了",另一半钉"没那本事的地方不许吹"
—— 后者更重要,因为"界面上说挡住了、其实没挡"是这个项目列在禁忌里的最贵坏法。
"""
import pytest

from minicode import netguard
from minicode.config import Config, Provider, _net_policy_or_empty
from minicode.permission import ASK, REJECT, PermissionGate
from minicode.sandbox import SandboxPolicy, SandboxState
from minicode.tools.registry import build_default_registry

GOOD = "https://jwxt.gzasc.edu.cn/kb/index.jsp"
EVIL = "https://evil.example.org/x"


def _config(policy=None, mode="confirm"):
    return Config(
        providers=[Provider(id="t", name="T", base_url="https://x.invalid/v1",
                            api_key="k", model="m")],
        active_provider_id="t", permission_mode=mode,
        net_policy=policy or {},
    )


def _gate(policy=None, mode="confirm", ws=None):
    return PermissionGate(_config(policy, mode),
                          sandbox=SandboxState(SandboxPolicy(), ws), workspace=ws)


# ---------------------------------------------------------------- 主机名与名单

@pytest.mark.parametrize("url,host", [
    (GOOD, "jwxt.gzasc.edu.cn"),
    ("https://Example.COM:8443/p", "example.com"),
    ("http://user:pw@example.com/", "example.com"),
    ("jwxt.gzasc.edu.cn/kb", "jwxt.gzasc.edu.cn"),      # 无协议:page_parse 的 url_like
    ("", ""),
    ("not a url at all", ""),
    ("https://", ""),
])
def test_host_extraction(url, host):
    assert netguard.host_of(url) == host


def test_the_wildcard_cannot_be_beaten_by_a_lookalike_prefix():
    """`evil-gzasc.edu.cn` 命不中 `*.gzasc.edu.cn`。

    这一条是这类名单唯一真正要防的写法:少了"边界处必须有个点"这一步,
    允许名单就成了一个看着严格、其实一推就开的门。
    """
    assert netguard._match("evil-gzasc.edu.cn", "*.gzasc.edu.cn") is False
    assert netguard._match("notgzasc.edu.cn", "*.gzasc.edu.cn") is False
    assert netguard._match("gzasc.edu.cn", "*.gzasc.edu.cn") is True
    assert netguard._match("www.gzasc.edu.cn", "*.gzasc.edu.cn") is True
    assert netguard._match("WWW.GzAsc.Edu.CN", "*.gzasc.edu.cn") is True   # 大小写


# ---------------------------------------------------------------- 三档语义

def test_off_is_byte_for_byte_today():
    """默认档:什么都不问什么都不拒 —— 关掉开关后必须与今天逐字一样。"""
    cfg = _config()
    assert netguard.decide(cfg, EVIL) == (netguard.ALLOW, "evil.example.org")
    assert netguard.note(cfg) == ""


def test_ask_mode_asks_for_anything_not_on_the_list():
    cfg = _config({"mode": "ask", "allow": ["*.gzasc.edu.cn"]})
    assert netguard.decide(cfg, GOOD)[0] == netguard.ALLOW
    assert netguard.decide(cfg, EVIL)[0] == netguard.ASK


def test_deny_list_beats_allow_list():
    """同一条域既在 allow 又在 deny:拒。加严的那一份永远赢。"""
    cfg = _config({"mode": "ask", "allow": ["a.com"], "deny": ["a.com"]})
    assert netguard.decide(cfg, "https://a.com/x")[0] == netguard.REJECT


def test_deny_mode_refuses_what_is_not_allowed():
    cfg = _config({"mode": "deny", "allow": ["*.gzasc.edu.cn"]})
    assert netguard.decide(cfg, GOOD)[0] == netguard.ALLOW
    assert netguard.decide(cfg, EVIL)[0] == netguard.REJECT


def test_an_unreadable_host_still_asks_instead_of_passing():
    """主机名解不出来时不许静默放行:那正是一条"看不见的东西都放过"的缝。"""
    cfg = _config({"mode": "ask", "allow": ["a.com"]})
    verdict, host = netguard.decide(cfg, "javascript:alert(1)")
    assert verdict == netguard.ASK and host == ""


# ---------------------------------------------------------------- 配置清洗

@pytest.mark.parametrize("raw", [
    None, "ask", 7, [],                                  # 不是字典
    {"mode": "aske"}, {"mode": ""}, {},                  # 档名写错/缺失
    {"mode": "ALLOW_ALL"},                               # 大小写认,但值不在三种里
])
def test_garbage_policy_means_no_policy_not_the_strictest_one(raw):
    """写错一个字母就退回"没有策略",而不是退回最严的那一档。

    退回最严 = 这台机器的联网整个关掉,而 config.json 里看不出哪里错了 ——
    比"没生效"难查得多。
    """
    assert _net_policy_or_empty(raw) == {}


def test_mode_case_is_tolerated_and_junk_list_items_dropped():
    got = _net_policy_or_empty({"mode": " ASK ", "allow": ["*.A.com", "  ", 5],
                                "deny": "x.com", "extra": 1})
    assert got == {"mode": "ask", "allow": ["*.a.com"]}   # 名单统一小写;非列表丢掉


# ---------------------------------------------------------------- 哪些工具归它管

@pytest.mark.parametrize("name", ["bash", "py_script", "read_file", "write_file",
                                  "web_search", "get_weather", "campus_notices",
                                  "mcp", "library_search"])
def test_tools_outside_its_reach_are_not_touched(name):
    """**这条是"不许吹"的那一半**:命令里 curl 到任何域,这一层管不着。

    参数里塞一个 url 也不算数 —— 只有名单里那三只、且从它们真正的参数名读。
    """
    cfg = _config({"mode": "deny", "allow": []})
    assert netguard.gate(cfg, name, {"url": EVIL}) == (netguard.ALLOW, "")


def test_the_three_covered_tools_read_their_real_parameter_names():
    """键名与注册表里的真实签名对账。

    手写一份参数名名单最容易坏的形状是"拼错了但永远匹配不上"—— 于是策略
    看起来配好了,实际一格都没生效,而且一句报错都没有。
    """
    reg = {t.name: t for t in build_default_registry()}
    for name, key in netguard.URL_TOOLS.items():
        assert name in reg, f"{name} 这只工具不存在了,策略名单该跟着退"
        props = reg[name].parameters.get("properties") or {}
        assert key in props, f"{name} 的参数里没有 {key}(真实:{list(props)})"
    assert set(netguard.URL_TOOLS) == {"web_crawl", "page_parse", "browser_open"}


@pytest.mark.parametrize("name", ["explore·web_crawl", "implement·page_parse"])
def test_subagent_tool_prefix_does_not_hide_it(name):
    """子代理的工具名带档位前缀,取末段判断 —— 漏了这一手等于子代理不受策略。"""
    cfg = _config({"mode": "deny", "allow": ["*.gzasc.edu.cn"]})
    assert netguard.gate(cfg, name, {"url": EVIL, "url_like": EVIL})[0] == netguard.REJECT


# ---------------------------------------------------------------- 权限门里那一格

def test_gate_asks_in_ask_mode_and_says_which_host(ws_path):
    reg = {t.name: t for t in build_default_registry()}
    gate = _gate({"mode": "ask", "allow": ["*.gzasc.edu.cn"]}, ws=ws_path)
    assert gate.check(reg["browser_open"], {"url": EVIL}) == ASK
    assert gate._ask_kind == "net"
    assert "evil.example.org" in gate._ask_note


def test_gate_rejects_in_deny_mode_and_gives_a_reason(ws_path):
    reg = {t.name: t for t in build_default_registry()}
    gate = _gate({"mode": "deny", "allow": ["*.gzasc.edu.cn"]}, ws=ws_path)
    assert gate.check(reg["web_crawl"], {"url": EVIL}) == REJECT
    assert "出站策略" in gate.reject_reason and "evil.example.org" in gate.reject_reason
    assert gate.check(reg["web_crawl"], {"url": GOOD}) != REJECT


def test_full_mode_still_asks_because_this_is_not_a_mode_exception(ws_path):
    """`full` 的承诺是"不再问档位该问的",而这一条是用户**另设**的策略。

    与凭据文件同一口径:那条红线在 full 档照样拦,不是因为档位忘了生效,
    是因为它管的不是"自主度"这一维。
    """
    reg = {t.name: t for t in build_default_registry()}
    gate = _gate({"mode": "ask", "allow": []}, mode="full", ws=ws_path)
    assert gate.check(reg["browser_open"], {"url": EVIL}) == ASK


def test_this_kind_is_never_rememberable(ws_path):
    """那颗「以后都允许」不许出现在这一问上:域名不属于「只收窄」的那两种形状。"""
    gate = _gate({"mode": "ask", "allow": []}, ws=ws_path)
    assert gate.can_remember("browser_open", {"url": EVIL}, "net") is False


def test_off_mode_leaves_the_gate_exactly_as_today(ws_path):
    """对照:同一只工具、同一个档位,没配策略时结论与今天一样。"""
    reg = {t.name: t for t in build_default_registry()}
    plain = _gate(None, ws=ws_path)
    assert plain.check(reg["browser_open"], {"url": EVIL}) == ASK  # 本来就问(写类工具)
    assert plain._ask_kind != "net"


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def ws_path(tmp_path):
    d = tmp_path / "ws"
    d.mkdir(exist_ok=True)
    return d
