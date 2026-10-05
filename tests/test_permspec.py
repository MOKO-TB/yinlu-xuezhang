"""项目级权限白名单(v0.9.87)的测试。

钉的是三件事,顺序就是重要性:
① **红线永远在白名单前面** —— 白名单声称允许读凭据文件、允许工作区外的绝对路径,
   也必须该确认确认、该拒收拒收;
② **没核对批准过就一条都不生效**,批准的是**内容指纹**(改一个字自动失效);
③ 生效时**留痕**(理由要能被 Agent 读到,它要写进安全日志并推一条 info)。
"""
import json
from pathlib import Path

import pytest

from minicode import permspec
from minicode.config import Config, Provider
from minicode.permission import ALLOW, ASK, PermissionGate
from minicode.tools.base import Tool, ToolContext
from minicode.tools.registry import build_default_registry

TOOLS = {t.name.rsplit("·", 1)[-1] for t in build_default_registry()}


@pytest.fixture
def trust_file(tmp_path, monkeypatch):
    """把信任库指到临时目录 —— 绝不让测试写到用户真实的 perms-trust.json。"""
    path = tmp_path / "perms-trust.json"
    monkeypatch.setattr(permspec, "trust_path", lambda: path)
    return path


def _workspace(tmp_path, rules=None, raw=None):
    ws = tmp_path / "proj"
    ws.mkdir(parents=True, exist_ok=True)
    body = raw if raw is not None else json.dumps({"allow": rules or []},
                                                  ensure_ascii=False)
    (ws / "permissions.json").write_text(body, encoding="utf-8")
    return ws


def _tool(name, read_only=False):
    return Tool(name=name, description="", parameters={"type": "object"},
                handler=lambda **kw: "", read_only=read_only)


def _gate(ws, **kw):
    """带上真实 SandboxState —— 凭据/越界那几层没有沙箱对象是不参与判断的,
    不带沙箱测出来的"放行"是假结论(第一版就差点这么放过 .env)。"""
    from minicode.sandbox import SandboxPolicy, SandboxState
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x",
                                     api_key="k", model="m")],
                 active_provider_id="t", permission_mode=kw.pop("mode", "confirm"))
    kw.setdefault("sandbox", SandboxState(SandboxPolicy(), Path(ws)))
    return PermissionGate(cfg, workspace=ws, tool_names=TOOLS, **kw)


# ------------------------------------------------------------------ 装载与校验

def test_unknown_tool_rule_is_dropped_with_a_reason():
    rules, problems = permspec.parse_rules(
        [{"tool": "no_such_tool", "path": "a.txt"}], TOOLS)
    assert rules == [] and any("no_such_tool" in p for p in problems)


def test_rule_must_give_exactly_one_pattern():
    rules, problems = permspec.parse_rules(
        [{"tool": "write_file"}, {"tool": "write_file", "path": "a", "path_prefix": "b"}],
        TOOLS)
    assert rules == [] and len(problems) == 2


def test_redline_patterns_are_refused_at_load_time():
    """绝对路径 / .. 上跳 / 敏感目录:装载时就拒,不留到运行时赌红线挡得住。"""
    rules, problems = permspec.parse_rules([
        {"tool": "read_file", "path": "C:/Windows/win.ini"},   # 绝对路径
        {"tool": "read_file", "path_prefix": "../../etc"},
        {"tool": "read_file", "path_prefix": ".ssh"},
    ], TOOLS)
    assert rules == []
    assert len(problems) == 3
    assert any("绝对路径" in p for p in problems)
    assert any("上跳" in p for p in problems)
    assert any("敏感目录" in p for p in problems)
    # 顺带钉住:可预授权的首词就是那份构建类名单,`rm` 进不来
    r2, pr2 = permspec.parse_rules([{"tool": "bash", "command_prefix": "rm -rf build"}],
                                    TOOLS)
    assert r2 == [] and any("不是可预授权" in x for x in pr2)


def test_command_head_is_shared_with_the_build_command_gate():
    """首词口径只有一份:权限门与 command_prefix 必须认得同一个词。"""
    assert permspec.command_head('pytest -q tests') == "pytest"
    assert permspec.command_head('"D:\\a\\python.EXE" -m pytest') == "python"
    rules, _ = permspec.parse_rules(
        [{"tool": "bash", "command_prefix": "pytest"}], TOOLS)
    assert rules[0].pattern == "pytest"
    assert rules[0].matches("bash", {"command": "pytest -q"})
    assert not rules[0].matches("bash", {"command": "rm -rf build"})


def test_path_matching_is_relative_only():
    rules, _ = permspec.parse_rules(
        [{"tool": "write_file", "path_prefix": "notes/"}], TOOLS)
    r = rules[0]
    assert r.matches("write_file", {"path": "notes/a.md"})
    assert r.matches("write_file", {"path": "notes\\sub\\b.md"})   # 反斜杠同样归一
    assert not r.matches("write_file", {"path": "other/a.md"})
    assert not r.matches("write_file", {"path": "/etc/passwd"})     # 绝对路径不匹配
    assert not r.matches("write_file", {"path": "../outside.txt"})  # 上跳不匹配
    assert not r.matches("read_file", {"path": "notes/a.md"})       # 工具不对


def test_missing_or_broken_file_explains_itself(tmp_path):
    ws = tmp_path / "empty"
    ws.mkdir()
    spec = permspec.load(ws, TOOLS)
    assert not spec.effective and "没有 permissions.json" in spec.reason
    broken = _workspace(tmp_path / "b", raw="{不是 json")
    spec2 = permspec.load(broken, TOOLS)
    assert not spec2.effective and "解析失败" in spec2.reason


# ---------------------------------------------------------------- 批准与失效

def test_untrusted_file_grants_nothing(tmp_path, trust_file):
    ws = _workspace(tmp_path, [{"tool": "write_file", "path": "notes/a.md"}])
    spec = permspec.load(ws, TOOLS)
    assert spec.rules and not spec.trusted and "没核对批准过" in spec.reason


def test_trust_follows_the_content_hash(tmp_path, trust_file):
    ws = _workspace(tmp_path, [{"tool": "write_file", "path": "notes/a.md"}])
    spec = permspec.load(ws, TOOLS)
    permspec.trust(ws, spec.sha, len(spec.rules))
    assert permspec.load(ws, TOOLS).effective
    # 改一个字(多加一条规则)→ 指纹变了 → 整份失效
    (ws / "permissions.json").write_text(
        json.dumps({"allow": [{"tool": "write_file", "path": "notes/a.md"},
                              {"tool": "bash", "command_prefix": "rm"}]}),
        encoding="utf-8")
    after = permspec.load(ws, TOOLS)
    assert not after.effective and "改过" in after.reason
    assert permspec.revoke(ws) is True
    assert not permspec.load(ws, TOOLS).effective
    assert permspec.revoke(ws) is False        # 再撤销不炸,照实说没批准过


def test_decide_names_the_rule_it_hit(tmp_path, trust_file):
    ws = _workspace(tmp_path, [{"tool": "bash", "command_prefix": "echo"},
                               {"tool": "write_file", "path": "notes/a.md"}])
    spec = permspec.load(ws, TOOLS)
    permspec.trust(ws, spec.sha, 2)
    spec = permspec.load(ws, TOOLS)
    hit = spec.decide("write_file", {"path": "notes/a.md"})
    assert "第 2 条" in hit and "免确认" not in hit      # 说清是哪条,不吹后果
    assert spec.decide("write_file", {"path": "elsewhere.md"}) == ""


# ------------------------------------------------------------ 权限门:红线优先

def test_gate_allows_a_trusted_rule_at_the_confirm_step(tmp_path, trust_file):
    ws = _workspace(tmp_path, [{"tool": "write_file", "path": "notes/a.md"}])
    spec = permspec.load(ws, TOOLS)
    permspec.trust(ws, spec.sha, 1)
    gate = _gate(ws)
    assert gate.check(_tool("write_file"), {"path": "notes/a.md"}) == ALLOW
    assert "第 1 条" in gate.project_allow_reason        # 理由要能被 Agent 读到并留痕


def test_gate_leaves_an_untrusted_rule_asking(tmp_path, trust_file):
    ws = _workspace(tmp_path, [{"tool": "write_file", "path": "notes/a.md"}])
    assert _gate(ws).check(_tool("write_file"),
                           {"path": "notes/a.md"}) == ASK


def test_whitelist_never_overrides_a_credential_file(tmp_path, trust_file):
    """白名单说"允许读 .env"也照样要确认 —— 凭据那一层在它前面 return。

    这条是整件事的心跳:配置化的东西如果能把安全门挤掉,那它就不是白名单而是后门。
    """
    ws = _workspace(tmp_path, [{"tool": "read_file", "path": ".env"}])
    (ws / ".env").write_text("API_KEY=x", encoding="utf-8")
    spec = permspec.load(ws, TOOLS)
    assert spec.rules, "装载阶段不该拦这条(它是个正常相对路径)"
    permspec.trust(ws, spec.sha, len(spec.rules))
    gate = _gate(ws)
    assert gate.check(_tool("read_file"), {"path": ".env"}) == ASK
    assert gate.project_allow_reason == ""


def test_whitelist_does_not_touch_workspace_outside_paths(tmp_path, trust_file):
    """工作区外的路径:规则里写绝对路径装载就被拒,运行时也就无从免确认。"""
    rules, problems = permspec.parse_rules(
        [{"tool": "read_file", "path": str(tmp_path / "outside.txt")}], TOOLS)
    assert rules == [] and any("绝对路径" in p for p in problems)


def test_readonly_tools_are_allowed_before_the_whitelist_matters(tmp_path, trust_file):
    """只读工具本来就直接放行,白名单不该改变它(也不该被用来"撤销"放行)。"""
    ws = _workspace(tmp_path, [])
    assert _gate(ws).check(_tool("grep", read_only=True),
                           {"pattern": "x"}) == ALLOW


def test_disaster_command_still_hard_rejected(tmp_path, trust_file):
    """首词命中白名单,但命令里混了一段灾难删除 —— 整条仍硬拒,不免确认。

    `rm -rf /` 这种目标在 `check_disaster_command` 的黑名单里(它按 ; & | 换行
    逐段查),所以批准过 `pytest` 也救不了一条 `pytest; rm -rf /`。
    """
    from minicode.permission import REJECT
    ws = _workspace(tmp_path, [{"tool": "bash", "command_prefix": "pytest"}])
    spec = permspec.load(ws, TOOLS)
    assert len(spec.rules) == 1
    permspec.trust(ws, spec.sha, 1)
    gate = _gate(ws)
    assert gate.check(_tool("bash"), {"command": "pytest -q"}) == ALLOW
    assert gate.project_allow_reason
    assert gate.check(_tool("bash"), {"command": "pytest -q; rm -rf /"}) == REJECT


# ------------------------------------------------------------------ 两只工具

def _ctx(ws):
    return ToolContext(workspace=ws, config=None)


def test_permissions_tool_reports_state(tmp_path, trust_file):
    ws = _workspace(tmp_path, [{"tool": "write_file", "path": "notes/a.md"}])
    out = permspec.permissions(_ctx(ws), "show")
    assert "未生效" in out and "没核对批准过" in out
    assert "第 1 条" not in out                       # show 不逐条列
    verify = permspec.permissions(_ctx(ws), "verify")
    assert "notes/a.md" in verify and "sha256" in verify
    assert "错误" in permspec.permissions(_ctx(ws), "explode")


def test_trust_tool_refuses_an_empty_or_broken_list(tmp_path, trust_file):
    ws = _workspace(tmp_path, [{"tool": "no_such", "path": "a"}])
    out = permspec.permissions_trust(_ctx(ws), "trust")
    assert "错误" in out and "不批准空清单" in out
    assert permspec.load(ws, TOOLS).trusted is False


def test_trust_tool_shows_what_is_being_approved(tmp_path, trust_file):
    ws = _workspace(tmp_path, [{"tool": "write_file", "path_prefix": "notes/"},
                               {"tool": "bash", "command_prefix": "pytest"}])
    out = permspec.permissions_trust(_ctx(ws), "trust")
    assert "已批准这 2 条" in out and "改一个字" in out
    assert "免确认" in permspec.permissions(_ctx(ws), "show")
    assert "撤销" in permspec.permissions_trust(_ctx(ws), "revoke")
    assert permspec.permissions_trust(_ctx(ws), "explode").startswith("错误")


def test_no_workspace_is_stated_not_guessed(tmp_path):
    ws = tmp_path / "nowhere"
    spec = permspec.load(ws, TOOLS)
    assert not spec.effective and "没有 permissions.json" in spec.reason


# ---------------------------------------------------------------- deny / ask(v0.9.116)
#
# 这一组钉的不是"能不能写这两种规则",而是**顺序**:拒绝在读只放行之前、
# 强制确认在档位之前。位置错了这两种决定就都是装饰品,而装饰品比没有更糟 ——
# 用户以为自己在文件里写了"必须问我"。

from minicode.permission import REJECT      # noqa: E402


def _trusted(tmp_path, rules):
    ws = _workspace(tmp_path, rules)
    spec = permspec.load(ws, TOOLS)
    permspec.trust(ws, spec.sha, len(spec.rules))
    return ws, spec


def test_decision_defaults_to_allow_so_old_files_are_unchanged():
    rules, problems = permspec.parse_rules(
        [{"tool": "write_file", "path": "notes/a.md"}], TOOLS)
    assert len(rules) == 1 and rules[0].decision == "allow" and problems == []


def test_unknown_decision_drops_the_rule_instead_of_guessing():
    """把 deny 打成 denay 就悄悄当 allow 放行,是这类字段最贵的一种坏法。"""
    rules, problems = permspec.parse_rules(
        [{"tool": "write_file", "path": "a.md", "decision": "denay"}], TOOLS)
    assert rules == [] and any("decision" in x for x in problems)


def test_deny_outranks_allow_on_the_same_target(tmp_path, trust_file):
    ws, _ = _trusted(tmp_path, [
        {"tool": "write_file", "path_prefix": "notes", "decision": "allow"},
        {"tool": "write_file", "path": "notes/secret.md", "decision": "deny"}])
    gate = _gate(ws, mode="accept_all")
    assert gate.check(_tool("write_file"), {"path": "notes/secret.md"}) == REJECT
    assert "第 2 条" in gate.reject_reason, "要指得出是哪条规则拒的"
    # 同目录别的文件仍按 allow 免确认 —— deny 不是"整份作废"
    assert gate.check(_tool("write_file"), {"path": "notes/ok.md"}) == ALLOW


def test_deny_reaches_a_read_only_tool_before_the_readonly_allow(tmp_path, trust_file):
    ws, _ = _trusted(tmp_path, [
        {"tool": "read_file", "path": "notes/private.txt", "decision": "deny"}])
    gate = _gate(ws, mode="full")
    assert gate.check(_tool("read_file", read_only=True),
                      {"path": "notes/private.txt"}) == REJECT


def test_ask_rule_survives_full_mode_and_names_the_door(tmp_path, trust_file):
    ws, _ = _trusted(tmp_path, [
        {"tool": "bash", "command_prefix": "pytest", "decision": "ask"}])
    gate = _gate(ws, mode="full")
    assert gate.check(_tool("bash"), {"command": "pytest -q"}) == ASK
    assert gate.ask_kind == "project_rule", "H3 靠这个字段决定不许谁代答"


def test_untrusted_ask_and_deny_rules_do_nothing(tmp_path, trust_file):
    """攻击面与 allow 同一条:clone 来的仓库带一份 deny 清单,没核对批准就一条不生效
    —— 否则别人可以用"拒绝一切"瘫痪你的工作流,和用"放行一切"开后门一样坏。"""
    ws = _workspace(tmp_path, [
        {"tool": "read_file", "path": "a.txt", "decision": "deny"}])
    gate = _gate(ws, mode="full")
    assert gate.check(_tool("read_file", read_only=True), {"path": "a.txt"}) == ALLOW


def test_deny_may_name_an_unsafe_command_head_while_allow_may_not():
    """首词白名单只管 allow:预授权 rm 会开后门,拒绝 rm 不会。"""
    rules, problems = permspec.parse_rules([
        {"tool": "bash", "command_prefix": "rm", "decision": "deny"},
        {"tool": "bash", "command_prefix": "rm", "decision": "allow"}], TOOLS)
    assert [r.decision for r in rules] == ["deny"]
    assert any("不是可预授权" in x for x in problems)


def test_redline_pattern_is_refused_even_in_a_deny_rule():
    """红线类路径写一条拒绝照样装载时拒收:那些本来进不去,留着只会让人误以为是
    这条规则在拦 —— 而规则没批准时它其实什么都没拦。"""
    rules, problems = permspec.parse_rules(
        [{"tool": "read_file", "path": ".ssh/id_rsa", "decision": "deny"}], TOOLS)
    assert rules == [] and any("敏感目录" in x for x in problems)
    assert any("这条写了也不会是它在拦" in x for x in problems)


def test_summary_counts_each_decision(tmp_path, trust_file):
    ws = _workspace(tmp_path, [
        {"tool": "write_file", "path": "a.md"},
        {"tool": "bash", "command_prefix": "pytest", "decision": "ask"},
        {"tool": "read_file", "path": "b.txt", "decision": "deny"}])
    spec = permspec.load(ws, TOOLS)
    out = spec.summary()
    assert "1 条 allow" in out and "1 条 ask" in out and "1 条 deny" in out


def test_trust_says_what_will_still_be_asked(tmp_path, trust_file):
    """批准那一刻给用户的承诺必须与规则一致 —— 旧文案对所有规则都说"不再问你"。"""
    ws, _ = _trusted(tmp_path, [
        {"tool": "bash", "command_prefix": "pytest", "decision": "ask"}])

    class _Ctx:
        workspace = ws

    out = permspec.permissions_trust(_Ctx(), "trust")
    assert "把问题要回来" in out
    assert "不再问你" not in out, "这份清单里一条 allow 都没有"
