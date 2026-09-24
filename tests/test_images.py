"""图片生成的口径测试:接口一条、本地排版一条,不通时只许说缺什么。

和 test_ocr.py 同一套打法 —— 这轮**没有真文生图 Key 可测**,所以全部用假 POST
钉住"请求发出去长什么样、返回怎么解",真端点行不行由工具回显的 status 说话,
测试不替它背书。
"""
import base64
import json
import types

import pytest

from minicode import images
from minicode.config import Provider

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _cfg(*providers, images_cfg=None):
    return types.SimpleNamespace(
        providers=list(providers), provider=providers[0] if providers else None,
        capabilities={"images": images_cfg} if images_cfg is not None else {})


def _provider(pid="img", key="sk-borrow-me", model="any"):
    return Provider(id=pid, name=pid, base_url="https://x.test/v1",
                    api_key=key, model=model)


GOOD_ENTRY = [{"provider_id": "img", "model": "text-to-image-xl",
               "size": "768x1024"}]


class _Ctx:
    def __init__(self, workspace=".", config=None):
        self.workspace = workspace
        self.config = config


@pytest.fixture
def ctx(tmp_path):
    return _Ctx(workspace=tmp_path, config=_cfg(_provider(), images_cfg=GOOD_ENTRY))


def _fake_post(monkeypatch, payload_box, status=200, body=None):
    """假 POST:只记录"发出去的请求长什么样",不真联网。"""
    from minicode import model_probe

    def fake(base_url, path, payload, api_key, timeout):
        payload_box.update(base_url=base_url, path=path, payload=payload,
                           api_key=api_key, timeout=timeout)
        return status, json.dumps(body if body is not None
                                  else {"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
    monkeypatch.setattr(model_probe, "_post", fake)


# ------------------------------------------------------------------ 后端可用性

def test_no_config_means_no_api_and_says_what_is_missing():
    p = images.probe(_cfg(_provider()))
    assert p["api_ready"] is False and p["api"] == []
    assert "capabilities" in p["how"]            # 缺什么、怎么补,一次说清


def test_key_is_borrowed_from_the_provider_not_written_here():
    targets = images.api_targets(_cfg(_provider(), images_cfg=GOOD_ENTRY))
    assert targets[0]["ready"], targets[0]["why"]
    assert targets[0]["api_key"] == "sk-borrow-me"
    # 自述结构里不许带 Key(它会被显示/记录)
    assert "api_key" not in images.probe(_cfg(_provider(), images_cfg=GOOD_ENTRY))["api"][0]


def test_unfinished_config_explains_which_piece_is_missing():
    cfg = _cfg(_provider(pid="other", key=""), images_cfg=[{"provider_id": "ghost", "model": ""}])
    t = images.api_targets(cfg)[0]
    assert t["ready"] is False
    assert "ghost" in t["why"] and "model" in t["why"]


def test_local_backend_reports_font_and_pillow_separately():
    ok, why = images.local_ready()
    assert ok is True or "Pillow" in why          # 说不清就是 bug
    assert isinstance(why, str) and why


# ------------------------------------------------------------------ 接口请求形状

def test_api_request_carries_model_prompt_size_and_borrowed_key(ctx, monkeypatch):
    box = {}
    _fake_post(monkeypatch, box)
    out = images.image_generate(ctx, prompt="一张秋天的校园", backend="api")
    assert box["path"] == "/images/generations"
    assert box["payload"] == {"model": "text-to-image-xl", "prompt": "一张秋天的校园",
                              "n": 1, "size": "768x1024"}
    assert box["api_key"] == "sk-borrow-me"
    assert "images/" in out and "text-to-image-xl" in out
    assert "sk-borrow-me" not in out              # 回显里绝不带 Key


def test_api_error_status_is_passed_through_and_writes_nothing(ctx, tmp_path, monkeypatch):
    box = {}
    _fake_post(monkeypatch, box, status=403, body={"error": "quota"})
    out = images.image_generate(ctx, prompt="x", backend="api")
    assert "403" in out
    assert not (tmp_path / "images").exists() or list((tmp_path / "images").iterdir()) == []


def test_unexpected_response_shape_is_reported_not_invented(ctx, monkeypatch):
    box = {}
    _fake_post(monkeypatch, box, body={"result": "something else"})
    out = images.image_generate(ctx, prompt="x", backend="api")
    assert "data" in out and "没有出图" in out


def test_url_form_result_is_downloaded_and_saved(ctx, tmp_path, monkeypatch):
    box = {}
    _fake_post(monkeypatch, box, body={"data": [{"url": "https://cdn.test/a.png"}]})

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, _n):
            return PNG

    monkeypatch.setattr(images, "urlopen", lambda url, timeout: Resp())
    out = images.image_generate(ctx, prompt="x", backend="api", name="campus")
    saved = list((tmp_path / "images").glob("*.png"))
    assert len(saved) == 1 and saved[0].read_bytes() == PNG
    assert f"{len(PNG):,} 字节" in out


def test_oversized_download_is_refused(ctx, monkeypatch):
    box = {}
    _fake_post(monkeypatch, box, body={"data": [{"url": "https://cdn.test/big.png"}]})

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"x" * (n + 1)

    monkeypatch.setattr(images, "urlopen", lambda url, timeout: Resp())
    assert "超过" in images.image_generate(ctx, prompt="x", backend="api")


def test_empty_prompt_is_not_sent(ctx, monkeypatch):
    box = {}
    _fake_post(monkeypatch, box)
    assert "空的" in images.image_generate(ctx, prompt="  ", backend="api")
    assert box == {}                              # 没发出去才算数


# ------------------------------------------------------------------ 本地排版卡片

def test_card_renders_a_real_png_with_the_given_size(tmp_path):
    pytest.importorskip("PIL", reason="本地排版出图需要 Pillow")
    png = images.render_card("明天下午两点图书馆门口集合", title="集合通知",
                             width=800, height=1200)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    (tmp_path / "t.png").write_bytes(png)
    from PIL import Image
    with Image.open(tmp_path / "t.png") as im:
        assert im.size == (800, 1200)


def test_long_text_grows_the_canvas_instead_of_cutting_words(tmp_path):
    pytest.importorskip("PIL", reason="本地排版出图需要 Pillow")
    body = "\n".join(f"第 {i} 条注意事项" for i in range(30))
    png = images.render_card(body, width=800, height=600)
    (tmp_path / "g.png").write_bytes(png)
    from PIL import Image
    with Image.open(tmp_path / "g.png") as im:
        assert im.size[1] > 600                   # 画布长高了,不是字被裁了


def test_too_much_text_is_refused_rather_than_silently_clipped():
    pytest.importorskip("PIL", reason="本地排版出图需要 Pillow")
    with pytest.raises(images.ImageError) as e:
        images.render_card("很长的一行" * 4000, height=400)
    assert "文字太多" in str(e.value)


def test_card_needs_text_and_says_so(tmp_path):
    out = images.image_generate(_Ctx(workspace=tmp_path, config=_cfg()),
                                prompt="画一只猫", backend="card")
    assert "没有出图" in out and "text" in out
    assert not (tmp_path / "images").exists()


def test_auto_without_key_refuses_to_draw_an_illustration(tmp_path):
    """"画只猫"这类需求没接口时必须拒绝,而且不许偷偷改做成一张文字卡。"""
    cfg = _cfg(_provider(key=""))
    out = images.image_generate(_Ctx(workspace=tmp_path, config=cfg), prompt="画一只猫")
    assert "没有出图" in out
    assert "不会凭空画插图" in out and "capabilities" in out
    assert not (tmp_path / "images").exists()


def test_auto_falls_back_to_card_when_text_is_given(tmp_path):
    pytest.importorskip("PIL", reason="兜底走的是本地排版卡片")
    out = images.image_generate(_Ctx(workspace=tmp_path, config=_cfg()),
                                text="截止 9 月 30 日", title="缴费提醒")
    assert "文字卡片" in out and (tmp_path / "images").is_dir()
    assert "不是 AI 绘图" in out                   # 口径必须留在结果里


def test_files_are_never_overwritten(tmp_path):
    pytest.importorskip("PIL", reason="这一条走的是本地卡片落盘")
    out1 = images.image_generate(_Ctx(workspace=tmp_path, config=_cfg()),
                                 text="第一次", name="card")
    out2 = images.image_generate(_Ctx(workspace=tmp_path, config=_cfg()),
                                 text="第二次", name="card")
    assert "card.png" in out1 and "card-2.png" in out2
    assert len(list((tmp_path / "images").glob("*.png"))) == 2


def test_unknown_backend_lists_the_real_options(tmp_path):
    out = images.image_generate(_Ctx(workspace=tmp_path, config=_cfg()),
                                text="x", backend="magic")
    assert "auto" in out and "card" in out


def test_bad_size_is_rejected_before_any_file_is_written(tmp_path):
    pytest.importorskip("PIL", reason="尺寸校验在 Pillow 分支里")
    out = images.image_generate(_Ctx(workspace=tmp_path, config=_cfg()),
                                text="x", backend="card", size="99999x3")
    assert "没有出图" in out
    assert not (tmp_path / "images").exists()
