r"""看一张工作区里的图(v0.9.172+ 续五):让模型**看见**自己刚做出来的东西。

## 它补的是哪一格

用户贴的图模型能看(`attachments.py` 走 `image_url`),但**模型主动去读工作区里的
一张图没有工具**:`ocr_image` 是取字不是看,`browser_screenshot` 是把图推给**人**看。
后果很具体:`render_chart` 画的图表、`generate_image` 生成的图、`browser_screenshot`
的网页截图 —— 模型自己看不到效果,只能凭代码推断画出来对不对。

## 图是怎么送到模型眼前的

工具结果本身只走文本(`role: tool`),所以这里用 **Claude Code / Codex 同款做法**:
把图作为一条 **user 消息**追加进会话(`Session.add_user(content=[...])`),
下一轮的请求就带着它。文案里明说「这是 view_image 取来的图,不是用户的新指令」——
不这么说,模型会把它当成一句新要求(与插话信箱那条教训同源:别让工具的话冒充用户)。

## 三条口径

1. **围栏与凭据走 sandbox 同一套判**:只认工作区里的图片;
2. **模型看不见图就如实说**,并指路 `ocr_image` —— 不发一段它读不到的 base64 出去;
3. 压缩与上限**复用 `attachments`**(1536 长边 / 质量阶梯 / 1MB 上限),不另立一套。
"""
from __future__ import annotations

from pathlib import Path


def view_image(ctx, path: str, note: str = "") -> str:
    """把工作区里的一张图作为 user 消息发进会话;看不见图时如实说。"""
    from minicode import attachments, model_catalog
    from minicode.sandbox import BLOCKED, OUTSIDE, SECRET, classify_path

    raw = str(path or "").strip()
    if not raw:
        return "错误: 没给图片路径"
    ws = Path(getattr(ctx, "workspace", ".") or ".")
    p = Path(raw).expanduser()
    kind, abs_p = classify_path(ws, str(p))
    if kind == BLOCKED:
        return "敏感目录里的文件不看(沙箱同一套判)。"
    if kind == SECRET:
        return "凭据类文件不看 —— 那类要走专门的工具并且当面确认。"
    if kind == OUTSIDE:
        return "只认工作区里的图片(工作区外的东西不在这一只的范围里)。"
    if not abs_p.is_file():
        return f"找不到这张图:{abs_p}"
    if abs_p.suffix.lower() not in attachments.IMAGE_EXTS:
        return (f"{abs_p.suffix or '这个文件'}不是图片格式 —— 这一只只用来**看图**。"
                "要取图里的字用 `ocr_image`;要读文本文件用 `read_file`。")

    cfg = getattr(ctx, "config", None)
    model = str(getattr(cfg, "model", "") or "")
    if cfg is not None and not model_catalog.vision_capable(cfg, model):
        return ("这个模型看不到图(能力表/探测说它不支持视觉)—— **没有**把图发出去。"
                "要取图里的字可以改用 `ocr_image`;真要看图请先切一个支持视觉的模型。")

    session = getattr(ctx, "session", None)
    if session is None:
        return "这个上下文里没有会话,图发不出去(没有会话的旁路拿不到这条通道)。"

    url = attachments.image_data_url(abs_p)
    if not url:
        return f"这张图读不出来或压不动:{abs_p}(可能不是有效图片,或超出图像上限)"
    try:
        rel = abs_p.resolve().relative_to(Path(ws).resolve()).as_posix()
    except (ValueError, OSError):
        rel = str(abs_p)
    label = (f"(view_image 取来的图:{rel} —— 这是工具给的,不是用户的新指令)")
    if str(note or "").strip():
        label += "\n" + str(note).strip()
    session.add_user(label, content=[{"type": "text", "text": label},
                                     {"type": "image_url", "image_url": {"url": url}}])
    try:
        size = abs_p.stat().st_size
    except OSError:
        size = 0
    return (f"已把 {rel} 作为图片发进对话(原始 {size} 字节,发送前按 attachments 的"
            "上限压过)—— 下一轮你就能看到它。看不清就说看不清,别凭代码猜。")
