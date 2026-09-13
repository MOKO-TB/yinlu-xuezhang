"""附件处理:把用户上传的文件/图片安全落进工作区,并构造模型能吃的消息体。

设计要点(对齐星瑶 Y-Code 的附件策略,按本项目的体量收敛):
- **一律落盘**:附件先复制到工作区 `uploads/`(重名自动加序号),消息里只放
  相对路径 —— 这样会话留档、恢复、以及模型用工具读文件都自然成立;
- **图片压缩**:长边超过 1536 就等比缩小,JPEG/WebP 逐级降质量到 1MB 以内,
  带透明通道的 PNG 保持 PNG,GIF 保留原动画;
- **视觉模型**:`config.json` 里 `vision: true` 时把图片按实际编码做成
  base64 data URL 随消息发出;否则只给路径引用,并在提示里说明当前模型看不了图;
- **单文件上限 20MiB、解码后不超过 4000 万像素**(防解压炸弹)。
"""
from __future__ import annotations

import base64
import io
import shutil
from dataclasses import dataclass
from pathlib import Path

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
IMAGE_MAX_EDGE = 1536
IMAGE_QUALITY = 90
IMAGE_MAX_BYTES = 1024 * 1024
QUALITY_STEPS = (90, 85, 75, 60)
UPLOAD_DIR = "uploads"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff"}
_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".ico": "image/x-icon", ".tif": "image/tiff", ".tiff": "image/tiff",
}


@dataclass
class Attachment:
    name: str          # 原始文件名
    path: str          # 相对工作区的路径(消息里引用的就是它)
    size: int
    kind: str          # "image" | "file"
    mime: str = ""
    local: str = ""    # 内部用绝对路径(读图压缩时用;不发给前端/模型)

    def to_dict(self) -> dict:
        return {"name": self.name, "path": self.path, "size": self.size,
                "kind": self.kind, "mime": self.mime}

    def local_path(self) -> Path:
        return Path(self.local or self.path)


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def _mime_for(path: Path) -> str:
    return _MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


def mime_for(path: Path) -> str:
    """按扩展名推断 MIME(公开给桥接层复用)。"""
    return _mime_for(path)


def unique_target(directory: Path, name: str) -> Path:
    """同名文件自动加 -1/-2 后缀,避免互相覆盖。"""
    safe = Path(name).name or "attachment"
    target = directory / safe
    if not target.exists():
        return target
    stem, suffix = Path(safe).stem, Path(safe).suffix
    n = 1
    while True:
        cand = directory / f"{stem}-{n}{suffix}"
        if not cand.exists():
            return cand
        n += 1


def save_attachment(workspace: Path, src: Path) -> Attachment:
    """把一个外部文件复制进工作区 uploads/。"""
    src = Path(src)
    if not src.is_file():
        raise ValueError(f"不是文件: {src}")
    size = src.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"{src.name} 超过单附件上限 {human_size(MAX_ATTACHMENT_BYTES)}"
            f"(实际 {human_size(size)})")
    directory = Path(workspace) / UPLOAD_DIR
    directory.mkdir(parents=True, exist_ok=True)
    target = unique_target(directory, src.name)
    shutil.copy2(src, target)
    rel = target.relative_to(Path(workspace)).as_posix()
    return Attachment(name=src.name, path=rel, size=size,
                      kind="image" if is_image(src) else "file",
                      mime=_mime_for(src), local=str(target.resolve()))


def save_attachments(workspace: Path, paths: list[str]) -> tuple[list[Attachment], list[str]]:
    """批量落盘;返回 (成功的附件, 失败说明)。单个失败不影响其它文件。"""
    ok: list[Attachment] = []
    errors: list[str] = []
    for raw in paths or []:
        if not raw:
            continue
        try:
            ok.append(save_attachment(workspace, Path(str(raw))))
        except (OSError, ValueError) as e:
            errors.append(str(e))
    return ok, errors


# --------------------------------------------------------------------- 图片

def compress_image(path: Path, max_edge: int = IMAGE_MAX_EDGE,
                   quality: int = IMAGE_QUALITY,
                   max_bytes: int = IMAGE_MAX_BYTES) -> tuple[bytes, str]:
    """按需压缩图片,返回 (数据, mime)。无法处理时原样返回,绝不抛异常。"""
    raw = Path(path).read_bytes()
    suffix = Path(path).suffix.lower()
    if suffix == ".gif":
        return raw, "image/gif"          # 动图不重编码
    try:
        from PIL import Image
    except Exception:
        return raw, _mime_for(Path(path))
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.width * im.height > MAX_IMAGE_PIXELS:
                return raw, _mime_for(Path(path))
            has_alpha = im.mode in ("RGBA", "LA") or (
                im.mode == "P" and "transparency" in im.info)
            work = im.convert("RGBA" if has_alpha else "RGB")
            edge = max(work.size)
            if edge > max_edge:
                scale = max_edge / edge
                work = work.resize((max(1, round(work.width * scale)),
                                    max(1, round(work.height * scale))),
                                   Image.LANCZOS)
            if has_alpha:
                buf = io.BytesIO()
                work.save(buf, format="PNG", optimize=True)
                data = buf.getvalue()
                # 透明图保 PNG:超限时只缩边长,不改格式
                while len(data) > max_bytes and max(work.size) > 512:
                    work = work.resize((max(1, work.width * 4 // 5),
                                        max(1, work.height * 4 // 5)),
                                       Image.LANCZOS)
                    buf = io.BytesIO()
                    work.save(buf, format="PNG", optimize=True)
                    data = buf.getvalue()
                return data, "image/png"

            data = b""
            for q in (quality, *QUALITY_STEPS):
                buf = io.BytesIO()
                work.save(buf, format="JPEG", quality=q, optimize=True)
                data = buf.getvalue()
                if len(data) <= max_bytes:
                    break
            while len(data) > max_bytes and max(work.size) > 512:
                work = work.resize((max(1, work.width * 4 // 5),
                                    max(1, work.height * 4 // 5)),
                                   Image.LANCZOS)
                buf = io.BytesIO()
                work.save(buf, format="JPEG", quality=QUALITY_STEPS[-1], optimize=True)
                data = buf.getvalue()
            return data, "image/jpeg"
    except Exception:
        return raw, _mime_for(Path(path))


def data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def image_data_url(path: Path) -> str | None:
    """给视觉模型用的 data URL;压缩或读取失败返回 None。"""
    try:
        data, mime = compress_image(path)
    except OSError:
        return None
    return data_url(data, mime)


def preview_data_url(path: Path, max_edge: int = 320) -> str | None:
    """前端缩略图:压得更小,避免一次性传大图进 DOM。"""
    try:
        data, mime = compress_image(path, max_edge=max_edge, quality=70,
                                    max_bytes=200 * 1024)
    except OSError:
        return None
    return data_url(data, mime)


# --------------------------------------------------------------------- 消息体

def attachment_note(atts: list[Attachment], vision: bool) -> str:
    """注入到用户消息里的附件清单(模型据此知道有哪些文件可用)。"""
    if not atts:
        return ""
    lines = ["[附件] 用户上传了以下文件,已保存到工作区:"]
    for a in atts:
        if a.kind == "image":
            tail = "(图片已随消息提供给模型查看)" if vision \
                else "(图片:当前模型不支持看图,仅作文件引用)"
        else:
            tail = ""
        lines.append(f"- {a.path}({human_size(a.size)}){tail}")
    lines.append("需要文件内容时,请用 read_file / bash 等工具处理上述路径;"
                 "不要臆测文件内容。")
    return "\n".join(lines)


def display_text(content) -> str:
    """会话留档里的 content → 前端展示用文本(多模态取 text 段并去掉附件清单)。"""
    if isinstance(content, list):
        joined = "".join(str(p.get("text") or "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
        return strip_attachment_note(joined)
    return strip_attachment_note(str(content or ""))


def strip_attachment_note(text: str) -> str:
    """去掉注入到用户消息里的 [附件] 清单,只留下用户真正输入的内容。"""
    marker = "[附件]"
    if not text.startswith(marker):
        return text
    parts = text.split("\n\n", 1)
    return parts[1] if len(parts) == 2 else ""


def build_content(text: str, atts: list[Attachment] | None,
                  vision: bool) -> str | list[dict]:
    """构造发给模型的消息体:无附件/无视觉 → 纯文本;有图且支持视觉 → content parts。"""
    atts = atts or []
    parts: list[dict] = []
    body = text
    if atts:
        note = attachment_note(atts, vision)
        body = f"{note}\n\n{text}" if text.strip() else note

    if vision and any(a.kind == "image" for a in atts):
        parts.append({"type": "text", "text": body})
        for a in atts:
            if a.kind != "image":
                continue
            url = image_data_url(a.local_path())
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
        if len(parts) == 1:      # 图片全部读取失败:退回纯文本
            return body
        return parts
    return body
