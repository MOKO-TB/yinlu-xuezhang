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


def test_risks_for_new_media_tools():
    # OCR:图片可能外发给视觉模型,且结果只是草稿
    ocr = risk_notes("ocr_image", {"path": "photo.png"})
    assert any("vision" in n and "外部模型" in n for n in ocr)
    assert any("草稿" in n for n in ocr)
    # 图表:落盘 + 数值全部来自调用方,不假装程序会取数
    chart = risk_notes("render_chart", {"data": {"高数": 88, "英语": 92}})
    assert any("charts/" in n and "撤销" in n for n in chart)
    assert any("不取数也不补数" in n for n in chart)
    assert any("高数" in n for n in chart)          # 把真实数据摆进确认框
    # 图片生成:api 花额度、card 只是排版,auto 两种可能都写清
    api = risk_notes("generate_image", {"backend": "api", "prompt": "猫"})
    assert any("额度" in n for n in api) and not any("卡片" in n for n in api)
    card = risk_notes("generate_image", {"backend": "card", "text": "通知"})
    assert any("不联网" in n and "不是 AI 绘图" in n for n in card)
    auto = risk_notes("generate_image", {"prompt": "猫"})
    assert any("额度" in n for n in auto) and any("排版" in n or "卡片" in n for n in auto)
    assert any("images/" in n for n in auto)


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


# ------------------------------------------------------------ 后台命令的代价

def test_background_job_tells_the_lifetime_limit_in_the_confirm_box():
    """放后台就是绕开超时 —— 确认框必须把这条边界摆出来,不能只说"执行一条命令"。"""
    from minicode.sandbox import KNOWN_LIMITS
    notes = risk_notes("bash", {"command": "pytest -q", "background": True})
    assert any("后台" in n for n in notes)
    assert KNOWN_LIMITS["background"] in notes     # 措辞只有沙箱那一份真相
    plain = risk_notes("bash", {"command": "pytest -q"})
    assert KNOWN_LIMITS["background"] not in plain  # 没传 background 就不唠叨


def test_bash_status_is_read_only_and_bash_kill_states_the_cost():
    assert any("只读" in n for n in risk_notes("bash_status", {}))
    kill = risk_notes("bash_kill", {"id": "j2"})
    assert any("子进程" in n for n in kill)
    assert any("不会回滚" in n for n in kill)


def test_prefixed_subagent_tool_names_still_get_the_notes():
    """子代理的工具叫 `campus·bash_kill`,前缀切分后要拿到同一条风险说明。"""
    from minicode.sandbox import KNOWN_LIMITS
    assert KNOWN_LIMITS["background"] in risk_notes(
        "campus·bash", {"command": "make", "background": True})
    assert any("子进程" in n for n in risk_notes("implement·bash_kill", {"id": "j1"}))
