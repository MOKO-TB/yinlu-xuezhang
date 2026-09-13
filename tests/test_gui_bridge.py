"""GUI 桥接层测试:假 webview 捕获事件 + 假 LLM 驱动完整回合。"""
import json
import time

from minicode.attachments import save_attachment, save_attachments
from minicode.gui_bridge import create_app
from minicode.llm import AssistantTurn, ToolCall


class FakeWindow:
    """捕获 evaluate_js 推送的事件。"""

    def __init__(self):
        self.events = []

    def evaluate_js(self, code: str):
        prefix = "window.__gcodeEvent("
        assert code.startswith(prefix), code
        self.events.append(json.loads(code[len(prefix):-1]))

    def of_type(self, type_name):
        return [e for e in self.events if e["type"] == type_name]


class FakeLLM:
    def __init__(self, turns):
        self.turns = list(turns)

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None, on_retry=None):
        turn = self.turns.pop(0)
        if isinstance(turn, BaseException):
            raise turn
        if on_text and turn.text:
            on_text(turn.text)
        return turn


class FakeLLMScripted(FakeLLM):
    """按片依次回调 on_text(用于触发停止)。"""

    def __init__(self, chunks):
        self.chunks = chunks

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None, on_retry=None):
        for chunk in self.chunks:
            if on_text:
                on_text(chunk)
        return AssistantTurn(text="".join(self.chunks))


def wait_until(cond, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def make(config, tmp_path, llm):
    bridge, ui, agent, session = create_app(
        config, tmp_path, llm=llm, sessions_dir=tmp_path / "sessions",
        config_path=tmp_path / "config.json")
    window = FakeWindow()
    ui.attach(window)
    return bridge, window, session


def _write_call(call_id="c1"):
    import json as _json
    return ToolCall(id=call_id, name="write_file",
                    arguments=_json.dumps({"path": "gui.txt", "content": "from gui"}))


def test_full_flow_with_confirm(config, tmp_path):
    llm = FakeLLM([AssistantTurn(tool_calls=[_write_call()]),
                   AssistantTurn(text="文件已创建完成。")])
    bridge, window, session = make(config, tmp_path, llm)

    assert bridge.send_task("创建 gui.txt")["ok"]
    assert wait_until(lambda: any(e["type"] == "confirm_request" for e in window.events))
    bridge.confirm_reply("yes")
    assert wait_until(lambda: not bridge._busy)

    assert (tmp_path / "gui.txt").read_text(encoding="utf-8") == "from gui"
    types = [e["type"] for e in window.events]
    assert "confirm_request" in types
    assert "text_delta" in types and "finish_stream" in types
    assert window.of_type("busy")[-1]["value"] is False
    assert any(e["type"] == "sessions" for e in window.events)


def test_confirm_denied_skips_write(config, tmp_path):
    llm = FakeLLM([AssistantTurn(tool_calls=[_write_call()]),
                   AssistantTurn(text="好的,不写了")])
    bridge, window, _ = make(config, tmp_path, llm)

    bridge.send_task("写文件")
    assert wait_until(lambda: any(e["type"] == "confirm_request" for e in window.events))
    bridge.confirm_reply("no")
    assert wait_until(lambda: not bridge._busy)

    assert not (tmp_path / "gui.txt").exists()


def test_stop_interrupts_stream(config, tmp_path):
    llm = FakeLLMScripted(["a", "b", "c", "d", "e"])
    bridge, window, _ = make(config, tmp_path, llm)

    ui = bridge.ui
    original_on_text = ui.on_text_delta

    def on_text_with_stop(delta):
        if delta == "c":        # 收到第 3 片时请求停止
            bridge.stop_task()
        original_on_text(delta)

    ui.on_text_delta = on_text_with_stop

    bridge.send_task("长回复")
    assert wait_until(lambda: not bridge._busy)
    assert any(e["type"] == "info" and "已中断" in e["text"] for e in window.events)


def test_undo_via_bridge(config, tmp_path):
    llm = FakeLLM([AssistantTurn(tool_calls=[_write_call()]),
                   AssistantTurn(text="ok"),
                   AssistantTurn(text="done")])
    bridge, window, _ = make(config, tmp_path, llm)

    bridge.send_task("创建 gui.txt")
    assert wait_until(lambda: any(e["type"] == "confirm_request" for e in window.events))
    bridge.confirm_reply("always")   # 本轮总是允许
    assert wait_until(lambda: not bridge._busy)
    assert (tmp_path / "gui.txt").exists()

    msgs = bridge.undo()["msg"]
    assert "已撤销" in msgs
    assert not (tmp_path / "gui.txt").exists()


def test_get_info_has_skills_and_providers(config, tmp_path):
    bridge, window, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    info = bridge.get_info()
    assert info["app_name"] == "引路学长"
    assert info["providers"][0]["id"] == "test"
    assert info["active_provider"] == "test"
    assert isinstance(info["skills"], list)


def test_provider_management(config, tmp_path, monkeypatch):
    monkeypatch.setattr("minicode.config.CREDENTIALS_PATH",
                        tmp_path / "credentials.json")
    bridge, window, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))

    providers = [
        {"id": "x", "name": "X", "base_url": "https://x/v1", "api_key": "kx", "model": "mx"},
        {"id": "y", "name": "Y", "base_url": "https://y/v1", "api_key": "", "model": "my"},
    ]
    r = bridge.save_providers(json.dumps(providers), "y")
    assert r["ok"]
    assert config.model == "my"
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in saved["providers"]] == ["x", "y"]
    assert saved["active_provider"] == "y"
    assert bridge.get_providers()["active_provider"] == "y"

    # 切换供应商并重建 LLM 客户端
    assert bridge.set_provider("x")["ok"]
    assert config.model == "mx"
    assert bridge.agent.llm.config.base_url == "https://x/v1"
    assert not bridge.set_provider("ghost")["ok"]

    # 非法输入
    assert not bridge.save_providers("{bad", "x")["ok"]
    r = bridge.save_providers(json.dumps([{"name": "无地址"}]), "x")
    assert not r["ok"] and "base_url" in r["msg"]
def test_get_info_and_history_roundtrip(config, tmp_path):
    llm = FakeLLM([AssistantTurn(text="回复一")])
    bridge, window, session = make(config, tmp_path, llm)

    info = bridge.get_info()
    assert info["model"] == config.model
    assert info["workspace"] == str(tmp_path)

    bridge.send_task("任务一")
    assert wait_until(lambda: not bridge._busy)

    # 新会话恢复刚产生的留档(bridge2 自身会新建空会话排第一,需跳过)
    from minicode.session import list_sessions
    bridge2, ui2, _agent2, _session2 = create_app(
        config, tmp_path, llm=FakeLLM([AssistantTurn(text="x")]),
        sessions_dir=tmp_path / "sessions")
    window2 = FakeWindow()
    ui2.attach(window2)
    summaries2 = list_sessions(tmp_path / "sessions")
    target = next(i for i, s in enumerate(summaries2, 1) if s.n_messages > 0)
    assert bridge2.resume_session(target)["ok"]
    roles = [m["role"] for m in bridge2.session.messages]
    assert "user" in roles and "assistant" in roles
    resumed = [e for e in window2.events if e["type"] == "resumed"]
    assert resumed and any(m["text"] == "任务一" for m in resumed[0]["messages"])


def test_delete_current_session(config, tmp_path):
    bridge, window, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="done")]))
    bridge.send_task("任务")
    assert wait_until(lambda: not bridge._busy)

    from minicode.session import list_sessions
    summaries = list_sessions(tmp_path / "sessions")
    target = next(i for i, s in enumerate(summaries, 1) if s.n_messages > 0)
    path = summaries[target - 1].path

    r = bridge.delete_session(target)
    assert r["ok"]
    assert not path.exists()
    assert any(e["type"] == "cleared" for e in window.events)
    assert bridge.session.log_path is None             # 重置为全新会话,懒创建


def test_delete_non_current_session(config, tmp_path):
    llm = FakeLLM([AssistantTurn(text="done")])
    bridge, window, _ = make(config, tmp_path, llm)
    bridge.send_task("任务")
    assert wait_until(lambda: not bridge._busy)

    bridge2, ui2, _a, _s = create_app(config, tmp_path, llm=FakeLLM([AssistantTurn(text="x")]),
                                      sessions_dir=tmp_path / "sessions",
                                      config_path=tmp_path / "config.json")
    win2 = FakeWindow()
    ui2.attach(win2)

    from minicode.session import list_sessions
    summaries = list_sessions(tmp_path / "sessions")
    target = next(i for i, s in enumerate(summaries, 1) if s.n_messages > 0)
    path = summaries[target - 1].path

    assert bridge2.delete_session(target)["ok"]
    assert not path.exists()
    assert not any(e["type"] == "cleared" for e in win2.events)   # 删的不是当前会话


def test_open_url_uses_system_browser(config, tmp_path, monkeypatch):
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)
    bridge, _, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))

    assert bridge.open_url("https://example.com/a")["ok"]
    assert opened == ["https://example.com/a"]
    assert not bridge.open_url("file:///C:/x")["ok"]
    assert not bridge.open_url("javascript:alert(1)")["ok"]


def test_edit_resend_truncates_and_resends(config, tmp_path):
    llm = FakeLLM([AssistantTurn(text="第一次回答"),
                   AssistantTurn(text="重发后的回答")])
    bridge, window, session = make(config, tmp_path, llm)

    bridge.send_task("原始问题")
    assert wait_until(lambda: not bridge._busy)
    assert session.messages[-1]["content"] == "第一次回答"

    r = bridge.edit_resend(1, "改过的问题", "原始问题")
    assert r["ok"]
    assert wait_until(lambda: not bridge._busy)
    # 只剩:system + 新 user + 新 assistant(旧的两条被截断重发)
    roles = [m.get("role") for m in session.messages]
    assert roles == ["system", "user", "assistant"]
    assert session.messages[1]["content"] == "改过的问题"
    assert session.messages[2]["content"] == "重发后的回答"


def test_edit_resend_guard_mismatches(config, tmp_path):
    llm = FakeLLM([AssistantTurn(text="a"), AssistantTurn(text="b")])
    bridge, _, session = make(config, tmp_path, llm)
    bridge.send_task("问题一")
    assert wait_until(lambda: not bridge._busy)
    bridge.send_task("问题二")
    assert wait_until(lambda: not bridge._busy)

    assert not bridge.edit_resend(0, "x")["ok"]              # 序号非法
    assert not bridge.edit_resend(9, "x")["ok"]              # 超出条数
    assert not bridge.edit_resend(1, "x", "对不上的原文")["ok"]  # expect 不匹配
    assert not bridge.edit_resend(1, "/斜杠")["ok"]           # 非法命令
    # 校验失败不截断:两条用户消息仍在
    users = [m for m in session.messages if m.get("role") == "user"]
    assert [u["content"] for u in users] == ["问题一", "问题二"]


def test_get_suggestions_reflects_history(config, tmp_path):
    bridge, _, session = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    fresh = bridge.get_suggestions()["suggestions"]
    assert fresh[0] == "最近学校有什么活动"        # 无历史 → 默认顺序

    session.add_user("校园卡丢了怎么办")
    after = bridge.get_suggestions()["suggestions"]
    assert after[0] == "校园卡补办费用再确认一下"   # 聊过后 → 追问话术置顶
    assert after != fresh


def test_send_task_with_attachments_builds_note(config, tmp_path):
    """附件随消息进入会话上下文:落盘路径写进 user 消息,模型能看到清单。"""
    from PIL import Image
    src = tmp_path / "成绩单.png"
    Image.new("RGBA", (40, 30), (10, 200, 10, 255)).save(src)

    bridge, _, session = make(config, tmp_path, FakeLLM([AssistantTurn(text="看过")]))
    bridge.workspace = tmp_path
    atts, errors = save_attachments(tmp_path, [str(src)])
    assert not errors

    assert bridge.send_task("帮我看看这张成绩单", json.dumps([a.to_dict() for a in atts]))["ok"]
    assert wait_until(lambda: not bridge._busy)

    user_msg = next(m for m in session.messages if m.get("role") == "user")
    assert "[附件]" in user_msg["content"]
    assert "uploads/成绩单.png" in user_msg["content"]
    assert "帮我看看这张成绩单" in user_msg["content"]


def test_send_task_with_vision_sends_content_parts(config, tmp_path):
    from PIL import Image
    src = tmp_path / "a.png"
    Image.new("RGB", (30, 30), (1, 2, 3)).save(src)

    config.vision = True
    bridge, _, session = make(config, tmp_path, FakeLLM([AssistantTurn(text="ok")]))
    atts = [save_attachment(tmp_path, src)]

    assert bridge.send_task("看图", json.dumps([a.to_dict() for a in atts]))["ok"]
    assert wait_until(lambda: not bridge._busy)

    content = next(m for m in session.messages if m.get("role") == "user")["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert any(p.get("type") == "image_url" for p in content)


def test_send_task_ignores_attachments_outside_workspace(config, tmp_path):
    bridge, _, session = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("d", encoding="utf-8")

    assert bridge.send_task("读它", json.dumps([{"path": "../secret.txt"}]))["ok"]
    assert wait_until(lambda: not bridge._busy)
    content = next(m for m in session.messages if m.get("role") == "user")["content"]
    assert "[附件]" not in content                       # 越界附件被丢弃


def test_pick_attachments_saves_into_workspace(config, tmp_path):
    from PIL import Image
    src = tmp_path / "photo.png"
    Image.new("RGB", (20, 20), (9, 9, 9)).save(src)

    bridge, window, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))

    class FakeWebviewWindow:
        def create_file_dialog(self, *a, **kw):
            return [str(src)]

    bridge.ui.attach(FakeWebviewWindow())
    r = bridge.pick_attachments()
    assert r["ok"] and len(r["items"]) == 1
    item = r["items"][0]
    assert item["path"].startswith("uploads/") and item["kind"] == "image"
    assert (tmp_path / item["path"]).exists()
    assert item["preview"].startswith("data:image/")


def test_pick_attachments_without_window(config, tmp_path):
    bridge, _, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    bridge.ui.window = None
    assert not bridge.pick_attachments()["ok"]


def test_set_sandbox_toggles_and_persists(config, tmp_path):
    bridge, window, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    info = bridge.get_info()["sandbox"]
    assert info["available"] and info["enabled"]

    r = bridge.set_sandbox(False)
    assert r["ok"] and r["sandbox"]["enabled"] is False
    assert bridge.agent.ctx.sandbox.policy.enabled is False
    assert bridge.config.sandbox["enabled"] is False
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["sandbox"]["enabled"] is False
    assert bridge.set_sandbox(True)["ok"]
