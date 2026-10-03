"""出包时那份内置 Key 从哪来 —— v0.9.110 补的洞。

S1(v0.9.107)把内置 Key 从工作区 `config.local.json` 迁进 `~/.yinluxs/builtin_key.json`,
而且**读到就迁出、抹掉工作区那一份**。`packaging/gcode.spec` 注入的偏偏是工作区那个文件,
于是它从此只剩一句 `_comment`:构建一路绿、包照发,**同学装完发现校园模型开箱不能用**。
整条链上没有一步会红 —— 这种"没人报错的坏"只能靠把来源解析写成可测的纯函数钉住。
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_exe  # noqa: E402
from build_exe import _secrets_payload  # noqa: E402


def _w(p: Path, payload):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_workspace_copy_wins_when_it_still_has_the_key(tmp_path):
    ws = _w(tmp_path / "a" / "config.local.json", {"builtin_api_key": "OLD", "_comment": "x"})
    home = _w(tmp_path / "b" / "builtin_key.json", {"builtin_api_key": "NEW"})
    payload, where = _secrets_payload(ws, home)
    assert payload == {"builtin_api_key": "OLD"}
    assert where == "工作区旧家", "两处都在时用旧家(与运行时同一条顺序,别在这里改口径)"


def test_key_is_taken_from_the_credential_dir_after_the_migration(tmp_path):
    """这一支就是那个洞的正身:工作区被抹空之后,构建必须还能拿到 Key。"""
    ws = _w(tmp_path / "a" / "config.local.json", {"_comment": "Key 已迁出"})
    home = _w(tmp_path / "b" / "builtin_key.json", {"builtin_api_key": "REAL"})
    payload, where = _secrets_payload(ws, home)
    assert payload == {"builtin_api_key": "REAL"}, "迁出之后构建就拿不到 Key 了 —— 包会开箱即废"
    assert where == "凭据目录新家"


def test_no_key_anywhere_reports_empty_instead_of_guessing(tmp_path):
    payload, where = _secrets_payload(tmp_path / "nope.json", tmp_path / "also-nope.json")
    assert payload is None and where == "", "缺 Key 要能被判出来,调用方才会打印警告"


def test_broken_json_does_not_break_the_lookup(tmp_path):
    ws = tmp_path / "a" / "config.local.json"
    ws.parent.mkdir(parents=True)
    ws.write_text("{这不是 JSON", encoding="utf-8")
    home = _w(tmp_path / "b" / "builtin_key.json", {"builtin_api_key": "REAL"})
    assert _secrets_payload(ws, home)[0] == {"builtin_api_key": "REAL"}
    # 列表/字符串这类形状读不出字段:不能崩,也不能把整份内容当 Key
    _w(ws, ["builtin_api_key"])
    assert _secrets_payload(ws, tmp_path / "none.json")[0] is None


def test_only_the_key_field_ships(tmp_path):
    """注入包里的只准是那一个字段:旧家剩下的注释/别的键都不该跟着外发。"""
    ws = _w(tmp_path / "a" / "config.local.json",
            {"builtin_api_key": "K", "other_private": "别的东西", "providers": [{"id": "x"}]})
    payload, _ = _secrets_payload(ws, tmp_path / "none.json")
    assert list(payload) == ["builtin_api_key"]


# ---------------- 名字这条:第一版就是栽在这里的 ----------------

def test_the_injected_file_keeps_the_runtime_name(tmp_path, monkeypatch):
    """PyInstaller 按**源文件名**落包,而运行时读 `resource_path("config.local.json")`。

    真实事故:第一版用 `mkstemp(prefix="yinluxs-secrets-")` 把 Key 交给 spec,构建日志
    一路绿、Key 长度也打印出来了,出包后核对才发现包里是
    `_internal/yinluxs-secrets-6s8z93wk.json` —— 内容对、名字没人读,"没 Key"变成了
    "Key 在包里但找不到",比原来更难查。所以钉死:交给 spec 的那个目录里只有一个文件,
    且它的名字必须等于运行时常量。
    """
    from minicode.config import LOCAL_SECRETS_NAME

    monkeypatch.setattr(build_exe, "_secrets_payload",
                        lambda *_a, **_k: ({"builtin_api_key": "REAL-51-chars"}, "凭据目录新家"))
    env = {}
    tmp_dir = build_exe._inject_build_secrets(env)
    try:
        assert env["YINLUXS_BUILD_SECRETS_DIR"] == tmp_dir
        names = sorted(p.name for p in Path(tmp_dir).iterdir())
        assert names == [LOCAL_SECRETS_NAME], "包里的文件名对不上运行时读的那个:%s" % names
        assert json.loads((Path(tmp_dir) / LOCAL_SECRETS_NAME).read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_bundle_check_catches_a_key_that_landed_under_the_wrong_name(tmp_path, capsys):
    """构建后的那道核对:有 Key 却没落进包 → 必须炸,不能只打印一句绿话。"""
    dist = tmp_path / "引路学长"
    (dist / "_internal").mkdir(parents=True)
    with pytest.raises(SystemExit) as e:
        build_exe._verify_bundle_secrets(dist, had_key=True)
    assert "开箱不能用" in str(e.value)

    # 名字对了才算过
    (dist / "_internal" / build_exe.LOCAL_SECRETS_NAME).write_text(
        json.dumps({"builtin_api_key": "X" * 40}), encoding="utf-8")
    build_exe._verify_bundle_secrets(dist, had_key=True)
    assert "长度 40" in capsys.readouterr().out

    # 构建机本来就没 Key:不炸,但要警告(这是"缺了也能出包"那条老口径)
    build_exe._verify_bundle_secrets(tmp_path / "empty", had_key=False)
    assert "警告" in capsys.readouterr().out
