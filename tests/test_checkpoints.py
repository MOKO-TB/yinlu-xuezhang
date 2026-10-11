"""改动前像落盘(v0.9.131):往返、上限、坏文件,以及那条"宁可少退不可退错"的界。"""
from __future__ import annotations

import json
from pathlib import Path

from minicode import checkpoints
from minicode.checkpoints import (CKPT_MAX_FILE_BYTES, CKPT_MAX_RECORDS,
                                  decode_stack, encode_stack, load, save)


def _rec(seq=1, path="a.py", data=b"old\n", **kw):
    row = {"seq": seq, "path": Path(path), "data": data, "display": path,
           "at": 100.0, "post": "sha:abc", "turn": 2, "epoch": 0}
    row.update(kw)
    return row


# ------------------------------------------------------------------ 往返

def test_round_trip_keeps_bytes_and_counters():
    stack = [_rec(1), _rec(2, path="b/c.md", data="中文原文".encode("utf-8"))]
    rows, seq, epoch = decode_stack(encode_stack(stack, 2, 0))
    assert seq == 2 and epoch == 0
    assert [r["path"] for r in rows] == [Path("a.py"), Path("b/c.md")]
    assert rows[1]["data"] == "中文原文".encode("utf-8")
    assert rows[0]["post"] == "sha:abc" and rows[0]["turn"] == 2


def test_new_file_round_trips_as_none_not_empty_bytes():
    """`data is None` 的含义是"当时不存在"→ 回退要删掉它,不能退成空文件。"""
    rows, _seq, _ep = decode_stack(encode_stack([_rec(1, data=None)], 1, 0))
    assert rows[0]["data"] is None


def test_binary_prior_survives():
    blob = bytes(range(256))
    rows, _, _ = decode_stack(encode_stack([_rec(1, data=blob)], 1, 0))
    assert rows[0]["data"] == blob


# ------------------------------------------------ 那条安全边界(最要紧的一支)

def test_oversized_prior_is_dropped_whole_not_stored_as_null():
    """存不下就**整条不要**。

    若退化成 `data=None` 落盘,读回来就成了"这个文件当时不存在",按那个含义回退会
    删掉用户现在的文件 —— 那是拿数据去赌,所以宁可清单上少一条。
    """
    big = b"x" * (CKPT_MAX_FILE_BYTES + 1)
    stack = [_rec(1, data=big), _rec(2, path="ok.py", data=b"fine")]
    rows, _seq, _ep = decode_stack(encode_stack(stack, 2, 0))
    assert [r["path"] for r in rows] == [Path("ok.py")]
    assert all(r["data"] is not None for r in rows)


def test_record_cap_drops_the_oldest():
    stack = [_rec(i, path=f"f{i}.py") for i in range(1, CKPT_MAX_RECORDS + 40)]
    rows, _seq, _ep = decode_stack(encode_stack(stack, 999, 0))
    assert len(rows) == CKPT_MAX_RECORDS
    assert rows[0]["seq"] == 40          # 最老的那 39 条被扔了(留最后 200 条)
    assert rows[-1]["seq"] == CKPT_MAX_RECORDS + 39


def test_total_size_cap_drops_from_the_front():
    stack = [_rec(i, path=f"g{i}.py", data=b"y" * 200_000) for i in range(1, 30)]
    text = encode_stack(stack, 30, 0)
    assert len(text) <= checkpoints.CKPT_MAX_TOTAL_BYTES + 400
    rows, _, _ = decode_stack(text)
    assert rows and rows[-1]["seq"] == 29    # 最新的一定留着:那才是他正要退的


# --------------------------------------------------------------- 坏文件

def test_garbage_file_reads_as_empty_and_never_raises(tmp_path):
    p = tmp_path / "checkpoints" / "s.json"
    p.parent.mkdir(parents=True)
    p.write_text("{ 这不是 json", encoding="utf-8")
    assert load(p) == ([], 0, 0)


def test_wrong_version_reads_as_empty():
    text = json.dumps({"version": 999, "seq": 5, "epoch": 1, "records": []})
    assert decode_stack(text) == ([], 0, 0)


def test_one_broken_row_does_not_take_the_whole_ledger_down():
    good = json.loads(encode_stack([_rec(1)], 1, 0))
    good["records"].append({"seq": 2, "path": "x.py", "existed": True,
                            "b64": "@@@非法 base64@@@"})
    rows, _seq, _ep = decode_stack(json.dumps(good))
    assert [r["seq"] for r in rows] == [1]


def test_missing_file_is_not_an_error(tmp_path):
    assert load(tmp_path / "nope.json") == ([], 0, 0)


# ---------------------------------------------------------------- 落盘本身

def test_save_then_load_round_trip(tmp_path):
    p = tmp_path / "checkpoints" / "sess_1.json"
    save(p, [_rec(1), _rec(2, path="b.py")], 2, 0)
    rows, seq, _ep = load(p)
    assert len(rows) == 2 and seq == 2
    assert not list(p.parent.glob("*.tmp")), "临时文件没被换掉"


def test_saving_an_empty_stack_removes_the_file(tmp_path):
    """账本空了还留一份旧文件 = 下次开机把退掉的东西又捞回来。"""
    p = tmp_path / "checkpoints" / "sess_1.json"
    save(p, [_rec(1)], 1, 0)
    assert p.is_file()
    save(p, [], 1, 0)
    assert not p.exists()


def test_save_with_no_path_is_a_noop(tmp_path):
    save(None, [_rec(1)], 1, 0)      # 不炸就是对的:没有留档就不该有账本


# ------------------------------------------------------------- Agent 接线

def _agent(tmp_path, monkeypatch):
    from minicode.agent import Agent
    from minicode.config import Config, Provider
    from minicode.llm import AssistantTurn
    from minicode.session import Session
    from minicode.tools.base import Tool, ToolContext

    monkeypatch.setattr("minicode.paths.APP_DIR", tmp_path / "app", raising=False)
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")], active_provider_id="t")
    s = Session(system_prompt="s", log_dir=tmp_path / "app" / "sessions")
    wrote = []

    def write_tool(ctx, path="a.py", content=""):
        p = Path(ctx.workspace) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        wrote.append(path)
        return f"已创建文件 {path}(1 行,{len(content)} 字符)。"

    tool = Tool(name="write_file", description="", read_only=False,
                handler=write_tool,
                parameters={"type": "object", "properties": {}})

    class _Allow:
        ask_note = ""
        ask_kind = ""
        project_allow_reason = ""

        def new_turn(self):
            pass

        def check(self, tool, args):
            return "allow"

        def approve_for_turn(self, name):
            pass

        def approve_outside(self, tool, args, session=False):
            pass

    class _Llm:
        def __init__(self, n):
            self.n = n

        def stream_chat(self, messages, tools, **kw):
            self.n += 1
            from minicode.llm import ToolCall
            return AssistantTurn(tool_calls=[ToolCall(
                id=f"c{self.n}", name="write_file",
                arguments=json.dumps({"path": f"f{self.n}.py",
                                      "content": "x = 1\n"}))])

    a = Agent(llm=_Llm(0), registry=[tool], session=s, gate=_Allow(),
              ui=_Quiet(), ctx=ToolContext(workspace=tmp_path / "ws", config=cfg),
              max_steps=4)
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
    return a, cfg


class _Quiet:
    def info(self, text):
        pass

    def tool_start(self, tool, args):
        pass

    def tool_done(self, tool, result):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def test_checkpoints_are_written_before_the_tool_runs(tmp_path, monkeypatch):
    a, _cfg = _agent(tmp_path, monkeypatch)
    a.run_turn("写三个文件")
    assert a._ckpt_file and a._ckpt_file.is_file(), "账本没落盘"
    rows, seq, _ep = load(a._ckpt_file)
    # 每一步写一个文件,跑满 max_steps 就是 4 处 —— 断"落了几处"要跟着步数走,
    # 真正要钉的是"盘上的账本 == 内存里的账本"。
    assert len(a._undo_stack) >= 3
    assert len(rows) == len(a._undo_stack)
    assert seq == a._undo_seq == max(r["seq"] for r in rows)


def test_a_second_process_gets_the_ledger_back_and_can_undo(tmp_path, monkeypatch):
    """模拟"重启后再退":第二个 Agent 实例读回同一本账,退的文件真的回到原样。"""
    a1, cfg = _agent(tmp_path, monkeypatch)
    a1.run_turn("写文件")
    first = a1._undo_stack[0]
    target = Path(first["path"])
    assert target.read_text(encoding="utf-8") == "x = 1\n"

    a2, _cfg = _agent(tmp_path, monkeypatch)          # 新实例 = 重启
    assert a2._undo_stack == []                       # 还没读
    a2.run_turn("再写一个")                            # 第一问之前把账读回来
    seqs = [r["seq"] for r in a2._undo_stack]
    assert 1 in seqs, "旧账没恢复"
    assert a2._epoch == first["epoch"]                # 纪元一起还原
    # 恢复出来的记录不该被判成 stale(那等于持久化白做)
    assert not a2.undo_records()["items"][0].get("stale")


def test_the_loader_is_actually_called(monkeypatch):
    """钉"调用点存在",不是"函数存在"。

    这仓库栽过一次:函数、配置、文档、测试全写了,唯独没人调用,于是真机上永远不生效。

    **v0.9.151 起这一格要扫两个函数**:`run_turn` 被拆成了"外壳 + `_run_turn_body`"
    —— 外壳里那个 `finally` 是 `stop` 钩子的落点(见那一对函数的 docstring),于是
    这一轮的主体搬进了 `_run_turn_body`。**意图一个字没变**:钉的还是"这一步真被
    调用了";变的只是"它写在哪个函数里"。所以这里跟着扫两个 —— **而不是把断言搬走**:
    哪天真有人把 `_load_checkpoints()` 从主体里删掉,这一支照样红;而"只扫 `run_turn`"
    那种改法会让它永远绿着,那正是这一支要防的形状。
    """
    src = Path("minicode/agent.py").read_text(encoding="utf-8")

    def _slice(name: str) -> str:
        start = src.index(f"    def {name}(")
        return src[start:src.index("    def ", start + 10)]

    body = _slice("run_turn") + _slice("_run_turn_body")
    assert "self._load_checkpoints()" in body


def test_ship_never_and_mydata_both_know_the_directory():
    from minicode import mydata, state_files
    names = {f.name for f in state_files.SHIP_NEVER}
    assert "checkpoints" in names
    assert any(e.key == "checkpoints" for e in mydata.TABLE)
    entry = next(e for e in mydata.TABLE if e.key == "checkpoints")
    assert entry.pack is False, "前像换台机器去退没有意义,绝不能进迁移包"


def test_sweep_orphans_drops_ledgers_whose_session_is_gone(tmp_path, monkeypatch):
    """留档删了、前像还在 = 同学的课件原文无声地留在盘上。"""
    from minicode import checkpoints as ck
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "session_a.jsonl").write_text("{}", encoding="utf-8")
    here = tmp_path / "app" / "checkpoints"
    here.mkdir(parents=True)
    monkeypatch.setattr("minicode.paths.APP_DIR", tmp_path / "app", raising=False)
    (here / "session_a.json").write_text("{}", encoding="utf-8")
    (here / "session_gone.json").write_text("{}", encoding="utf-8")
    assert ck.sweep_orphans(sessions) == 1
    assert (here / "session_a.json").is_file()
    assert not (here / "session_gone.json").exists()


def test_the_sweep_is_actually_called():
    src = Path("minicode/app_setup.py").read_text(encoding="utf-8")
    assert "checkpoints.sweep_orphans(" in src
