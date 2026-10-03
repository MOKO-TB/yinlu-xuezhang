"""出包时那份内置 Key 从哪来 —— v0.9.110 补的洞。

S1(v0.9.107)把内置 Key 从工作区 `config.local.json` 迁进 `~/.yinluxs/builtin_key.json`,
而且**读到就迁出、抹掉工作区那一份**。`packaging/gcode.spec` 注入的偏偏是工作区那个文件,
于是它从此只剩一句 `_comment`:构建一路绿、包照发,**同学装完发现校园模型开箱不能用**。
整条链上没有一步会红 —— 这种"没人报错的坏"只能靠把来源解析写成可测的纯函数钉住。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

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
