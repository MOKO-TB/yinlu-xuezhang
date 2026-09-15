"""GUI 桥接层测试:假 webview 捕获事件 + 假 LLM 驱动完整回合。"""
import json
import os
import threading
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


class GatedLLM(FakeLLM):
    """流式输出被闸门卡住:任务会一直保持「进行中」,直到测试放行。

    原来这组用例用 SlowLLM(按 sleep 配速):「任务还在跑」这个断言本质上依赖
    "几秒的窗口内断言一定能到达"。全量回归时机器负载一高,窗口可能提前跑完,
    于是出现 `assert False is True` 的随机失败。这里换成显式闸门——测试不
    release,任务就不可能结束,断言不再依赖时间。
    """

    def __init__(self, gate: threading.Event, chunk: str = "x",
                 wait_timeout: float = 10.0):
        self.gate = gate
        self.chunk = chunk
        self.wait_timeout = wait_timeout

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None):
        if on_text:
            on_text(self.chunk)          # 先吐一片,让 wait_until(text_delta) 成立
        self.gate.wait(timeout=self.wait_timeout)   # 卡住:任务保持 busy
        return AssistantTurn(text=self.chunk)


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

    # 停止标志在「会话运行器」的 UI 适配器上(每个会话一份)
    runner = bridge._current
    original_on_text = runner.ui.on_text_delta

    def on_text_with_stop(delta):
        if delta == "c":        # 收到第 3 片时请求停止
            bridge.stop_task()
        original_on_text(delta)

    runner.ui.on_text_delta = on_text_with_stop

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


def test_get_info_ships_library_health(config, tmp_path, monkeypatch):
    """馆藏库自检结果要随 get_info 下发:库没装全时界面当场就能提示。"""
    import json as _json
    import sqlite3

    from minicode import library
    # 开发态的 app_path 兑底会命中仓库里那份 1.3 GB 真库,把兑底指到空目录
    nowhere = tmp_path / "_no_bundled_db"
    monkeypatch.setattr(library, "app_path", lambda *p: nowhere.joinpath(*p))
    monkeypatch.setattr(library, "resource_path", lambda *p: nowhere.joinpath(*p))

    bridge, _window, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))

    # 1) 目录里没有库 → 界面提示“未安装”
    health = bridge.get_info()["library_health"]
    assert health["status"] == "missing"
    assert "未安装" in health["notice"]

    # 2) 有库但比凭证记录的小(分发/安装时只拿到半截)→ 提示“不完整”
    db = tmp_path / "crawled" / library.DOMAIN / library.DB_NAME
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE books(record_id INTEGER PRIMARY KEY, title TEXT);"
                    "INSERT INTO books VALUES (1, '活着');")
    c.commit()
    c.close()
    library.write_stamp(db, 1)
    sp = library.stamp_path_for(db)
    stamp = _json.loads(sp.read_text(encoding="utf-8"))
    stamp["size"] = stamp["size"] + 1_000_000     # 凭证说应该有更大的一份
    sp.write_text(_json.dumps(stamp), encoding="utf-8")
    health = bridge.get_info()["library_health"]
    assert health["status"] == "incomplete"
    assert "不完整" in health["notice"]

    # 3) 库正常时不下发提示(界面不吵)
    library.write_stamp(db, 1)
    health = bridge.get_info()["library_health"]
    assert health["status"] == "ok"
    assert health["notice"] == ""


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
    # 内置校园模型始终第一位(用户供应商跟在后面)
    assert [p["id"] for p in saved["providers"]] == ["gzasc", "x", "y"]
    assert saved["active_provider"] == "y"
    assert bridge.get_providers()["active_provider"] == "y"
    # 内置供应商的 key 永不下发(界面不可见、也不可改)
    gp = bridge.get_providers()["providers"][0]
    assert gp["id"] == "gzasc" and gp["api_key"] == "" and gp.get("builtin") is True

    # 切换供应商并重建 LLM 客户端
    assert bridge.set_provider("x")["ok"]
    assert config.model == "mx"
    assert bridge.agent.llm.config.base_url == "https://x/v1"
    # 切回内置校园模型也允许(默认推荐)
    assert bridge.set_provider("gzasc")["ok"]
    assert config.provider.id == "gzasc"
    assert bridge.agent.llm.config.base_url.endswith("/v1")
    assert not bridge.set_provider("ghost")["ok"]

    # 伪造内置 id 想改写内置供应商 → 拒绝
    forged = [{"id": "gzasc", "name": "假", "base_url": "https://evil/v1",
               "api_key": "k", "model": "m"}]
    r = bridge.save_providers(json.dumps(forged), "gzasc")
    assert not r["ok"] and "内置" in r["msg"]
    assert config.providers[0].base_url != "https://evil/v1"   # 原样未动

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

    session.add_user("宿舍空调坏了")
    after = bridge.get_suggestions()["suggestions"]
    assert after[0] == "宿舍水电维修流程是什么"   # 聊过后 → 追问话术置顶
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


# ---------------------------------------------------------------- 校园地图

def test_get_info_ships_campus_map(config, tmp_path):
    bridge, _window, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    cmap = bridge.get_info()["campus_map"]
    assert len(cmap["items"]) == 36
    assert cmap["base_map"].endswith("map.jpg")
    assert cmap["size"] == {"width": 2559, "height": 1279}   # 坐标空间要和底图一致
    assert cmap["caveats"] and cmap["not_buildings"]
    assert "how_to_resolve" not in cmap      # 解析规则在后端,不下发


def test_confirm_request_carries_why(config, tmp_path):
    """确认框要能说清"为什么做这一步":模型填的 reason 直达前端。"""
    llm = FakeLLM([AssistantTurn(
        text="我先看一下知识库里有哪些文件。",
        tool_calls=[ToolCall(id="c1", name="bash", arguments=json.dumps(
            {"command": "ls kb/", "reason": "确认知识库文件清单"}))]),
        AssistantTurn(text="ok")])
    bridge, window, _ = make(config, tmp_path, llm)

    bridge.send_task("看看知识库")
    assert wait_until(lambda: any(e["type"] == "confirm_request" for e in window.events))
    ev = window.of_type("confirm_request")[0]
    assert ev["why"] == "确认知识库文件清单"
    assert "列出目录" in ev["digest"]
    assert ev["risks"]
    bridge.confirm_reply("no")
    assert wait_until(lambda: not bridge._busy)


# ------------------------------------------------- 任务进行中也能新建/切换

def test_new_task_keeps_previous_run_in_background(config, tmp_path):
    """任务进行中点"新建任务":开一个新会话,原任务转后台继续跑完(v0.8.13 起的行为)。"""
    gate = threading.Event()
    llm = GatedLLM(gate)
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("长任务")
    assert wait_until(lambda: bridge._busy and session.log_path is not None)
    assert wait_until(lambda: window.of_type("text_delta"))   # 确实在流式输出中

    running = bridge._current
    r = bridge.new_task()
    assert r["ok"] and r["session"] != running.key

    # 新会话是干净的;原会话既没被清空,也还在后台跑
    assert len(bridge.session.messages) == 1                  # 只有 system
    assert bridge._busy is False                              # 当前会话空闲
    assert running.busy is True                               # 闸门未开,必然还在跑
    assert window.of_type("cleared")[-1]["session"] == r["session"]

    gate.set()                                                # 放行:后台自己跑完
    assert wait_until(lambda: not running.busy)
    assert any(m["role"] == "assistant" for m in session.messages)


def test_resume_running_session_attaches_without_interrupt(config, tmp_path):
    """切回一个正在后台跑的会话:直接接上,而不是打断它。"""
    gate = threading.Event()
    llm = GatedLLM(gate)
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("长任务")
    assert wait_until(lambda: bridge._busy and session.log_path is not None)
    assert wait_until(lambda: window.of_type("text_delta"))

    running = bridge._current
    bridge.new_task()                       # 切到新会话,原任务转后台
    assert not bridge._busy

    bridge.refresh_sessions()
    target = next(i for i, e in enumerate(window.of_type("sessions")[-1]["items"], start=1)
                  if e.get("running"))
    assert bridge.resume_session(target)["ok"]
    assert bridge._current is running       # 接上的就是后台那个运行器
    assert bridge._busy                     # 仍在跑,没被打断
    assert not any(e["type"] == "info" and "已中断" in (e.get("text") or "")
                   for e in window.events)

    gate.set()                              # 收尾:别让后台线程卡到超时
    assert wait_until(lambda: not running.busy)
    assert wait_until(lambda: not bridge._busy)


def test_get_asset_serves_skill_images(config, tmp_path):
    asset_dir = tmp_path / "skills" / "bike-parking-guide" / "assets"
    asset_dir.mkdir(parents=True)
    (asset_dir / "map.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    bridge, _, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))

    r = bridge.get_asset("assets/map.jpg")
    assert r["ok"] and r["data_url"].startswith("data:image/jpeg;base64,")
    # 防目录穿越
    assert not bridge.get_asset("../../secret.txt")["ok"]
    assert not bridge.get_asset("assets/missing.jpg")["ok"]
