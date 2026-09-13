"""权限确认框的"为什么要做这一步"解析测试。"""
from minicode.explain import (
    action_reason, command_digest, permission_why, risk_notes,
)


# ------------------------------------------------------------ 命令人话解读

def test_digest_recognizes_common_commands():
    assert "Python" in command_digest("python -c \"import json; print(json.load(open('kb/a.json')))\"")
    assert command_digest("pytest -q tests/test_kb.py") == "运行测试"
    assert "删除" in command_digest("rm -rf build/")
    assert "下载" in command_digest("curl -s https://example.com/x.json")
    assert "仓库" in command_digest("git status --short")
    assert "搜索" in command_digest("grep -rn 'foo' minicode/")
    assert "列出目录" in command_digest("ls -la")


def test_digest_marks_multi_step_and_falls_back():
    assert "多个步骤" in command_digest("cd kb && python build.py")
    # 认不出来就如实说,不要编意图
    assert "无法自动判断" in command_digest("./weird-binary --xyz")


def test_digest_handles_empty():
    assert command_digest("") == "执行一条空命令"


# ------------------------------------------------------------ 影响面提示

def test_risks_for_bash():
    assert any("无法恢复" in n for n in risk_notes("bash", {"command": "rm -rf tmp/"}))
    assert any("写入" in n for n in risk_notes("bash", {"command": "echo hi > a.txt"}))
    assert any("联网" in n for n in risk_notes("bash", {"command": "curl https://x.com"}))
    readonly = risk_notes("bash", {"command": "ls -la"})
    assert any("只读" in n for n in readonly)
    # bash 的改动不在撤销范围,这条必须一直提示
    assert any("撤销" in n for n in readonly)


def test_risks_for_file_tools():
    assert any("撤销" in n for n in risk_notes("write_file", {"path": "a.py"}))
    assert any("只读" in n for n in risk_notes("read_file", {"path": "a.py"}))


def test_risks_strip_subagent_prefix():
    assert any("只读" in n for n in risk_notes("explore·read_file", {}))


# ------------------------------------------------------------ 为什么要做

def test_reason_prefers_model_reason():
    assert action_reason("bash", {"command": "ls"}, reason="看看目录结构") == "看看目录结构"


def test_reason_falls_back_to_last_narration_paragraph():
    narration = "我先看看知识库。\n\n这条命令只列出目录,不会改动任何文件。"
    assert action_reason("bash", {"command": "ls"}, narration=narration) == \
        "这条命令只列出目录,不会改动任何文件。"


def test_reason_is_trimmed_and_bounded():
    long = "为" * 400
    out = action_reason("bash", {"command": "ls"}, reason=long)
    assert len(out) <= 201 and out.endswith("…")


def test_reason_when_model_says_nothing():
    out = action_reason("bash", {"command": "ls"})
    assert "没有留下" in out and "自行判断" in out


def test_permission_why_bundle():
    why = permission_why("bash", {"command": "python build_kb.py"}, reason="重建知识库索引")
    assert why["why"] == "重建知识库索引"
    assert "Python" in why["digest"]
    assert why["risks"]
    # 非 bash 工具不该硬塞一段命令解读
    assert permission_why("edit_file", {"path": "a.py"})["digest"] == ""
