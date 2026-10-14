"""OCR 的口径测试:能用要说清用了谁,不能用要照实说缺什么。

这里刻意**不**依赖机器上真装了 winrt 绑定:除最后一条(有绑定才真跑)以外,
全部走 monkeypatch,验证的是"接线与自述",不是"Windows 认字准不准"。
"""
import json
import types

import pytest

from minicode import ocr
from minicode.config import Provider

FIXTURE = "tests/fixtures/ocr_cuoti.png"


def _cfg(*providers, active=None):
    return types.SimpleNamespace(providers=list(providers),
                                 provider=active or providers[0])


def _provider(pid="p1", key="sk-real", model="m-vision", enabled=True):
    p = Provider(id=pid, name=pid, base_url="https://x.test/v1",
                 api_key=key, model=model)
    p.enabled = enabled
    return p


class _Ctx:
    """工具层的 ctx 只要这两个属性。"""

    def __init__(self, workspace=".", config=None):
        self.workspace = workspace
        self.config = config


# ------------------------------------------------------------------ 文本清洗

def test_normalize_collapses_spaces_between_hanzi_only():
    assert ocr.normalize_system_text("已 知 函 数") == "已知函数"
    assert ocr.normalize_system_text("已 知 ， 求 它") == "已知，求它"
    # 拉丁字符之间的空格必须留着:那是 f 冈 / x ^ 2 这类误识别的样子,
    # 把它们粘起来等于把错字洗成看着对的,反而更坏。
    assert ocr.normalize_system_text("f 冈 = x ^ 2") == "f 冈 = x ^ 2"


# ------------------------------------------------------------------ 系统后端

def test_missing_bindings_says_what_to_install(monkeypatch):
    def boom():
        raise ocr.BackendMissing("本机没有 Windows OCR 的 Python 绑定")
    monkeypatch.setattr(ocr, "_import_system_modules", boom)
    assert ocr.system_ready() is False
    why = ocr.ocr_probe()["system"]["why"]
    assert "pip install" in why
    # 装不上的话后面全白搭:清单里必须带上实测缺不得的那几个(foundation.collections
    # 与 storage.streams 都是运行时才拉,漏了就是半路 ModuleNotFoundError)
    for dist in ocr.SYSTEM_DISTS:
        assert dist in why


def test_language_not_installed_falls_back_and_says_so(monkeypatch):
    """不猜系统能力:语言包不在可选清单里就退回,并把退回这件事讲出来。"""
    class Lang:
        def __init__(self, tag):
            self.language_tag = tag

    class FakeEngine:
        available_recognizer_languages = [Lang("zh-Hans-CN"), Lang("en-US")]

        @staticmethod
        def try_create_from_user_profile_languages():
            e = FakeEngine()
            e.recognizer_language = Lang("zh-Hans-CN")
            return e

        @staticmethod
        def try_create_from_language(lang):
            e = FakeEngine()
            e.recognizer_language = lang
            return e

    monkeypatch.setattr(ocr, "_import_system_modules",
                        lambda: (FakeEngine, None, None, None))
    _engine, used, note, available = ocr._create_engine(FakeEngine, "ja-JP")
    assert used == "zh-Hans-CN"
    assert "没装" in note and "ja-JP" in note
    assert available == ["zh-Hans-CN", "en-US"]
    _engine2, used2, note2, _a = ocr._create_engine(FakeEngine, "en-US")
    assert used2 == "en-US" and note2 == ""


def test_no_engine_at_all_degrades_without_inventing_text(monkeypatch):
    class Empty:
        available_recognizer_languages = []

        @staticmethod
        def try_create_from_user_profile_languages():
            return None

    monkeypatch.setattr(ocr, "_import_system_modules", lambda: (Empty, 1, 1, 1))
    with pytest.raises(ocr.BackendMissing) as e:
        ocr._create_engine(Empty, None)
    assert "没有可用" in str(e.value) and "语言和区域" in str(e.value)


# ------------------------------------------------------------------ 视觉后端

def test_provider_without_key_or_ruled_out_by_probe_is_skipped(monkeypatch):
    from minicode import model_probe
    monkeypatch.setattr(model_probe, "cached",
                        lambda model, base_url="", **kw: {"supports_vision": False}
                        if model == "m-blind" else None)
    cfg = _cfg(_provider(pid="nokey", key="", model="m-vision"),
               _provider(pid="blind", model="m-blind"),
               _provider(pid="good", model="m-vision"))
    targets = ocr._vision_targets(cfg)
    assert [t["model"] for t in targets] == ["m-vision"]
    assert targets[0]["provider"] == "good"


def test_recognize_vision_actually_puts_image_in_the_request(monkeypatch):
    from pathlib import Path
    from minicode import model_probe

    seen = {}

    def fake_post(base_url, path, payload, api_key, timeout):
        seen["path"] = path
        seen["payload"] = payload
        seen["key"] = api_key
        return 200, json.dumps({"choices": [{"message": {"content": "已知函数…"}}]})

    monkeypatch.setattr(model_probe, "_post", fake_post)
    res = ocr.recognize_vision(Path(FIXTURE), _cfg(_provider()))
    assert res["ok"] is True and res["text"] == "已知函数…"
    assert res["model"] == "p1/m-vision"
    assert seen["path"] == "/chat/completions" and seen["key"] == "sk-real"
    parts = seen["payload"]["messages"][0]["content"]
    kinds = [p.get("type") for p in parts]
    assert kinds == ["text", "image_url"], "图和指令必须一起发出去"
    assert parts[1]["image_url"]["url"].startswith("data:image")
    # 提示词里那句"别猜"是这功能不作恶的地方
    assert "不要按常识补全" in parts[0]["text"]


def test_vision_error_keeps_real_status_and_invents_nothing(monkeypatch):
    from pathlib import Path
    from minicode import model_probe

    monkeypatch.setattr(model_probe, "_post",
                        lambda *a, **k: (400, '{"error":{"message":"does not support image"}}'))
    res = ocr.recognize_vision(Path(FIXTURE), _cfg(_provider()))
    assert res["ok"] is False and res["text"] == ""
    assert "拒绝" in res["why"] or "不支持" in res["why"]


def test_200_with_empty_content_is_not_success(monkeypatch):
    from pathlib import Path
    from minicode import model_probe

    monkeypatch.setattr(model_probe, "_post",
                        lambda *a, **k: (200, '{"choices":[{"message":{"content":"  "}}]}'))
    res = ocr.recognize_vision(Path(FIXTURE), _cfg(_provider()))
    assert res["ok"] is False and "空" in res["why"]


def test_no_config_means_no_vision(monkeypatch):
    # 工具不能自己去 load_config():那是 CLI 入口,读不到就 sys.exit
    assert ocr.vision_ready(None)["ready"] is False
    with pytest.raises(ocr.BackendMissing) as e:
        ocr.recognize_vision(FIXTURE, None)
    assert "配置" in str(e.value)


# ------------------------------------------------------------------ 工具出口

def test_tool_output_without_any_backend_says_so(monkeypatch, tmp_path):
    def boom():
        raise ocr.BackendMissing("本机没有 Windows OCR 的 Python 绑定")
    monkeypatch.setattr(ocr, "_import_system_modules", boom)
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    out = ocr.ocr_image(_Ctx(workspace=str(tmp_path)), "x.png")
    assert "没有可用的 OCR 后端" in out
    assert "--- 识别文本 ---" not in out, "没认出来就不能摆个识别文本的架子"
    assert "下一步" in out


def test_tool_missing_file_points_at_workspace(tmp_path):
    out = ocr.ocr_image(_Ctx(workspace=str(tmp_path)), "没这个文件.png")
    assert "找不到这张图片" in out and str(tmp_path) in out


def test_unknown_backend_rejected(monkeypatch, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    res = ocr.recognize(img, backend="magic")
    assert res["ok"] is False and "不认识的后端" in res["why"]


def test_relative_path_goes_to_the_engine_as_absolute(monkeypatch, tmp_path):
    """StorageFile 收到相对路径会以一句看不懂的 OSError 收场(实测),
    所以 recognize() 必须先把路径绝对化再交给后端。"""
    seen = {}

    def fake(path, lang=None):
        seen["path"] = path
        return {"ok": True, "text": "占位", "backend": "system"}

    monkeypatch.setattr(ocr, "recognize_system", fake)
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        (tmp_path / "rel.png").write_bytes(b"\x89PNG")
        res = ocr.recognize("rel.png", backend="system")
    finally:
        os.chdir(cwd)
    assert res["ok"] is True
    assert seen["path"].is_absolute() and seen["path"].name == "rel.png"


def test_auto_records_why_the_first_backend_was_skipped(monkeypatch, tmp_path):
    """系统引擎挂了退到视觉模型时,"为什么退"要留在结果里,不能悄悄消失。"""
    from minicode import model_probe

    def boom():
        raise ocr.BackendMissing("没装绑定")
    monkeypatch.setattr(ocr, "_import_system_modules", boom)
    monkeypatch.setattr(model_probe, "_post",
                        lambda *a, **k: (200, json.dumps(
                            {"choices": [{"message": {"content": "答：最小值为 2"}}]})))
    img = tmp_path / "x.png"
    img.write_bytes(open(FIXTURE, "rb").read())
    out = ocr.ocr_image(_Ctx(workspace=str(tmp_path), config=_cfg(_provider())),
                        "x.png", backend="auto")
    assert "视觉模型" in out and "答：最小值为 2" in out
    assert "没装绑定" in out, "降级过程本身也要可追"
    assert "请按图核对" in out


def test_long_text_is_clipped_not_dropped(monkeypatch, tmp_path):
    from minicode import model_probe
    monkeypatch.setattr(model_probe, "_post",
                        lambda *a, **k: (200, json.dumps(
                            {"choices": [{"message": {"content": "字" * 300}}]})))
    img = tmp_path / "x.png"
    img.write_bytes(open(FIXTURE, "rb").read())
    out = ocr.ocr_image(_Ctx(workspace=str(tmp_path), config=_cfg(_provider())),
                        "x.png", backend="vision", max_chars=100)
    assert "已截到 100 字" in out and "300" in out


# ------------------------------------------------------------------ 真跑一条

def test_real_windows_engine_reads_the_fixture(tmp_path):
    """装了绑定就真认一遍:这一条不通就别提"OCR 可用"。"""
    if not ocr.system_ready():
        pytest.skip("本机未装 winrt-* 绑定(见 ocr.SYSTEM_DISTS),系统引擎这条跑不了")
    from pathlib import Path
    res = ocr.recognize(Path(FIXTURE), backend="system")
    assert res["ok"] is True, res.get("why")
    assert res["backend"] == "system" and res["engine"]
    assert "最小值" in res["text"]
    assert len(res["lines"]) >= 2
    assert "不可靠" in res["warn"], "离线引擎必须自带可靠性告警"


# ------------------------------------------------------------------ PDF 分支(v0.9.172+)

def _pdf_with_image(tmp_path, pages=1, size=(800, 1200)):
    """造一份"扫描版"PDF:Pillow 把图排成 PDF 页(每页就是一张整页图)。

    用 Pillow 生成而不是往仓库塞二进制 —— 两份依赖(Pillow/pypdf)都在,
    fixture 自己就能长出来。
    """
    from PIL import Image
    imgs = []
    for i in range(pages):
        im = Image.new("RGB", size, "white")
        imgs.append(im)
    p = tmp_path / "scan.pdf"
    imgs[0].save(str(p), "PDF", save_all=True, append_images=imgs[1:])
    return p


def _blank_pdf(tmp_path):
    from pypdf import PdfWriter
    p = tmp_path / "vector.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    w.write(str(p))
    return p


def test_pdf_pages_extract_the_page_image(tmp_path):
    pdf = _pdf_with_image(tmp_path, pages=2)
    pages, skipped, total, err = ocr._pdf_page_images(pdf)
    assert err == "" and total == 2
    assert [x["page"] for x in pages] == [1, 2], "两页各抽到一张页图"
    assert all(len(x["data"]) > ocr.PDF_IMAGE_MIN_BYTES for x in pages)
    assert skipped == []


def test_pdf_without_images_is_told_apart_not_silently_skipped(tmp_path):
    """矢量/带文字层的 PDF:没有页图就**如实说**"走 pdf 技能抽文本",
    绝不返回一份空的"识别完成"。"""
    pdf = _blank_pdf(tmp_path)
    pages, skipped, total, err = ocr._pdf_page_images(pdf)
    assert err == "" and total == 1 and pages == []
    assert skipped and "没有可抽取的图" in skipped[0] and "pdf 技能" in skipped[0]
    out = ocr.ocr_pdf(_Ctx(), pdf)
    assert "没有可抽取的页图" in out and "pdf 技能" in out


def test_ocr_pdf_marks_pages_and_reports_failures_honestly(tmp_path, monkeypatch):
    pdf = _pdf_with_image(tmp_path, pages=2)

    def fake_recognize(path, *, backend="auto", lang=None, cfg=None):
        # 第一页成功、第二页失败:失败的**只报那一页**
        state = getattr(fake_recognize, "n", 0) + 1
        fake_recognize.n = state
        if state == 1:
            return {"ok": True, "text": "第一章 绪论", "backend": "system",
                    "engine": "fake", "elapsed_ms": 12, "lines": 1}
        return {"ok": False, "why": "引擎认不出"}

    monkeypatch.setattr(ocr, "recognize", fake_recognize)
    out = ocr.ocr_pdf(_Ctx(), pdf, max_chars=6000)
    assert "--- 第 1 页 ---" in out and "第一章 绪论" in out
    assert "没认出来的页" in out and "第 2 页" in out
    assert "OCR 完成(PDF 扫描版)" in out and "2 页有图、1 页认出文字" in out


def test_ocr_pdf_without_any_backend_says_what_is_missing(tmp_path, monkeypatch):
    pdf = _pdf_with_image(tmp_path, pages=1)
    monkeypatch.setattr(ocr, "recognize",
                        lambda *a, **k: {"ok": False, "why": "两条都没走通"})
    out = ocr.ocr_pdf(_Ctx(), pdf)
    assert "没有可用的 OCR 后端" in out
    assert "系统引擎" in out and "视觉模型" in out, "缺什么要逐条说清"


def test_ocr_image_routes_pdf_to_the_pdf_chain(tmp_path, monkeypatch):
    """同一个出口:`ocr_image` 收到 .pdf 就交给 PDF 链(工具名与参数面不变)。"""
    pdf = _pdf_with_image(tmp_path, pages=1)
    seen = {}

    def fake_pdf(ctx, p, *, backend="auto", lang="", max_chars=6000):
        seen.update({"path": str(p), "backend": backend, "lang": lang})
        return "PDF 链的产物"

    monkeypatch.setattr(ocr, "ocr_pdf", fake_pdf)
    out = ocr.ocr_image(_Ctx(workspace=str(tmp_path)), "scan.pdf",
                        backend="vision", lang="zh-Hans-CN")
    assert out == "PDF 链的产物"
    assert seen["path"].endswith("scan.pdf") and seen["backend"] == "vision"
