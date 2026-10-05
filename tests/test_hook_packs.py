"""内置钩子配方(L3,v0.9.116)的测试。

钉四件事,顺序按重要性:
① preview **一个字都不写**(它是"看一眼",写了就成了未经点头的改动);
② nonce 的几种失败(没有 / 对不上 / 过期 / 用过一次)都不许改到 config.json;
③ apply 成功必须**先备份**且当场生效,写盘失败必须回滚内存那一份 ——
   "内存里挂着、磁盘上没写"这种半吊子状态会让用户下次重启发现钩子没了;
④ 读写切分:hooks 只读、hooks_apply 要确认 —— 合成一只就得二选一,
   要么挂东西不弹框,要么看一眼也弹框。
"""
import json
import time

import pytest

from minicode import hook_packs
from minicode.paths import app_path


@pytest.fixture(autouse=True)
def _clear_previews():
    hook_packs._previews.clear()
    yield
    hook_packs._previews.clear()


@pytest.fixture
def user_config(tmp_path):
    """app 目录里放一份已有的 config.json —— 配方只加到已有配置上,不凭空造。"""
    path = app_path("config.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"permission_mode": "confirm", "hooks": {}}),
                    encoding="utf-8")
    return path


def _nonce(ctx, template):
    """preview 之后从进程里那张表取凭据。

    不去从返回文本里 split:那句人话的结尾是"—— 十分钟有效…",按冒号切取到的是
    半句话而不是凭据(第一版就是这么错的)。文本长什么样由别的测试管。
    """
    hook_packs.preview(ctx, template)
    return next(iter(hook_packs._previews))


def _disk_hooks(path):
    return json.loads(path.read_text(encoding="utf-8")).get("hooks") or {}


def test_list_says_nothing_is_mounted(ctx):
    out = hook_packs.list_line(ctx)
    assert "一条都没挂" in out and "git-audit" in out and "run-tests" in out


def test_preview_unknown_template_offers_the_two(ctx):
    out = hook_packs.preview(ctx, "nope")
    assert out.startswith("错误") and "git-audit" in out and "run-tests" in out


def test_preview_writes_nothing(ctx, user_config):
    out = hook_packs.preview(ctx, "git-audit")
    assert "pre_tool" in out and "hook-audit.log" in out and "凭据" in out
    assert _disk_hooks(user_config) == {}, "preview 不该动配置一个字"
    assert not app_path(hook_packs.AUDIT_LOG).exists(), "preview 也不该开日志"


def test_run_tests_preview_keeps_workspace_at_runtime_and_says_the_risk(ctx):
    out = hook_packs.preview(ctx, "run-tests")
    assert "{workspace}" in out, (
        "工作区要留给运行期填 —— 写死目录的话,用户切了项目钩子还在测老地方")
    assert "执行你工作区里的代码" in out, "这条配方的前提必须摆在预览里"
    assert "hook_run_tests.py" in out


def test_apply_needs_a_matching_live_nonce(ctx, user_config):
    for bad in ("", "0123456789abcdef"):
        assert "错误" in hook_packs.apply(ctx, "git-audit", bad)
    assert _disk_hooks(user_config) == {}


def test_apply_rejects_wrong_template(ctx, user_config):
    nonce = _nonce(ctx, "git-audit")
    assert "错误" in hook_packs.apply(ctx, "run-tests", nonce)
    assert _disk_hooks(user_config) == {}


def test_apply_rejects_expired_nonce(ctx, user_config):
    nonce = _nonce(ctx, "git-audit")
    hook_packs._previews[nonce]["expires"] = time.time() - 1
    assert "过期" in hook_packs.apply(ctx, "git-audit", nonce)
    assert _disk_hooks(user_config) == {}


def test_apply_mounts_with_backup_and_takes_effect_now(ctx, user_config):
    nonce = _nonce(ctx, "git-audit")
    res = hook_packs.apply(ctx, "git-audit", nonce)
    assert "已挂上 git-audit" in res and "不用重启" in res
    assert (user_config.parent / (user_config.name + ".bak-before-hook")).exists()
    assert _disk_hooks(user_config).get("pre_tool"), "磁盘上要看得见"
    assert ctx.config.hooks.get("pre_tool"), "内存里那份也要当场生效"
    # 一次性:同一个凭据不能挂第二次
    assert "错误" in hook_packs.apply(ctx, "git-audit", nonce)


def test_apply_says_so_when_there_is_no_config_to_edit(ctx):
    assert "找不到" in hook_packs.apply(ctx, "git-audit", _nonce(ctx, "git-audit"))


def test_run_tests_apply_writes_its_helper(ctx, user_config):
    nonce = _nonce(ctx, "run-tests")
    assert "已挂上 run-tests" in hook_packs.apply(ctx, "run-tests", nonce)
    helper = app_path(hook_packs.TEST_HELPER)
    assert helper.is_file(), "命令指着一条不存在的助手 = 每次都没跑还说不出为什么"
    assert "updated_output" in helper.read_text(encoding="utf-8")


def test_apply_rolls_back_memory_when_the_disk_write_fails(ctx, user_config, monkeypatch):
    before = user_config.read_text(encoding="utf-8")
    nonce = _nonce(ctx, "git-audit")

    def boom(path, cfg):
        raise OSError("磁盘满了")

    monkeypatch.setattr("minicode.config.save_config", boom)
    res = hook_packs.apply(ctx, "git-audit", nonce)
    assert "错误" in res and "回滚" in res
    assert ctx.config.hooks == {}, "磁盘没写成,内存不许装着写成了"
    assert user_config.read_text(encoding="utf-8") == before


def test_the_two_tools_split_read_and_write():
    from minicode.tools.registry import build_default_registry
    got = {t.name: t.read_only for t in build_default_registry()
           if t.name in ("hooks", "hooks_apply")}
    assert got == {"hooks": True, "hooks_apply": False}, (
        "挂配方 = 改用户配置里的任意命令执行入口,必须弹框;"
        "而「查一眼」不该也弹框 —— 合成一只就得二选一")


def test_mounted_audit_recipe_actually_writes_the_log(ctx, user_config):
    """挂上之后真跑得动 —— 那句 cmd 的 `type ... >> ...` 是我照语法写的,
    错了的话这条"开箱即用"的审计配方会静默什么都不做,而它存在的理由恰恰是留下事后线索。
    """
    from minicode import hooks
    nonce = _nonce(ctx, "git-audit")
    hook_packs.apply(ctx, "git-audit", nonce)
    log = app_path(hook_packs.AUDIT_LOG)
    got = hooks.run_hook("pre_tool", ctx.config.hooks, "bash",
                         args={"command": "git status"}, workspace=str(ctx.workspace))
    assert got is not None and got["ok"] is True, got
    assert got["decision"] == "continue" and got["blocked"] is False, "审计只记不拦"
    assert log.is_file() and "git status" in log.read_text(encoding="utf-8")
    # 名单外的命令不叫这条钩子(command_prefix 认领判定在真配方上成立)
    assert hooks.run_hook("pre_tool", ctx.config.hooks, "bash",
                          args={"command": "pytest -q"}) is None
