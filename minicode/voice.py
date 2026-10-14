r"""语音输入(v0.9.172+):说一句 → 离线转成文字。

引擎是 Windows 自带的 **SpeechRecognizer**(`Windows.Media.SpeechRecognition`):
离线、不联网、不含模型权重(用的是系统语言包),与 `ocr.py` 那条系统引擎**同一族
winrt 绑定** —— 装法与缺件报错的口径都照它来,不另立一套。

三条口径(与 ocr.py 一致,一条都不放松):

1. **只做"听一句"**:起识别器 → 听一次 → 收口。不做常驻监听、不做唤醒词、
   不做热词 —— 那些是另一个产品,而且"一直在听"这件事本身要用户单独点头。
2. **没听到就说没听到**:超时、音频质量失败、引擎置信度 rejected、成功但空文本 ——
   四种各有各的实话,**绝不返回空串装成功**,更不猜一句填上。
3. **不落盘音频**:winrt 的识别器直接从默认输入设备取,本项目不写音频文件、
   不留副本;转出来的文字与 OCR 一样**只当待核对草稿**(口语、口音、同音词、
   专有名词都会错),发出去之前自己看一遍。
"""
from __future__ import annotations

import time

# 与 OCR 那份一样:本机实测的最小依赖集。少任何一个都会在半路 ModuleNotFoundError
# (SpeechRecognizer 的 Language 走 Globalization,集合类型走 Foundation.Collections)。
VOICE_DISTS = (
    "winrt-Windows.Media.SpeechRecognition",
    "winrt-Windows.Globalization",
    "winrt-Windows.Foundation",
    "winrt-Windows.Foundation.Collections",
)
INSTALL_HINT = "pip install " + " ".join(VOICE_DISTS)

VOICE_WARN = ("系统语音识别对口语、口音、同音词与专有名词都会错 —— "
              "这段文字是待核对草稿,发出去之前自己看一遍。")

ENGINE_NAME = "Windows 语音识别(离线)"


class VoiceMissing(RuntimeError):
    """这台机器上真用不了语音识别。message 里必须是能照做的下一步。"""


def _import_speech():
    """取 winrt 投影;缺哪个就报哪个(不猜"大概能行")。"""
    try:
        from winrt.windows.media.speechrecognition import (
            SpeechRecognitionConfidence, SpeechRecognitionResultStatus,
            SpeechRecognizer)
    except ImportError as e:
        raise VoiceMissing(
            f"没装 Windows 语音识别的 winrt 绑定({e.name or 'winrt-*'})。"
            f"装法:{INSTALL_HINT}") from e
    return SpeechRecognizer, SpeechRecognitionResultStatus, SpeechRecognitionConfidence


def available() -> dict:
    """这台机器能不能听:这一层只探**绑定**(麦克风与语音包要到真听那一步才知道)。

    为什么不连麦克风一起探:探测本身会弹一次麦克风权限/占用设备,而"能不能听"
    在没点按钮之前并不重要 —— 报得越准越好的同时,别为了准去碰设备。
    """
    try:
        SpeechRecognizer, _status, _conf = _import_speech()
    except VoiceMissing as e:
        return {"ready": False, "why": str(e), "engine": ""}
    sys_lang = ""
    try:
        lang = getattr(SpeechRecognizer, "system_speech_language", None)
        sys_lang = str(getattr(lang, "display_name", "") or "")
    except Exception:
        pass            # 取不到系统语言不算"不能听":真听的时候引擎自己会挑
    return {"ready": True, "why": "", "engine": ENGINE_NAME,
            "system_language": sys_lang}


def _status_text(status, Status) -> str:
    """把 SpeechRecognitionResultStatus 翻成人话 —— 四种"没听到"各有各的原因。"""
    name = getattr(status, "name", str(status or "")).lower()
    if name == "timeout":
        return "这段时间里没听到说话(超时)"
    if name == "audio_quality_failure":
        return "听到了声音但听不清(音频质量问题 —— 离麦克风近一点、环境安静些再试)"
    if name == "user_canceled":
        return "这次听被取消了"
    if name in ("topic_language_unavailable", "grammar_language_unavailable"):
        return "系统里没装这门语言的语音包(设置 → 时间和语言 → 语音里加一个)"
    if name in ("grammar_compilation_failed", "grammar_mismatched_language"):
        return f"语音引擎的语法装不上({name})"
    return f"识别没成({name or '未知状态'})"


def listen_once(*, seconds: float = 8.0, lang: str = "") -> dict:
    """听一句并转文字。回 {ok,text,engine,confidence,why,warn,elapsed_ms}。

    `seconds` 是**等你开口**的上限(静音超时);一开口之后的收句等待由引擎自己定。
    """
    started = time.perf_counter()
    out: dict = {"ok": False, "text": "", "engine": "", "confidence": "",
                 "why": "", "warn": VOICE_WARN, "elapsed_ms": 0}
    try:
        SpeechRecognizer, Status, Confidence = _import_speech()
    except VoiceMissing as e:
        out["why"] = str(e)
        return out
    out["engine"] = ENGINE_NAME
    try:
        if lang:
            from winrt.windows.globalization import Language
            rec = SpeechRecognizer(Language(str(lang)))
        else:
            rec = SpeechRecognizer()
        # 开口等待按秒给(接口单位是毫秒);收句等待短一点,别让用户说完还干等
        rec.timeouts_initial_silence = int(max(1.0, float(seconds)) * 1000)
        rec.timeouts_inter_sentence_silence = 800
        result = rec.recognize_async().get()      # winrt 的 IAsyncOperation:get() 阻塞等结果
    except Exception as e:
        out["why"] = (f"起不了识别器或读不到麦克风({type(e).__name__}: {e}) —— "
                      "检查系统的麦克风权限与默认输入设备")
        return out
    status = getattr(result, "status", None)
    text = str(getattr(result, "text", "") or "").strip()
    conf = getattr(result, "confidence", None)
    out["confidence"] = str(getattr(conf, "name", conf or "")).lower()
    if status is not None and getattr(status, "name", "") != "success":
        out["why"] = _status_text(status, Status)
        return out
    if not text:
        out["why"] = "引擎说识别成功了,但一个字都没有 —— 再说一遍试试"
        return out
    if out["confidence"] == "rejected":
        # 引擎自己都不敢认:宁可不给,也不塞一段"可能是错的"文字进输入框
        out["why"] = ("引擎不敢确认这句(置信度 rejected)—— "
                      "没敢把可能是错的话填进输入框,再说一遍试试")
        return out
    out.update(ok=True, text=text)
    out["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return out


def voice_probe() -> dict:
    """给界面/诊断看的一屏:引擎在不在、缺什么、系统语言是什么。"""
    got = available()
    return {"ok": bool(got["ready"]), **got,
            "install_hint": "" if got["ready"] else INSTALL_HINT}
