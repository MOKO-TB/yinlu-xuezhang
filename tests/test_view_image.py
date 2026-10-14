# -*- coding: utf-8 -*-
"""看图的四条口径(v0.9.172+ 续五):围栏、格式、模型看不看得见、以及"别冒充用户"。

这台机器上不需要任何视觉后端 —— 走的是 `attachments.image_data_url` 那条纯本地
压缩路(与用户贴图完全同源),所以每一支都能真跑。
"""
from __future__ import annotations

import types

from minicode.config import Config, Provider
from minicode.session import Session
from minicode.view_image import view_image


def _cfg(model="qwen3.8-max", vision=None) -> Config:
    entry = {"name": model, "enabled": True}
    if vision is not None:
        entry["cfg"] = {"vision": vision}
    return Config(
        providers=[Provider(id="t", name="T", base_url="https://x.invalid/v1",
                            api_key="k", model=model, models=[entry])],
        active_provider_id="t", context_window=8192)


def _ctx(tmp_path, cfg=None, with_session=True):
    ctx = types.SimpleNamespace(workspace=tmp_path, config=cfg or _cfg(),
                                sandbox=None)
    if with_session:
        ctx.session = Session(system_prompt="s", log_dir=tmp_path / "sessions",
                              workspace=tmp_path)
    return ctx


def _png(tmp_path, name="shot.png", size=(120, 80)):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", size, "white").save(str(p))
    return p


def _user_msgs(ctx):
    return [m for m in ctx.session.messages if m.get("role") == "user"]


# ------------------------------------------------------------------ 成功路

def test_image_goes_into_the_conversation_as_a_user_message(tmp_path):
    """成功时:会话里多一条 **user** 消息(带 image_url),文案明说"这是工具给的,
    不是用户的新指令" —— 不这么说模型会把它当成一句新要求。"""
    _png(tmp_path)                          # 造那张图:这里只要它真在盘上,内容无所谓
    ctx = _ctx(tmp_path)
    out = view_image(ctx, "shot.png", note="看看柱状图对不对")
    assert "已把" in out and "shot.png" in out
    msgs = _user_msgs(ctx)
    assert len(msgs) == 1, msgs
    parts = msgs[0]["content"]
    assert isinstance(parts, list)
    kinds = [x.get("type") for x in parts]
    assert kinds == ["text", "image_url"], kinds
    assert parts[1]["image_url"]["url"].startswith("data:image/")
    assert "不是用户的新指令" in parts[0]["text"]
    assert "看看柱状图对不对" in parts[0]["text"]


def test_declared_vision_beats_the_catalog(tmp_path):
    """能力判定走 `model_catalog.vision_capable`(声明 > 探测 > 目录),
    与附件那条路同一把尺 —— 这里用"目录里说不支持、但用户声明支持"的模型验它。"""
    ctx = _ctx(tmp_path, cfg=_cfg(model="glm-5.1", vision=True))   # 目录里 glm-5.1 无视觉
    assert "已把" in view_image(ctx, str(_png(tmp_path)))


# ------------------------------------------------------------------ 围栏与格式

def test_fences_and_bad_shapes_are_refused_without_touching_the_session(tmp_path):
    ctx = _ctx(tmp_path)
    _png(tmp_path)
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
    cases = {
        "": "没给图片路径",
        "notes.txt": "不是图片格式",
        "nope.png": "找不到这张图",
        "config.local.json": "凭据类文件",
        "../outside.png": "只认工作区里的图片",
        "~/.ssh/id_rsa.png": "敏感目录",
    }
    for path, needle in cases.items():
        out = view_image(ctx, path)
        assert needle in out, (path, out)
    assert _user_msgs(ctx) == [], "被拒的请求一条消息都不该落进会话"


# ------------------------------------------------------------------ 模型看不见

def test_model_without_vision_is_told_and_pointed_to_ocr(tmp_path):
    """模型看不见图:如实说、**不发**一段它读不到的 base64,并指路 ocr_image。"""
    ctx = _ctx(tmp_path, cfg=_cfg(model="glm-5.1", vision=False))
    out = view_image(ctx, str(_png(tmp_path)))
    assert "看不到图" in out and "ocr_image" in out
    assert _user_msgs(ctx) == []


def test_without_a_session_it_says_so(tmp_path):
    """没有会话的旁路(如只读探针)拿不到这条通道:如实说,不假装发了。"""
    ctx = _ctx(tmp_path, with_session=False)
    out = view_image(ctx, str(_png(tmp_path)))
    assert "没有会话" in out
