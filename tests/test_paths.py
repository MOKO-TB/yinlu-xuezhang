"""数据目录解析(v0.9.22:装进只读目录也能保存配置)。

安装版被以管理员权限装进 Program Files 时 exe 目录不可写,以前表现为
"配好 Key 退出重进就没了";现在做一次写探针,写不进去就整体搬到用户目录。
"""
import json

import pytest

from minicode import paths


def test_is_writable_probe(tmp_path):
    ok = tmp_path / "w"
    assert paths._is_writable(ok) is True
    assert (ok / ".write-probe.tmp").exists() is False      # 探针自己擦干净了
    assert paths._is_writable(tmp_path / "不存在的父目录" / "子目录") is True


def test_is_writable_false_for_a_file(tmp_path):
    """拿文件当目录:探针失败要返回 False,绝不能抛异常。"""
    f = tmp_path / "afile.txt"
    f.write_text("x", encoding="utf-8")
    assert paths._is_writable(f) is False


def test_local_state_dir_follows_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths._local_state_dir() == tmp_path / "引路学长"
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert paths._local_state_dir() == paths.Path.home() / ".yinluxs" / "app"


def test_adopt_only_moves_when_target_is_empty(tmp_path):
    src, dst = tmp_path / "exe", tmp_path / "app"
    src.mkdir()
    (src / "config.json").write_text('{"a": 1}', encoding="utf-8")
    paths._adopt(src, dst)
    assert (dst / "config.json").read_text(encoding="utf-8") == '{"a": 1}'
    # 目标已有配置(用户在新目录里配过)→ 绝不覆盖
    dst2 = tmp_path / "exe2"
    dst2.mkdir()
    (dst2 / "config.json").write_text('{"a": 2}', encoding="utf-8")
    paths._adopt(dst2, dst)
    assert json.loads((dst / "config.json").read_text(encoding="utf-8")) == {"a": 1}


def test_diagnostics_reports_where_data_lives():
    d = paths.diagnostics()
    assert set(d) >= {"app_dir", "config", "backend", "frozen", "writable"}
    assert d["config"].endswith("config.json")
    assert d["backend"] in ("exe", "repo", "local")
    assert isinstance(d["frozen"], bool) and isinstance(d["writable"], bool)


def test_frozen_app_dir_falls_back_to_user_dir(tmp_path, monkeypatch):
    """模拟装进只读目录:数据目录搬到用户目录,并把旧 config.json 带过去。"""
    exe_dir = tmp_path / "Program Files" / "引路学长"
    exe_dir.mkdir(parents=True)
    (exe_dir / "config.json").write_text('{"providers": []}', encoding="utf-8")
    state = tmp_path / "state"
    monkeypatch.setattr(paths, "_is_writable", lambda d: False)
    monkeypatch.setattr(paths, "_local_state_dir", lambda: state)

    app_dir, backend = paths._frozen_app_dir(exe_dir)
    assert (app_dir, backend) == (state, "local")
    assert (state / "config.json").read_text(encoding="utf-8") == '{"providers": []}'


def test_frozen_app_dir_keeps_exe_dir_when_writable(tmp_path, monkeypatch):
    """便携版行为一字不改:exe 旁能写就住在那儿,整个文件夹拷走数据跟着走。"""
    exe_dir = tmp_path / "U盘" / "引路学长"
    exe_dir.mkdir(parents=True)
    monkeypatch.setattr(paths, "_is_writable", lambda d: True)
    monkeypatch.setattr(paths, "_local_state_dir",
                        lambda: pytest.fail("能写就不该去用户目录"))

    assert paths._frozen_app_dir(exe_dir) == (exe_dir, "exe")
