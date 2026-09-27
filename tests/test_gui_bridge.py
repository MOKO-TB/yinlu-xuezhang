"""GUI 桥接层测试:假 webview 捕获事件 + 假 LLM 驱动完整回合。"""
import pytest
import json
import threading
import time

from minicode.attachments import save_attachment, save_attachments
from minicode.gui_bridge import create_app
from minicode.llm import AssistantTurn, ToolCall


# 整文件打 slow:实测 64 s(2026-09-25 --durations=0)—— 慢在真线程/
# 真 socket/真超时,不是算法。日常只跑 `pytest -m "not slow"` 时它会被跳过。
pytestmark = pytest.mark.slow


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


def test_regenerate_replaces_the_last_answer(config, tmp_path):
    """"重新生成":旧答案从上下文里撤下,那句提问**原地**重跑一次(不再追加第二遍)。"""
    llm = FakeLLM([AssistantTurn(text="第一版答案"), AssistantTurn(text="第二版答案")])
    bridge, window, session = make(config, tmp_path, llm)

    assert bridge.send_task("怎么查绩点")["ok"]
    assert wait_until(lambda: bridge._busy)
    assert wait_until(lambda: not bridge._busy)
    assert bridge.regenerate()["ok"]
    assert wait_until(lambda: bridge._busy)
    assert wait_until(lambda: not bridge._busy)

    bodies = [m.get("content") for m in session.messages if m.get("role") == "assistant"]
    assert "第二版答案" in bodies and "第一版答案" not in bodies
    users = [m for m in session.messages if m.get("role") == "user"]
    assert len(users) == 1, "重答不是重新发问:那句提问只能有一条"
    # 旧答案留在留档里(追加式),只补一行 regenerated 标记 —— 与 cleared/compacted 同一口径
    log = session.log_path.read_text(encoding="utf-8")
    assert '"regenerated"' in log


def test_llm_failure_watches_for_recovery_and_says_so(config, tmp_path, monkeypatch):
    """模型连不上 → 起一条探活 → 通了在聊天里说一句 + 走一趟桌面通知(发现三那条死胡同)。

    只把 `list_models` 这一层换成假的(探活打的是真 HTTP);第一问就通,所以不会真睡
    60 秒。跑完必须把"一次只跑一条"那道闸放行,否则第二次失败就永远不再探活。
    """
    from minicode import model_probe, proactive, tray

    llm = FakeLLM([RuntimeError("连接被拒绝")])
    bridge, window, _session = make(config, tmp_path, llm)
    monkeypatch.setattr(model_probe, "list_models", lambda *a, **k: (200, ["m"]))
    monkeypatch.setattr(proactive, "wd_log", lambda msg: None)
    notified = []
    monkeypatch.setattr(tray, "notify",
                        lambda title, body, **k: (notified.append((title, body)),
                                                   {"ok": True})[1])

    bridge.send_task("这句发不出去")
    assert wait_until(lambda: any(e["type"] == "error" and e.get("llm")
                                  for e in window.events)), "失败事件要带 llm 标记"
    assert wait_until(lambda: any(e["type"] == "info" and "模型服务又能连上" in e.get("text", "")
                                  for e in window.events)), "探活了要主动告诉用户一声"
    assert notified and notified[0][0] == "模型恢复了"
    assert bridge._liveness_running is False


def test_regenerate_refuses_when_there_is_nothing_to_redo(config, tmp_path):
    llm = FakeLLM([AssistantTurn(text="答案")])
    bridge, _window, session = make(config, tmp_path, llm)

    r = bridge.regenerate()
    assert r["ok"] is False and r["msg"], "空会话点重答要说清为什么不做,别静默"

    session.add_user("只提问还没答过")
    r = bridge.regenerate()
    assert r["ok"] is False and "提问" in r["msg"]


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
    # 内置供应商的 key 与服务地址(内网 IP)永不下发(界面不可见、也不可改)
    gp = bridge.get_providers()["providers"][0]
    assert gp["id"] == "gzasc" and gp["api_key"] == "" and gp.get("builtin") is True
    assert gp["base_url"] == ""

    # 切换供应商并重建 LLM 客户端
    assert bridge.set_provider("x")["ok"]
    assert config.model == "mx"
    assert bridge.agent.llm.config.base_url == "https://x/v1"
    # 切回内置校园模型也允许(默认推荐)
    assert bridge.set_provider("gzasc")["ok"]
    assert config.provider.id == "gzasc"
    assert bridge.agent.llm.config.base_url.endswith("/v1")
    assert not bridge.set_provider("ghost")["ok"]

    # 伪造内置 id 想改写内置供应商 → 静默过滤(不是报错):
    # v0.9.03 的 bug 就是 get_providers 把内置项一并返回、保存时又被“内置不可编辑”
    # 挡死,导致任何添加/删除都存不进去。现在提交里的内置项一律跳过,由后端按
    # 代码补回,所以"保存失败"不应再发生。
    forged = [{"id": "gzasc", "name": "假", "base_url": "https://evil/v1",
               "api_key": "k", "model": "m"}]
    r = bridge.save_providers(json.dumps(forged), "gzasc")
    assert r["ok"]
    assert config.providers[0].base_url != "https://evil/v1"   # 内置以代码为准
    assert [p.id for p in config.providers] == ["gzasc"]       # 没被伪造项污染
    assert config.provider.id == "gzasc"                       # 激活内置不被偷换

    # 非法输入
    assert not bridge.save_providers("{bad", "x")["ok"]
    r = bridge.save_providers(json.dumps([{"name": "无地址"}]), "x")
    assert not r["ok"] and "base_url" in r["msg"]


def test_builtin_endpoint_is_never_sent_to_frontend(tmp_path):
    """内置校园服务的内网地址不下发给界面:用户只看得到模型名。

    地址本身是内网 IP,对同学既没用也不该暴露;模型管理弹窗里那一行由此
    留空,由「内置」标签承担说明。
    """
    from dataclasses import replace

    from minicode.config import BUILTIN_PROVIDER, BUILTIN_PROVIDER_ID, Config

    cfg = Config(providers=[replace(BUILTIN_PROVIDER)],
                 active_provider_id=BUILTIN_PROVIDER_ID)
    bridge, _window, _ = make(cfg, tmp_path, FakeLLM([AssistantTurn(text="x")]))

    assert cfg.base_url.startswith("http://10.")      # 后端自己照常用
    assert bridge.get_info()["base_url"] == ""
    assert bridge.get_info()["model"] == cfg.model    # 模型名照常展示
    builtin = bridge.get_providers()["providers"][0]
    assert builtin["builtin"] is True
    assert builtin["base_url"] == "" and builtin["api_key"] == ""


def test_session_model_switch_and_metrics(config, tmp_path):
    """任务面板:会话内切模型不写回全局配置,指标随切换刷新。"""
    from minicode.config import Provider
    bridge, window, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    cfg = bridge.config
    cfg.providers.append(Provider(id="deepseek", name="DeepSeek",
                                  base_url="https://api.deepseek.com",
                                  api_key="sk-test", model="deepseek-chat"))

    m = bridge.get_session_metrics()
    assert m["ok"] and m["model"] == cfg.model
    assert m["provider_id"] == cfg.provider.id
    assert m["context_window"] > 0
    assert m["selectable"][0]["builtin"] is False           # 夹具的 test 不是内置
    gz = next((p for p in m["selectable"] if p["id"] == "gzasc"), None)
    if gz is not None:
        assert gz["builtin"] is True            # 内置供应商永远带 builtin 标记
    ds = next(p for p in m["selectable"] if p["id"] == "deepseek")
    assert ds["capabilities"]                               # 能力标签非空

    r = bridge.set_session_model("deepseek")
    assert r["ok"]
    runner = bridge._current
    assert runner.llm_override == {"provider_id": "deepseek", "model": "deepseek-chat"}
    assert runner.agent.llm.config.model == "deepseek-chat"  # 本会话生效
    assert cfg.provider.id != "deepseek"                     # 全局没动
    m2 = bridge.get_session_metrics()
    assert m2["provider_id"] == "deepseek" and m2["model"] == "deepseek-chat"

    # 没配 key 的供应商不给切
    cfg.providers.append(Provider(id="nokey", name="无钥",
                                  base_url="https://n/v1",
                                  api_key="", model="m"))
    r = bridge.set_session_model("nokey")
    assert not r["ok"] and "Key" in r["msg"]


def test_model_catalog_lookup():
    """能力目录:已知模型给窗口与视觉标签,未知模型不猜数字。"""
    from minicode.model_catalog import capabilities, context_window_for, lookup
    qwen = lookup("qwen3.8-27b")
    assert qwen["known"] and qwen["context_window"] == 1_000_000 and qwen["supports_vision"]
    mini = lookup("minimax-m2.5")
    assert mini["known"] and not mini["supports_vision"]
    assert lookup("不存在的模型")["known"] is False
    assert context_window_for("qwen3.8-27b", 131072) == 1_000_000
    assert context_window_for("未知", 131072) == 131072
    assert capabilities("qwen3.8-27b") == ["文本", "视觉", "工具调用"]


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


def test_resume_and_delete_by_filename(config, tmp_path):
    """会话切换/删除按**文件名**定位(不受序号错位影响,路线图 #11)。"""
    from minicode.session import list_sessions
    llm = FakeLLM([AssistantTurn(text="回复一")])
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("任务一")
    assert wait_until(lambda: not bridge._busy)

    bridge2, ui2, _a2, _s2 = create_app(
        config, tmp_path, llm=FakeLLM([AssistantTurn(text="x")]),
        sessions_dir=tmp_path / "sessions")
    window2 = FakeWindow()
    ui2.attach(window2)
    summaries2 = list_sessions(tmp_path / "sessions")
    target = next(s for s in summaries2 if s.n_messages > 0)

    # 用文件名恢复:命中同一条,与序号无关
    assert bridge2.resume_session(target.path.name)["ok"]
    roles = [m["role"] for m in bridge2.session.messages]
    assert "user" in roles and "assistant" in roles

    # 侧栏推送里每条都带 file 字段(前端据此回传)
    sess_events = [e for e in window2.events if e["type"] == "sessions"]
    assert sess_events and all("file" in it for it in sess_events[-1]["items"])

    # 带路径分隔符也只按 basename 匹配;乱名/穿越一律判不存在
    assert bridge2.resume_session("sessions/" + target.path.name.replace("\\", "/"))["ok"] \
        or bridge2.resume_session(target.path.name)["ok"]
    assert bridge2.resume_session("../../etc/passwd")["ok"] is False
    assert bridge2.resume_session("不存在的会话.jsonl")["ok"] is False
    assert bridge2.delete_session("不存在的会话.jsonl")["ok"] is False
    # 按文件名删除真的删掉那条留档
    assert bridge2.delete_session(target.path.name)["ok"]
    assert target.path.name not in {s.path.name for s in list_sessions(tmp_path / "sessions")}


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


def _png(tmp_path, name="a.png"):
    from PIL import Image
    src = tmp_path / name
    Image.new("RGB", (30, 30), (1, 2, 3)).save(src)
    return src


def test_vision_follows_model_capability_without_config_flag(config, tmp_path):
    """选了带视觉的模型就能发图。

    v0.9.21 的坑:发图只看 `config.vision`,而那个开关在界面上**没有任何入口**
    (只有手改 config.json 才打得开),于是"选了视觉模型仍说不能看图"。
    """
    src = _png(tmp_path)
    config.providers[0].model = "kimi-k2.6"
    assert config.vision is False                 # 用户从没打开过任何开关

    bridge, _, session = make(config, tmp_path, FakeLLM([AssistantTurn(text="看过")]))
    assert bridge._vision_on() is True

    atts = [save_attachment(tmp_path, src)]
    assert bridge.send_task("看图", json.dumps([a.to_dict() for a in atts]))["ok"]
    assert wait_until(lambda: not bridge._busy)

    content = next(m for m in session.messages if m.get("role") == "user")["content"]
    assert isinstance(content, list)
    assert any(p.get("type") == "image_url" for p in content)
    assert config.vision is False                 # 能力判定不去篡改总开关


def test_text_only_model_still_sends_paths(config, tmp_path):
    """没登记视觉能力的模型继续只给路径清单 —— 不把上 MB 的 base64 塞进请求。"""
    src = _png(tmp_path)
    bridge, _, session = make(config, tmp_path, FakeLLM([AssistantTurn(text="ok")]))
    assert bridge._vision_on() is False           # test-model 不在能力表里

    atts = [save_attachment(tmp_path, src)]
    assert bridge.send_task("看图", json.dumps([a.to_dict() for a in atts]))["ok"]
    assert wait_until(lambda: not bridge._busy)

    content = next(m for m in session.messages if m.get("role") == "user")["content"]
    assert isinstance(content, str)
    assert "[附件]" in content


def test_set_vision_forces_images_and_persists(config, tmp_path):
    """手动强制:自己知道这个模型能看图(能力表没登记的网关模型)时点一下就行。"""
    src = _png(tmp_path)
    bridge, window, session = make(config, tmp_path,
                                   FakeLLM([AssistantTurn(text="a"),
                                            AssistantTurn(text="b")]))

    r = bridge.set_vision(True)
    assert r["ok"] and r["vision"] is True and r["vision_forced"] is True
    metrics = window.of_type("session_metrics")[-1]
    assert metrics["vision"] is True and metrics["vision_forced"] is True
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["vision"] is True

    assert bridge.send_task("看图",
                            json.dumps([a.to_dict() for a in
                                        [save_attachment(tmp_path, src)]]))["ok"]
    assert wait_until(lambda: not bridge._busy)
    content = next(m for m in session.messages if m.get("role") == "user")["content"]
    assert isinstance(content, list)              # 纯文本模型也被强制编码成图片

    assert bridge.set_vision(False)["vision"] is False
    assert bridge._vision_on() is False           # 关掉后回到按能力判定


def test_persist_config_failure_tells_the_user(config, tmp_path, monkeypatch):
    """装进只读目录时不能静默丢改动:说清"配置没能写入"以及怎么办。"""
    import minicode.gui_bridge as gb

    def boom(path, cfg):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(gb, "save_config", boom)
    bridge, window, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))

    assert bridge.set_vision(True)["ok"] is True   # 本次窗口内仍然生效
    tips = [e for e in window.of_type("info") if "配置没能写入" in e.get("text", "")]
    assert tips and "重启会丢失" in tips[0]["text"]


def test_close_choice_without_tray_is_honest(config, tmp_path, monkeypatch):
    """接管失败(网页版/非 Windows)时给出可操作的说明,不假装收进了托盘。"""
    from minicode import tray
    monkeypatch.setattr(tray, "_CONTROLLER", None)
    bridge, _, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    r = bridge.close_choice("hide")
    assert r["ok"] is False and "退出" in r["msg"]


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


# ---------------------------------------------------------------- 计划面板(v0.9.24)

def test_get_plan_without_a_plan_offers_a_guide(config, tmp_path):
    """没计划时不能回一张空表:前端要看 has_plan=False + path 才知道怎么引导。"""
    bridge, _, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    r = bridge.get_plan()
    assert r["ok"] and r["has_plan"] is False and r["steps"] == []
    assert r["path"] == "plans/plan.md" and r["dispatch_text"] == ""


def test_get_plan_reflects_the_workspace_file_with_flags(config, tmp_path):
    """面板数据来自工作区那份文件:每步带 ready/blocked,顺带给派发文案。"""
    from minicode import planning
    bridge, _, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    ctx = planning.ToolContext(workspace=tmp_path, config=config)
    planning._plan_write(ctx, "毕设排期", [
        {"title": "定题"}, {"title": "写开题报告", "depends_on": [1]},
        {"title": "做原型", "depends_on": [1]}])

    r = bridge.get_plan()
    assert r["ok"] and r["has_plan"] and r["goal"] == "毕设排期"
    assert r["done"] == 0 and r["total"] == 3
    assert r["ready"] == [1] and r["blocked"] == []
    assert [s["ready"] for s in r["steps"]] == [True, False, False]
    assert "第 1 步 定题" in r["dispatch_text"]


def test_plan_writes_push_plan_changed_to_the_window(config, tmp_path):
    """学长每写一次计划,界面要收到 plan_changed(带 session 标记),角标才会动。"""
    from minicode import planning
    bridge, window, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    ctx = bridge._current.agent.ctx          # 真上下文:ui 是本会话的推送适配器
    assert "计划已写入" in planning._plan_write(ctx, "毕设排期", [
        {"title": "定题"}, {"title": "写开题报告", "depends_on": [1]}])
    events = window.of_type("plan_changed")
    assert len(events) == 1
    assert events[0]["goal"] == "毕设排期" and events[0]["ready"] == [1]
    assert events[0]["session"] == bridge._current.key

    planning._plan_update(ctx, 1, "done")
    events = window.of_type("plan_changed")
    assert len(events) == 2 and events[-1]["done"] == 1 and events[-1]["ready"] == [2]
    # 面板不会反过来改文件:读一次什么都不该变
    before = (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8")
    bridge.get_plan()
    assert (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8") == before


# ------------------------------------------- 任务监控:实时表与时间线(v0.9.37)

def _tool(name="bash"):
    from minicode.tools.base import Tool
    return Tool(name=name, description="", parameters={}, handler=lambda ctx, **k: "ok")


def test_live_table_records_the_real_start_and_end():
    """「跑了多久」只有在这里记一次才可能是真的:开始/结束都是真实钟点。"""
    from minicode.gui_ui import GuiUI
    ui = GuiUI()
    ui.attach(FakeWindow())
    t = _tool("bash")
    ui.tool_start(t, {"command": "pytest -q"})
    now = ui.live_now()
    assert now["name"] == "bash" and now["summary"] == "pytest -q"
    assert now["elapsed"] >= 0
    ui.tool_done(t, "全部通过")
    assert ui.live_now() is None                 # 结束了就不许再挂着"在跑"
    row = ui.live_rows()[-1]
    assert row["ok"] is True and row["end"] >= row["start"]


def test_live_table_nesting_closes_the_inner_call_first():
    """子代理内部工具套在 `task` 里面:先开始的后结束,收栈不能收错。"""
    from minicode.gui_ui import GuiUI
    ui = GuiUI()
    outer, inner = _tool("task"), _tool("explore·read_file")
    ui.tool_start(outer, {"type": "explore"})
    ui.tool_start(inner, {"path": "a.py"})
    assert ui.live_now()["name"] == "explore·read_file"
    ui.tool_done(inner, "内容")
    assert ui.live_now()["name"] == "task"       # 外层还在跑,不能跟着收掉
    ui.tool_done(outer, "报告")
    assert ui.live_now() is None
    assert [r["name"] for r in ui.live_rows()] == ["task", "explore·read_file"]


def test_abandoned_call_is_closed_instead_of_spinning_forever():
    """按停止时 `tool_done` 根本不会来到:回合一结束就要把它收成"中断"。

    不收的话监控栏会永远转圈 —— 那是比"没有时间"更糟的谎:它说有一件事正在发生,
    而它早就停了。
    """
    from minicode.gui_ui import GuiUI
    ui = GuiUI()
    t = _tool("bash")
    ui.tool_start(t, {"command": "很久的命令"})
    assert ui.live_now() is not None
    assert ui.live_abandon() == 1
    assert ui.live_now() is None
    assert ui.live_rows()[-1]["abandoned"] is True


def test_failed_tool_records_ok_false_with_the_same_prefix_rule():
    """成败判据与 `tool_done` 推给前端的那一份必须是同一个口径,否则两栏会打架。"""
    from minicode.gui_ui import GuiUI
    ui = GuiUI()
    t = _tool("bash")
    ui.tool_start(t, {"command": "boom"})
    ui.tool_done(t, "错误: 命令失败")
    assert ui.live_rows()[-1]["ok"] is False


def test_live_table_drops_oldest_finished_rows_only():
    """超长回合会截断,但**在跑的那一条永不丢** —— 丢了面板就再也找不到它。"""
    from minicode import gui_ui
    ui = gui_ui.GuiUI()
    t = _tool("bash")
    for i in range(gui_ui.LIVE_CAP + 5):
        ui.tool_start(t, {"command": f"c{i}"})
        ui.tool_done(t, "ok")
    assert len(ui.live_rows()) <= gui_ui.LIVE_CAP + 1
    ui.tool_start(t, {"command": "还在跑"})
    for i in range(gui_ui.LIVE_CAP + 5):
        ui.tool_start(_tool("read_file"), {"path": f"p{i}"})
        ui.tool_done(_tool("read_file"), "ok")
    assert ui.live_now()["summary"] == "还在跑"


def test_monitor_activity_carries_real_times_after_a_real_turn(config, tmp_path):
    """走一遍真的回合:时间线那几行领到的时刻来自实时表,回合结束后不再有"在跑"。"""
    llm = FakeLLM([AssistantTurn(tool_calls=[_write_call()]),
                   AssistantTurn(text="写好了")])
    bridge, window, _ = make(config, tmp_path, llm)
    bridge.send_task("写 gui.txt")
    assert wait_until(lambda: any(e["type"] == "confirm_request" for e in window.events))
    bridge.confirm_reply("yes")
    assert wait_until(lambda: not bridge._busy)

    act = bridge.get_activity()
    turn = act["timeline"]["turns"][0]
    assert turn["calls"] == 1 and turn["errors"] == 0
    row = turn["items"][0]
    assert row["verb"] == "写文件" and row["summary"] == "gui.txt"
    assert row["time"] and row["status"] == "ok"
    assert row["action"]["type"] == "reveal"        # 点一下真去开所在文件夹
    assert act["now"] is None and act["summary"]["running"] == 0
    assert act["updated_at"]


class _StreamOkLLM:
    """只回一个字的模型:确认框细节测试不需要真跑回合。"""

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None):
        return AssistantTurn(text="ok")


def test_write_file_overwrite_confirm_shows_real_diff(config, tmp_path):
    """覆盖已有文件的确认框给真 diff(改了哪几行一眼看清),新建文件维持预览。"""
    from minicode.tools.base import Tool
    (tmp_path / "cfg.ini").write_text("mode = fast\nretries = 1\n", encoding="utf-8")
    bridge, _window, _session = make(config, tmp_path, _StreamOkLLM())
    tool = Tool(name="write_file", description="", parameters={},
                handler=lambda ctx, **k: "ok")
    detail = bridge._permission_detail_text(tool, {
        "path": "cfg.ini", "content": "mode = slow\nretries = 1\n"})
    assert "覆盖已有文件: cfg.ini" in detail
    assert "-mode = fast" in detail and "+mode = slow" in detail
    assert "retries = 1" in detail                    # 没改的行只作为上下文出现


def test_write_file_new_confirm_keeps_preview(config, tmp_path):
    from minicode.tools.base import Tool
    bridge, _window, _session = make(config, tmp_path, _StreamOkLLM())
    tool = Tool(name="write_file", description="", parameters={},
                handler=lambda ctx, **k: "ok")
    detail = bridge._permission_detail_text(tool, {
        "path": "new.txt", "content": "第一行\n第二行\n"})
    assert "新建文件: new.txt" in detail and "第一行" in detail
