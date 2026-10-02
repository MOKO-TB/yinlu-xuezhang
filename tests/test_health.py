# -*- coding: utf-8 -*-
"""一键体检(v0.9.89):每一行都必须由一次真调用撑起来,查不成一律写 unknown。

这一屏存在的意义是"用户遇到不工作时能自助定位",所以最危险的失效不是崩,
而是**装成正常**:一项探针抛了却回 ok、深检没跑却显示数字、面板里凭空多一个
"一键批准白名单"——这三种都只会让人更相信错的结论。下面的针按这三条摆。
"""

import subprocess
from pathlib import Path

import pytest

from minicode import health
from minicode.config import BUILTIN_PROVIDER_ID

ROOT = Path(__file__).resolve().parent.parent


class FakeProvider:
    def __init__(self, pid="p1", name="本机", base_url="https://x.test/v1", api_key="sk-x"):
        self.id, self.name, self.base_url, self.api_key = pid, name, base_url, api_key


class FakeConfig:
    def __init__(self, key=True, mode="confirm", provider=None, base_url="https://x.test/v1"):
        self.permission_mode = mode
        self.api_key_configured = key
        self.model = "glm-4"
        self.base_url = base_url
        self.provider = provider if provider is not None else FakeProvider()


class FakeSpec:
    """替 `agent.gate.project` 那一份装载结果(只给 health 读它真的读的那几个键)。"""

    def __init__(self, rules=(), problems=(), trusted=False, reason="", sha="ab" * 32):
        self.rules, self.problems = list(rules), list(problems)
        self.trusted, self.reason, self.sha = trusted, reason, sha

    @property
    def effective(self):
        return bool(self.rules) and self.trusted


def rows_of(rep, group=None):
    out = []
    for g in rep["groups"]:
        if group and g["key"] != group:
            continue
        out += g["rows"]
    return out


def by_key(rep, key):
    for r in rows_of(rep):
        if r["key"] == key:
            return r
    raise AssertionError(f"表里没有 {key} 这一行:{[r['key'] for r in rows_of(rep)]}")


def make(workspace=None, config=None, project=None, mcp_rows=None, **kw):
    """默认 repo=False:测试绝不真去跑那四支脚本(慢,而且改一次文档就红一片)。"""
    kw.setdefault("repo", False)
    return health.report(workspace=workspace if workspace is not None else ROOT,
                         config=config, project=project, mcp_rows=mcp_rows, **kw)


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """把 REPO_ROOT 钉到一份假仓库(scripts/check_docs.py 在、dist 在)。

    真仓库里 `dist/` 在不在是"上次打没打过包"的事,不该让这四条针跟着变绿变红 ——
    彩排时就是在没有 dist 的干净工作树里红的(CI 上也一样)。
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check_docs.py").write_text("# 占位\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    monkeypatch.setattr(health, "REPO_ROOT", tmp_path)
    return tmp_path


def deep_report(fake_repo, **kw):
    """深检那一档的公共调用:三个探针都给"能连上"的假象,gate 由用例自己给。"""
    kw.setdefault("probe", lambda *a: (200, []))
    kw.setdefault("browser_health", lambda: {"ok": True})
    kw.setdefault("startup", lambda: {})
    return health.report(workspace=fake_repo, config=FakeConfig(), project=FakeSpec(),
                         mcp_rows=[], deep=True, repo=True, **kw)


# ---------------------------------------------------------------- 表的结构


def test_local_only_shape_is_eight_rows_and_no_network_group():
    """开面板就该看到的八行(毫秒级、一个都不敲网络)。

    v0.9.96 加了第八行「注入防御留痕」—— 它读的是本机一份 ≤500 行的日志,
    与深检那三条(问网络/起浏览器/查快捷方式)不是一类成本,所以留在 local 组。
    """
    rep = make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[])
    assert rep["ok"] is True and rep["deep"] is False
    assert [r["key"] for r in rows_of(rep, "local")] == [
        "form", "dirs", "workspace", "modelcfg", "permission", "mcp", "library",
        "injection"]
    assert rows_of(rep, "deep") == []          # 没点深检就不许有联网那几行
    assert rows_of(rep, "repo") == []
    assert rep["total"] == 8


def test_empty_groups_render_nothing_and_badges_come_from_backend():
    rep = make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[])
    for r in rows_of(rep):
        assert r["badge"] == health.BADGE[r["status"]]
        assert r["group"] in {k for k, _l, _i in health.GROUPS}


def test_verdict_names_the_bad_row_and_counts_match():
    rep = make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[])
    assert rep["summary"] == {k: sum(1 for r in rows_of(rep) if r["status"] == k)
                              for k in health.BADGE}
    bad = make(config=FakeConfig(key=False), project=FakeSpec(), mcp_rows=[])
    assert "有问题" in bad["verdict"] and "模型配置" in bad["verdict"]
    assert by_key(bad, "modelcfg")["status"] == health.BAD


def test_a_probe_that_throws_is_unknown_and_never_ok():
    """探针自己崩了只能报"没查成":把异常吞掉再回 ok 是这张表最坏的失效。"""
    def boom():
        raise RuntimeError("PowerShell 起不来")
    rep = make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[], deep=True,
               probe=lambda *a: (200, []), browser_health=lambda: {"ok": True},
               startup=boom)
    row = by_key(rep, "startup")
    assert row["status"] == health.UNKNOWN
    assert "PowerShell 起不来" in row["detail"]
    assert "没查成" in rep["verdict"] and "没查成不等于没问题" in rep["verdict"]


# ---------------------------------------------------------------- 本机各项口径


def test_unwritable_data_dir_is_bad_and_says_what_that_looks_like(monkeypatch):
    """写不进去的唯一表现是"配了不保存"(当时有效、重启回去),fix 里必须这么写。"""
    from minicode import paths
    monkeypatch.setattr(paths, "diagnostics", lambda: {
        "app_dir": "C:/Program Files/引路学长", "config": "C:/x/config.json",
        "backend": "exe", "frozen": True, "writable": False})
    rep = make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[])
    row = by_key(rep, "dirs")
    assert row["status"] == health.BAD and "重启又变回去" in row["fix"]
    assert "Program Files" in row["fix"]


def test_missing_workspace_is_bad_with_a_way_back():
    rep = make(workspace=ROOT / "没有这个目录-真的", config=FakeConfig(),
               project=FakeSpec(), mcp_rows=[])
    row = by_key(rep, "workspace")
    assert row["status"] == health.BAD and "重新选" in row["fix"]


def test_no_api_key_points_at_the_dialog_that_already_exists():
    rep = make(config=FakeConfig(key=False), project=FakeSpec(), mcp_rows=[])
    row = by_key(rep, "modelcfg")
    assert row["status"] == health.BAD
    assert row["action"] == {"kind": "models", "label": "去配置模型"}


def test_builtin_provider_keeps_its_internal_address_off_the_panel():
    """与 get_info 同一条口径:内置供应商的内网地址不下发给界面。"""
    cfg = FakeConfig(provider=FakeProvider(pid=BUILTIN_PROVIDER_ID,
                                          base_url="http://10.0.0.9:8000/v1"))
    rep = make(config=cfg, project=FakeSpec(), mcp_rows=[])
    assert "10.0.0.9" not in by_key(rep, "modelcfg")["detail"]


def test_allowlist_states_are_four_different_things():
    cases = {
        # 没有文件:正常,并且明说"无"
        "none": (FakeSpec(reason="这个项目没有 permissions.json"), health.OK, "项目白名单:无"),
        # 写了但没批准:要注意 + 那条唯一真路(回对话里过确认框)
        "untrusted": (FakeSpec(rules=[object()], reason="没核对批准过"), health.WARN, "未生效"),
        # 批准过又改了文件:同样未生效,但原因要说清是哈希对不上
        "drift": (FakeSpec(rules=[object()], reason="批准之后文件又改过了(哈希对不上)"),
                  health.WARN, "哈希对不上"),
        # 生效:正常,并写出几条免确认
        "live": (FakeSpec(rules=[object()], trusted=True), health.OK, "已生效"),
    }
    for name, (spec, want, needle) in cases.items():
        row = by_key(make(config=FakeConfig(), project=spec, mcp_rows=[]), "permission")
        assert row["status"] == want, name
        assert needle in row["detail"], (name, row["detail"])


def test_untrusted_allowlist_offers_no_shortcut_past_the_confirm_dialog():
    """面板上不许存在"点一下就批准":那条点头只能发生在确认框里(v0.9.87 的设计核心)。"""
    row = by_key(make(config=FakeConfig(),
                      project=FakeSpec(rules=[object()], reason="没核对批准过"),
                      mcp_rows=[]), "permission")
    assert row["action"]["kind"] == "ask"
    assert "确认框" in row["action"]["text"]
    src = (ROOT / "minicode" / "health.py").read_text(encoding="utf-8")
    # 硬证:这个模块根本没有能力自己批准 —— 它既不装载白名单,也不写信任库
    assert "permspec" not in src, "health 里出现 permspec = 打算自己 trust/revoke"


def test_permission_row_refuses_to_load_the_file_itself():
    """没递来装载结果时宁可 unknown,也不第二次 load:两份装载就是两份真相。"""
    from minicode import permspec

    def no_second_load(*a, **k):
        raise AssertionError("health 不该自己再装载一次 permissions.json")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(permspec, "load", no_second_load)
    try:
        row = by_key(make(config=FakeConfig(), project=None, mcp_rows=[]), "permission")
    finally:
        monkey.undo()
    assert row["status"] == health.UNKNOWN and "没递进来" in row["detail"]


def test_mcp_rows_distinguish_not_configured_from_down():
    assert by_key(make(config=FakeConfig(), project=FakeSpec(),
                       mcp_rows=[]), "mcp")["status"] == health.SKIP
    assert by_key(make(config=FakeConfig(), project=FakeSpec(),
                       mcp_rows=None), "mcp")["status"] == health.UNKNOWN
    down = by_key(make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[
        {"label": "fs", "connected": True, "n_tools": 12, "enabled": True},
        {"label": "浏览器", "connected": False, "n_tools": 0, "enabled": True}]), "mcp")
    assert down["status"] == health.WARN
    assert "fs(12 工具)" in down["detail"] and "浏览器" in down["detail"]
    assert "整组消失" in down["fix"]


def test_library_missing_is_normal_for_source_runs_and_a_fault_for_installed_ones(monkeypatch):
    from minicode import library
    monkeypatch.setattr(library, "catalog_health",
                        lambda ws=None: {"status": "missing", "path": None})
    assert by_key(make(config=FakeConfig(), project=FakeSpec(),
                       mcp_rows=[]), "library")["status"] == health.SKIP


def test_the_same_missing_library_is_a_fault_in_an_installed_build(monkeypatch):
    from minicode import library, paths
    monkeypatch.setattr(library, "catalog_health",
                        lambda ws=None: {"status": "missing", "path": "D:/x/library.db"})
    monkeypatch.setattr(paths, "diagnostics", lambda: {
        "app_dir": "D:/ylxz/引路学长", "config": "D:/ylxz/config.json",
        "backend": "exe", "frozen": True, "writable": True})
    row = by_key(make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[]), "library")
    assert row["status"] == health.BAD
    assert "重新" in row["fix"] and "check_library_db.py" in row["fix"]


def test_library_ok_row_carries_the_numbers_it_actually_read(monkeypatch):
    from minicode import library
    monkeypatch.setattr(library, "catalog_health", lambda ws=None: {
        "status": "ok", "path": "D:/x/library.db", "size": 1_310_000_000,
        "total": 1331774})
    row = by_key(make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[]), "library")
    assert row["status"] == health.OK
    assert "1331774 条书目" in row["detail"] and "1.31 GB" in row["detail"]


def test_library_incomplete_reports_both_sizes(monkeypatch):
    from minicode import library
    monkeypatch.setattr(library, "catalog_health", lambda ws=None: {
        "status": "incomplete", "path": "D:/x/library.db", "size": 400_000_000,
        "expected": 1_310_000_000, "count": 1331774})
    row = by_key(make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[]), "library")
    assert row["status"] == health.BAD
    assert "0.40 GB" in row["detail"] and "应为 1.31 GB" in row["detail"]


# ---------------------------------------------------------------- 深检项


def test_deep_only_runs_when_asked():
    calls = []
    make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[],
         probe=lambda *a: calls.append(1) or (200, []),
         browser_health=lambda: calls.append(1) or {"ok": True},
         startup=lambda: calls.append(1) or {})
    assert calls == []


def test_model_probe_maps_http_status_to_three_different_verdicts():
    def run(status):
        return by_key(make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[],
                           deep=True, probe=lambda b, k, t: (status, ["a", "b"]),
                           browser_health=lambda: {"ok": True}, startup=lambda: {}),
                      "modelnet")

    ok = run(200)
    assert ok["status"] == health.OK and "列出 2 个模型" in ok["detail"]
    # 404 也算活着:这家没实现 /models,但网络与服务都是通的
    assert run(404)["status"] == health.OK
    assert run(401)["status"] == health.BAD
    assert run(401)["action"]["kind"] == "models"
    assert run(429)["status"] == health.WARN


def test_404_when_no_models_listed_still_says_the_network_is_fine():
    row = by_key(make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[], deep=True,
                      probe=lambda *a: (404, []),
                      browser_health=lambda: {"ok": True}, startup=lambda: {}),
                 "modelnet")
    assert row["status"] == health.OK and "没实现" in row["detail"]


def test_browser_and_startup_rows_carry_their_own_words():
    rep = make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[], deep=True,
               probe=lambda *a: (200, ["m"]),
               browser_health=lambda: {"ok": False, "browser": "", "mode": "",
                                       "detail": "没找到 Edge/Chrome"},
               startup=lambda: {"supported": True, "installed": True, "ours": False,
                                "target": "D:/旧位置/引路学长.exe", "lnk": "启动/x.lnk"})
    assert by_key(rep, "browser")["status"] == health.BAD
    assert "YINLUXS_BROWSER" in by_key(rep, "browser")["fix"]
    wrong = by_key(rep, "startup")
    assert wrong["status"] == health.WARN and "旧位置" in wrong["detail"]


def test_startup_off_is_a_choice_not_a_fault():
    rep = make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[], deep=True,
               probe=lambda *a: (0, []), browser_health=lambda: {"ok": True},
               startup=lambda: {"supported": False})
    assert by_key(rep, "startup")["status"] == health.SKIP
    assert by_key(rep, "modelnet")["status"] == health.BAD      # 连不上服务是硬故障


# ---------------------------------------------------------------- 四道闸


def test_gate_group_only_appears_in_a_source_repo_and_only_under_deep(fake_repo):
    """钉在假仓库上:真仓库里 `dist/` 在不在是"上次打没打过包"的事,
    跟着它走会让同一条针在打包过的机器绿、在干净工作树/CI 上红(彩排时就是这么红的)。
    """
    rep = deep_report(fake_repo, gate=lambda root, name, args, timeout: (0, "全绿"))
    assert [r["key"] for r in rows_of(rep, "repo")] == [
        "gate_docs", "gate_js", "gate_db", "gate_frozen"]
    assert all(r["status"] == health.OK for r in rows_of(rep, "repo"))
    assert "退出码 0" in by_key(rep, "gate_docs")["detail"]
    # 同一棵树,不点「深检」就没有那四行(打开面板不该先卡一分多钟)
    assert rows_of(make(config=FakeConfig(), project=FakeSpec(), mcp_rows=[]), "repo") == []


def test_a_failing_gate_shows_the_scripts_own_words_verbatim():
    rep = health.report(workspace=ROOT, config=FakeConfig(), project=FakeSpec(),
                        mcp_rows=[], deep=True, repo=True,
                        probe=lambda *a: (200, []), browser_health=lambda: {"ok": True},
                        startup=lambda: {},
                        gate=lambda root, name, args, timeout:
                        (1, "HANDOFF §2 留了 6 轮里程碑(上限 5)"))
    row = by_key(rep, "gate_docs")
    assert row["status"] == health.BAD
    assert "留了 6 轮" in row["detail"]
    assert "python scripts/check_docs.py --collect" in row["fix"]


def test_a_hanging_gate_is_reported_as_not_checked():
    def slow(root, name, args, timeout):
        raise subprocess.TimeoutExpired(cmd="check_docs.py", timeout=timeout)
    rep = health.report(workspace=ROOT, config=FakeConfig(), project=FakeSpec(),
                        mcp_rows=[], deep=True, repo=True, probe=lambda *a: (200, []),
                        browser_health=lambda: {"ok": True}, startup=lambda: {}, gate=slow)
    assert by_key(rep, "gate_docs")["status"] == health.UNKNOWN
    assert "没等它" in by_key(rep, "gate_docs")["detail"]


def test_missing_node_is_not_reported_as_a_frontend_bug():
    rep = health.report(workspace=ROOT, config=FakeConfig(), project=FakeSpec(),
                        mcp_rows=[], deep=True, repo=True, probe=lambda *a: (200, []),
                        browser_health=lambda: {"ok": True}, startup=lambda: {},
                        gate=lambda root, name, args, timeout:
                        (1, "找不到 node:命令未找到") if name == "check_frontend_js.py" else (0, ""))
    js = by_key(rep, "gate_js")
    assert js["status"] == health.UNKNOWN
    assert "没装 Node" in js["detail"] and "别当故障" in js["fix"]
    assert by_key(rep, "gate_docs")["status"] == health.OK


def test_frozen_gate_skips_when_there_is_nothing_to_check(tmp_path):
    rows = health._check_gates(tmp_path, lambda *a: (0, ""), has_dist=False)
    frozen = [r for r in rows if r["key"] == "gate_frozen"][0]
    assert frozen["status"] == health.SKIP and "出包后" in frozen["detail"]
    assert len(rows) == len(health.GATE_SCRIPTS)


def test_run_gate_reads_utf8_output_from_the_script(tmp_path):
    script = tmp_path / "scripts"
    script.mkdir()
    (script / "tiny.py").write_text(
        "import sys\nsys.stdout.reconfigure(encoding='utf-8')\n"
        "print('漂移')\nsys.exit(1)\n", encoding="utf-8")
    code, tail = health._run_gate(tmp_path, "tiny.py", (), 30)
    assert code == 1 and "漂移" in tail


# ---------------------------------------------------------------- 接线与界面


class _StubBridge:
    """只递 Bridge.get_health 真读的那几样:workspace / config / agent.gate.project / _mcp_summary。"""

    def __init__(self, project=FakeSpec(rules=[object()], trusted=True), mcp=None):
        from types import SimpleNamespace
        self.workspace = ROOT
        self.config = FakeConfig()
        self.agent = SimpleNamespace(gate=SimpleNamespace(project=project))
        self._mcp = mcp if mcp is not None else []

    def _mcp_summary(self):
        return self._mcp


def _html() -> str:
    return (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def _hd_js() -> str:
    """从 index.html 里取出体检那段**脚本**(CSS 注释里也写着同一句版本号,不能拿它当起点)。"""
    html = _html()
    start = html.index("const hdApi")
    return html[start:html.index("两个记录入口", start)]


def test_injection_row_reports_counts_and_points_at_the_file():
    row = by_key(make(injection_stats=lambda: {
        "readable": True, "events": 3, "last_at": "2026-09-30T10:00:00+08:00",
        "gates": ["credential", "project_allow"]}), "injection")
    assert row["status"] == "ok"
    assert "3 次" in row["detail"] and "credential" in row["detail"]
    assert "injection-events.log" in row["detail"]      # 明细在哪要说得出地方
    assert "2026-09-30 10:00:00" in row["detail"]        # 时刻照人话写,不露 T


def test_injection_row_zero_says_it_does_not_prove_the_defense_works():
    """0 条只说明"没触发过",不能写成"防线正常"——那是没有来源的结论。"""
    row = by_key(make(injection_stats=lambda: {
        "readable": True, "events": 0, "gates": [], "last_at": ""}), "injection")
    assert "0 条" in row["detail"]
    assert "不代表防线" in row["detail"]


def test_injection_row_unreadable_is_unknown_not_ok():
    """文件在但读不出 = 没查成。报 ok 等于对着用户谎称"这台机器没触发过敏感动作"。"""
    row = by_key(make(injection_stats=lambda: {
        "readable": False, "events": 0, "gates": [], "last_at": ""}), "injection")
    assert row["status"] == "unknown"
    assert "不是没记" in row["detail"]


def test_injection_row_is_in_the_local_group():
    """这一行开面板就要看到(毫秒级读一个 ≤500 行的文件),不该藏在深检里。"""
    row = next(r for r in rows_of(make(injection_stats=lambda: {
        "readable": True, "events": 0, "gates": [], "last_at": ""}), group="local")
        if r["key"] == "injection")
    assert row["group"] == "local"


# ------------------------------------------------ 无窗口出口(python -m minicode.health)
