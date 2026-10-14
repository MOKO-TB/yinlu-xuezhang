# -*- coding: utf-8 -*-
"""语音输入的口径测试(v0.9.172+):能用要说清用谁,不能用要照实说缺什么。

与 `tests/test_ocr.py` 同一条纪律:这台机器上**没装** winrt 绑定,所以"缺件"
那条路是真跑的;其余走 monkeypatch 的假引擎,验的是**接线与自述**,
不是"Windows 听不听得准"。四种"没听到"各有各的实话 —— 一支一支钉。
"""
from __future__ import annotations

from minicode import voice


# ------------------------------------------------------------------ 缺件路径(真跑)

def test_without_bindings_probe_says_what_to_install():
    got = voice.voice_probe()
    if got["ready"]:                      # 装了绑定的机器上这条不成立,跳过断言
        assert got["engine"]
        return
    assert "winrt" in got["why"] and "pip install" in got["install_hint"], \
        "缺件必须给能照做的下一步(与 OCR 那条同一口径)"


def test_without_bindings_listen_never_fakes_a_result():
    got = voice.listen_once(seconds=1)
    if got["ok"]:                         # 装了绑定:这条由下面的假引擎用例管
        return
    assert got["text"] == "" and "winrt" in got["why"], \
        "没装绑定就说没装,绝不返回空串装成功"


# ------------------------------------------------------------------ 假引擎

class _Status:
    def __init__(self, name):
        self.name = name


class _Conf:
    def __init__(self, name):
        self.name = name


class _Result:
    def __init__(self, status="success", text="你好", conf="high"):
        self.status = _Status(status)
        self.text = text
        self.confidence = _Conf(conf)


class _Recorder:
    """假 SpeechRecognizer:记下构造参数与超时设置,recognize_async().get() 回预设结果。"""

    def __init__(self, result=None, ctor_args=None):
        self._result = result or _Result()
        self._ctor_args = ctor_args if ctor_args is not None else []
        self.timeouts_initial_silence = None
        self.timeouts_inter_sentence_silence = None

    def recognize_async(self):
        rec = self

        class _Op:
            def get(self_inner):
                return rec._result
        return _Op()


def _fake_speech(monkeypatch, result=None, ctor_args=None):
    made: list = []
    rec = _Recorder(result, ctor_args)

    def factory(*a, **k):
        made.append((a, k))
        if ctor_args is not None:
            ctor_args.extend(a)
        return rec
    monkeypatch.setattr(voice, "_import_speech",
                        lambda: (factory, _Status, _Conf))
    return rec, made


def test_listen_returns_text_and_confidence(monkeypatch):
    rec, _ = _fake_speech(monkeypatch)
    got = voice.listen_once(seconds=5)
    assert got["ok"] and got["text"] == "你好"
    assert got["confidence"] == "high" and got["engine"]
    assert rec.timeouts_initial_silence == 5000, "开口等待按秒换算成毫秒"
    assert "待核对草稿" in got["warn"], "转出来的字只当草稿,这句必须跟着"


def test_every_not_heard_status_has_its_own_sentence(monkeypatch):
    cases = {
        "timeout": "超时",
        "audio_quality_failure": "听不清",
        "grammar_language_unavailable": "语音包",
        "user_canceled": "取消",
    }
    for status, needle in cases.items():
        _fake_speech(monkeypatch, _Result(status=status, text=""))
        got = voice.listen_once(seconds=1)
        assert not got["ok"] and needle in got["why"], (status, got["why"])
        assert got["text"] == ""


def test_rejected_confidence_is_not_forced_into_the_box(monkeypatch):
    """引擎自己都不敢认(rejected):宁可不给,也不塞一段"可能是错的"文字。"""
    _fake_speech(monkeypatch, _Result(conf="rejected", text="大概是这句"))
    got = voice.listen_once(seconds=1)
    assert not got["ok"] and got["text"] == "" and "不敢确认" in got["why"]


def test_success_with_empty_text_is_told_apart(monkeypatch):
    _fake_speech(monkeypatch, _Result(text="   "))
    got = voice.listen_once(seconds=1)
    assert not got["ok"] and "一个字都没有" in got["why"]


def test_language_argument_reaches_the_engine(monkeypatch):
    import sys
    import types
    fake_lang = types.SimpleNamespace(Language=lambda s: ("LANG", s))
    fake_mod = types.ModuleType("winrt.windows.globalization")
    fake_mod.Language = fake_lang.Language
    monkeypatch.setitem(sys.modules, "winrt.windows.globalization", fake_mod)
    monkeypatch.setitem(sys.modules, "winrt", types.ModuleType("winrt"))
    monkeypatch.setitem(sys.modules, "winrt.windows", types.ModuleType("winrt.windows"))
    rec, made = _fake_speech(monkeypatch)
    got = voice.listen_once(seconds=1, lang="zh-Hans-CN")
    assert got["ok"] and made and made[0][0] == (("LANG", "zh-Hans-CN"),)


# ------------------------------------------------------------------ 桥与墙

def test_bridge_forwards_and_the_web_wall_holds():
    from minicode.gui_bridge import PublicApi
    for n in ("voice_probe", "voice_listen"):
        assert callable(getattr(PublicApi, n, None)), n
    from minicode.web_profile import PublicFacade
    allowed = PublicFacade(object()).allowed_methods()
    assert "voice_listen" not in allowed and "voice_probe" not in allowed, \
        "访客没有麦克风这一层:两个名字都不进网页白名单"
