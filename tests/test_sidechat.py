# -*- coding: utf-8 -*-
"""侧聊线程(v0.9.160):origin=sidechat 的后台会话,不抢视角、不进侧栏。

这一族钉的是**分离本身**:侧聊开了之后主聊天区一个字都不动(视角不被抢)、
侧栏/托盘的清单里没有它(隐藏靠 origin 标记,**序号照全量发**——归档那次
的教训,重排坐标等于把别人的门牌拆了)、发话走的是与主会话**同一套**
忙/闲判据(忙=插话信箱,闲=新回合,不另造第二语义)。
"""
from __future__ import annotations

import json
import time


from minicode.config import Config, Provider
from minicode.ui import TerminalUI


def _config() -> Config:
    return Config(
        providers=[Provider(id="t", name="T", base_url="https://x.invalid/v1",
                            api_key="k", model="m",
                            models=[{"name": "m", "enabled": True}])],
        active_provider_id="t", context_window=8192)


class RecordingUI(TerminalUI):
    """push 事件留底:侧栏清单、busy 流转都从这里断言。"""

    def __init__(self, cfg, ws):
        super().__init__(cfg, ws)
        self.events = []

    def push(self, _type, **payload):
        self.events.append((_type, payload))


def _bridge(tmp_path):
    from minicode.gui_bridge import Bridge
    cfg = _config()
    # sessions_dir 显式进临时目录:懒创建的留档文件跟着这个目录走,
    # 绝不许因为默认值摸到真实用户数据
    return Bridge(cfg, tmp_path, RecordingUI(cfg, tmp_path),
                  sessions_dir=tmp_path / "sessions")


def _sessions_pushed(bridge):
    return [p for t, p in bridge.ui.events if t == "sessions"][-1]["items"]


class StubAgent:
    """替掉 runner.agent:run_turn 把一问一答真写进 session(留档懒创建,
    不写消息连文件都没有),信箱收插话,别的不装。"""

    def __init__(self, session=None):
        self.session = session
        self.turns = []
        self.inbox = []
        self.inbox_max = 99

    def run_turn(self, text, content=None, add_user=True):
        self.turns.append(text)
        if self.session is not None:
            if add_user:
                self.session.add_user(text)
            self.session.add_assistant(f"答:{text}")

    def queue_interjection(self, text):
        if len(self.inbox) >= self.inbox_max:
            return False
        self.inbox.append(text)
        return True


def _stubify(runner) -> StubAgent:
    stub = StubAgent(session=runner.session)
    runner.agent = stub
    return stub


# ------------------------------------------------------------- 开线程

def test_sidechat_new_does_not_steal_the_view(tmp_path):
    """开侧聊 = 后台多一个会话,**当前视角一根手指都不动** —— 主聊天区
    不清屏、不切换,这是侧聊与「新建任务」的分界线。"""
    b = _bridge(tmp_path)
    before = b._current.key
    got = b.sidechat_new()
    assert got["ok"] and got["key"]
    assert b._current.key == before, "侧聊不许抢当前视角"
    assert got["key"] in b._runners
    # 来源标记住在会话本体上(落盘懒创建,标记必须等到建文件那刻随
    # session_start 一起落)—— 本体在,第一句话一落地它就在留档里
    assert b._runners[got["key"]].session.origin == "sidechat"
    # 还没发过话:留档连文件都没有,file 是空串,key 才是主键
    assert got["file"] == ""


def test_sidechat_is_hidden_from_sidebar_but_indexes_stay_stable(tmp_path):
    """侧栏清单看不到它,但**序号是全量发的** —— 归档那条教训:在这里重排
    等于把 /resume 与侧栏搜索的坐标弄破。跳过 = 不显示,不是从名单消失。

    形状上要能区分"过滤后重排"与"跳过保号":让侧聊的留档**比**主会话的
    新(全量序在前),过滤后主会话的 index 必须仍是 2(全量坐标),
    而不是被重排成 1。"""
    b = _bridge(tmp_path)
    b.new_task()
    _stubify(b._current)
    b.send_task("主会话先落个档")
    b._current.thread.join(5)
    time.sleep(0.05)                                # 留档 mtime 拉开,排序才立得住
    sc = b.sidechat_new()
    runner = b._runners[sc["key"]]
    _stubify(runner)
    r = b.sidechat_send(sc["key"], "侧聊后落个档")
    b._runners[r["key"]].thread.join(5)
    side_file = runner.log_path.name
    items = _sessions_pushed(b)
    assert all(it["file"] != side_file for it in items), \
        "发过话的侧聊也不许出现在侧栏里"
    main_file = b._current.log_path.name
    mine = [it for it in items if it["file"] == main_file]
    assert mine and mine[0]["index"] == 2, \
        f"主会话的坐标必须保持全量序(侧聊占 1):{items}"


def test_tray_recent_skips_sidechat(tmp_path):
    b = _bridge(tmp_path)
    b.sidechat_new()
    assert b.recent_for_tray() == [], "托盘最近清单同样不列侧聊"


# ------------------------------------------------------------- 发话

def test_send_runs_a_turn_without_touching_the_view(tmp_path):
    b = _bridge(tmp_path)
    b.new_task()
    got = b.sidechat_new()
    runner = b._runners[got["key"]]
    _stubify(runner)
    r = b.sidechat_send(got["key"], "帮我想个标题")
    assert r["ok"] and r["key"] == got["key"]
    runner.thread.join(5)
    assert runner.agent.turns == ["帮我想个标题"]
    assert b._current.key != got["key"], "回合跑在侧聊里,视角不该被带过去"


def test_send_while_busy_queues_an_interjection(tmp_path):
    """忙 = 插话进信箱 —— 与主会话 send_task 同一只 queue_interjection,
    不另造第二套"排队"语义。"""
    b = _bridge(tmp_path)
    got = b.sidechat_new()
    runner = b._runners[got["key"]]
    _stubify(runner)
    runner.busy = True                       # 假装正在跑(不真起线程)
    r = b.sidechat_send(got["key"], "等你说完补一句")
    assert r["ok"] and r.get("queued")
    assert runner.agent.inbox == ["等你说完补一句"]


def test_send_to_unknown_thread_is_an_honest_no(tmp_path):
    b = _bridge(tmp_path)
    assert not b.sidechat_send("s999", "在吗")["ok"]
    assert not b.sidechat_send("../../etc/passwd", "hi")["ok"], "路径形状不收"


# ------------------------------------------------------------- 冷线程

def test_cold_thread_survives_restart_and_wakes_on_read(tmp_path):
    """侧聊是**留档里的会话**,不是内存里的气泡:重启之后从磁盘唤醒,
    历史照读,接着聊不用从头再来。"""
    b = _bridge(tmp_path)
    got = b.sidechat_new()
    runner = b._runners[got["key"]]
    _stubify(runner)
    b.sidechat_send(got["key"], "第一句话")
    runner.thread.join(5)
    # 第一句落地 = 留档懒创建发生(send 应答时文件名可能还没出生,join 之后才有)
    fname = runner.log_path.name
    meta = json.loads((tmp_path / "sessions" / fname).read_text(
        encoding="utf-8").splitlines()[0])
    assert meta.get("origin") == "sidechat"
    # 新桥(=重启):内存里没有它,读线程要能从留档唤醒
    b2 = _bridge(tmp_path)
    assert b2._sidechat_resolve(fname) is None, "重启后它应该是冷的"
    r = b2.sidechat_read(fname)
    assert r["ok"] and r["file"] == fname
    texts = [m["text"] for m in r["messages"] if m["role"] == "user"]
    assert "第一句话" in " ".join(texts)
    # 唤醒后它就是活的:接着发话不用再开
    runner2 = b2._runners[r["key"]]
    _stubify(runner2)
    r2 = b2.sidechat_send(r["key"], "第二句话")
    assert r2["ok"]
    runner2.thread.join(5)
    assert runner2.agent.turns == ["第二句话"]


def test_threads_lists_sidechat_only_with_live_state(tmp_path):
    b = _bridge(tmp_path)
    b.new_task()                                  # 主会话:也落个档,才好断"没混进来"
    _stubify(b._current)
    b.send_task("主任务")
    b._current.thread.join(5)
    main_file = b._current.log_path.name
    got = b.sidechat_new()
    runner = b._runners[got["key"]]
    _stubify(runner)
    b.sidechat_send(got["key"], "给个标题")
    runner.thread.join(5)
    lst = b.sidechat_threads()
    files = [t["file"] for t in lst["threads"]]
    assert runner.log_path.name in files
    assert all(t["file"] != main_file for t in lst["threads"]), \
        "主会话不许混进侧聊清单"
    mine = [t for t in lst["threads"] if t["file"] == runner.log_path.name][0]
    assert mine["key"] == got["key"] and mine["busy"] is False
    assert mine["title"], "第一句问话派生的标题在"


# ------------------------------------------------------------- 门面与墙

def test_facade_exposes_sidechat_methods():
    from minicode.gui_bridge import PublicApi
    for n in ("sidechat_new", "sidechat_threads", "sidechat_read",
              "sidechat_send"):
        assert callable(getattr(PublicApi, n, None)), n


def test_web_facade_has_none_of_it():
    """访客不聊侧聊:四个名字一个都不进网页白名单。"""
    from minicode.web_profile import PublicFacade
    allowed = PublicFacade(object()).allowed_methods()
    leaked = [n for n in ("sidechat_new", "sidechat_threads",
                          "sidechat_read", "sidechat_send") if n in allowed]
    assert not leaked, f"网页版白名单漏了:{leaked}"
