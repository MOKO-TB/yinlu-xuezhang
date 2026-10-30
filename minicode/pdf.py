"""PDF 页级操作:合并、抽页、旋转、提取文字层。

为什么值得有 —— pypdf 已是正式依赖(v0.9.172 起 ocr_image 抽页内嵌图就在用),
但全仓此前没有一处 `PdfWriter`:教务要求「多份材料合并成一份上传」、从三百页
课件里抽出要交的那几页、扫描件方向倒了要转正 —— 这些以前只能让模型现写
py_script,而 py_script 里一次 import 拼错就是用户机器上的一次真报错。这里收成
一只工具,把页码、覆盖、加密这些坑在工具层如实说清。

四条边界(每条都有测试钉着):

1. **加密 PDF 一律不做** —— 不猜密码:猜错的代价是返回一份谁都没见过的坏文件。
2. **页码一律 1 起、越界点名总页数** —— 绝不"就近取整页",页选错了用户拿到
   的是错的材料,还以为是对的。
3. **输出默认不覆盖已有文件**,要覆盖必须显式 `overwrite=true` —— 这类操作
   重跑一遍就该得到同一份结果,静默覆盖会把上一次的结果吃掉。
4. **提取文字只提取文字层**:一份都提不出字就明说「这份没有文字层,是扫描图,
   走 ocr_image」—— 绝不返回空字符串装成功。

工作区围栏不在这里重复拦:读源、写目标与其他写类工具同一条路,越界由权限门
(outside_path)决定要不要问人;handler 只管参数校验与诚实回话。
"""
from __future__ import annotations

import re
from pathlib import Path

ROTATE_DEGREES = (90, 180, 270)
TEXT_LIMIT = 6000
_PAGES_SPEC = re.compile(r"^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")


class PdfError(ValueError):
    """参数或文件不成立。message 里要点名是哪一条,不能只说「格式错误」。"""


# ---------------------------------------------------------------- 基础件

def _workspace(ctx) -> Path:
    return Path(getattr(ctx, "workspace", ".") or ".").expanduser().resolve()


def _src(ctx, path: str) -> Path:
    """源文件:相对工作区解析;不存在/不是 .pdf 当场说清。"""
    ws = _workspace(ctx)
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        p = (ws / p).resolve()
    if not p.is_file():
        raise PdfError(f"找不到这份 PDF:{p}(工作区是 {ws};给个存在的 .pdf 路径)")
    if p.suffix.lower() != ".pdf":
        raise PdfError(f"这不是 PDF 文件:{p.name}(只认 .pdf;图片取字请用 ocr_image)")
    return p


def _reader(p: Path):
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(p))
    except Exception as e:
        raise PdfError(f"这份 PDF 读不开:{type(e).__name__}: {e}") from e
    if reader.is_encrypted:
        raise PdfError(
            f"这份 PDF 加了密:{p.name}。学长不做解密(也不猜密码);"
            "用你自己的密码在 PDF 阅读器里另存一份不加密的,再来操作。")
    return reader


def parse_pages(spec: str, total: int) -> list[int]:
    """「1,3,5-8」→ 去重升序的 0-based 页下标。越界/空段/反段都要点名。"""
    s = str(spec or "").strip()
    if not s:
        raise PdfError("页码没给:要像「1,3,5-8」这样写(第 1 页从 1 数起)")
    if not _PAGES_SPEC.match(s):
        raise PdfError(f"页码写法看不懂:「{s}」。只认「1」「1,3」「2-5」和它们的逗号组合")
    out: set[int] = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
            if a > b:
                raise PdfError(f"页码段写反了:「{part}」(前小后大,如 2-5)")
            lo, hi = a, b
        else:
            lo = hi = int(part)
        if lo < 1 or hi > total:
            raise PdfError(f"页码越界:「{part}」不在 1~{total} 里(这份共 {total} 页)")
        out.update(range(lo - 1, hi))
    return sorted(out)


def _dest(ctx, out: str, default_name: str) -> Path:
    ws = _workspace(ctx)
    name = str(out or "").strip() or default_name
    p = Path(name).expanduser()
    if not p.is_absolute():
        p = (ws / p).resolve()
    if p.suffix.lower() != ".pdf":
        p = p.with_suffix(".pdf")
    return p


def _ensure_writable(dest: Path, overwrite: bool) -> None:
    if dest.exists() and not overwrite:
        raise PdfError(
            f"输出文件已经存在:{dest.name}。不覆盖 —— 换一个 out 文件名,"
            "或明确 overwrite=true 才覆盖。")


def _write(writer, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        writer.write(f)
    return f"{dest.name}({dest.stat().st_size / 1024:.1f} KB)"


def _default_name(src: Path, tag: str) -> str:
    return f"{src.stem}_{tag}.pdf"


# ---------------------------------------------------------------- 四个动作

def merge(ctx, sources, out: str = "", overwrite: bool = False) -> str:
    """把多份 PDF 按给出顺序合成一份。"""
    from pypdf import PdfWriter
    paths = [str(s) for s in (sources if isinstance(sources, list) else [])]
    paths = [s for s in paths if str(s).strip()]
    if len(paths) < 2:
        raise PdfError("合并至少要给两份 PDF(sources 列表);一份不需要合并")
    readers = [_reader(_src(ctx, s)) for s in paths]
    writer = PdfWriter()
    total = 0
    for r in readers:
        for page in r.pages:
            writer.add_page(page)
            total += 1
    dest = _dest(ctx, out, "merged.pdf")
    _ensure_writable(dest, overwrite)
    size = _write(writer, dest)
    names = "、".join(Path(s).name for s in paths)
    return (f"PDF 合并完成｜{len(paths)} 份({names})｜共 {total} 页｜输出:{size}。"
            f"顺序就是 sources 里的给出顺序;页码如有需要稍后可用 extract 抽查核对。")


def extract(ctx, src_path: str, pages: str, out: str = "",
            overwrite: bool = False) -> str:
    """从一份 PDF 抽出指定页,按原顺序存成一份新的。"""
    from pypdf import PdfWriter
    src = _src(ctx, src_path)
    reader = _reader(src)
    total = len(reader.pages)
    idx = parse_pages(pages, total)
    writer = PdfWriter()
    for i in idx:
        writer.add_page(reader.pages[i])
    tag = "p" + ",".join(str(i + 1) for i in idx) if len(idx) <= 6 else f"p{idx[0]+1}-{idx[-1]+1}"
    dest = _dest(ctx, out, _default_name(src, tag))
    _ensure_writable(dest, overwrite)
    size = _write(writer, dest)
    return (f"PDF 抽页完成｜{src.name}(共 {total} 页)抽出第 "
            f"{','.join(str(i + 1) for i in idx)} 页｜共 {len(idx)} 页｜输出:{size}。")


def rotate(ctx, src_path: str, pages: str, degrees: int, out: str = "",
           overwrite: bool = False) -> str:
    """把指定页顺时针旋转 90/180/270 度(在原朝向基础上叠加,写进页面属性)。"""
    from pypdf import PdfWriter
    if int(degrees) not in ROTATE_DEGREES:
        raise PdfError(f"旋转角度只认 {ROTATE_DEGREES},拿到的是 {degrees}")
    src = _src(ctx, src_path)
    reader = _reader(src)
    total = len(reader.pages)
    idx = set(parse_pages(pages, total))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i in idx:
            page.rotate(int(degrees))
        writer.add_page(page)
    tag = "rot" + str(degrees)
    dest = _dest(ctx, out, _default_name(src, tag))
    _ensure_writable(dest, overwrite)
    size = _write(writer, dest)
    rotated = ",".join(str(i + 1) for i in sorted(idx))
    return (f"PDF 旋转完成｜{src.name} 第 {rotated} 页顺时针转 {degrees} 度"
            f"(其余 {total - len(idx)} 页原样)|输出:{size}。转的是页面朝向标记,"
            "阅读器与打印都认;如果要的是永久压平的图像旋转,那份要重新扫描或打印成 PDF。")


def extract_text(ctx, src_path: str, pages: str = "",
                 max_chars: int = TEXT_LIMIT) -> str:
    """提取**文字层**的文本,按页分段;提不出字就指路 ocr_image,不装成功。"""
    src = _src(ctx, src_path)
    reader = _reader(src)
    total = len(reader.pages)
    if str(pages or "").strip().lower() in ("", "all"):
        idx = list(range(total))
    else:
        idx = parse_pages(pages, total)
    parts, grand = [], 0
    for i in idx:
        try:
            t = reader.pages[i].extract_text() or ""
        except Exception as e:
            t = ""
            parts.append(f"--- 第 {i + 1} 页 ---\n(这一页的文字提取失败:{type(e).__name__})")
            continue
        t = t.strip()
        grand += len(t)
        parts.append(f"--- 第 {i + 1} 页 ---\n{t}" if t else
                     f"--- 第 {i + 1} 页 ---\n(这一页没有文字层)")
    if grand == 0:
        return (f"一个字都没提出来:{src.name} 的这 {len(idx)} 页没有文字层 —— "
                "它多半是扫描图/图片版 PDF。文字识别走 ocr_image(那只直接吃 .pdf,"
                "逐页抽内嵌图再 OCR);这边不把空结果装成成功。")
    body = "\n".join(parts)
    clipped = len(body) > max_chars
    head = (f"PDF 文字层提取完成｜{src.name}｜共 {total} 页,取了第 "
            f"{','.join(str(i + 1) for i in idx) if len(idx) <= 12 else f'{idx[0]+1}-{idx[-1]+1}'} 页"
            f"｜正文 {grand} 字")
    out_lines = [head, body[:max_chars]]
    if clipped:
        out_lines.append(f"……(已截到 {max_chars} 字;要全文把 max_chars 调大)")
    return "\n".join(out_lines)


ACTIONS = ("merge", "extract", "rotate", "text")


def pdf_tool(ctx, action: str, sources=None, src: str = "", pages: str = "",
             degrees: int = 0, out: str = "", overwrite: bool = False,
             max_chars: int = TEXT_LIMIT) -> str:
    """工具分发口:action 派给对应动作,失败一律返回人话而不是抛栈。"""
    act = str(action or "").strip().lower()
    try:
        if act == "merge":
            return merge(ctx, sources, out=out, overwrite=overwrite)
        if act == "extract":
            return extract(ctx, src, pages, out=out, overwrite=overwrite)
        if act == "rotate":
            return rotate(ctx, src, pages, degrees, out=out, overwrite=overwrite)
        if act == "text":
            return extract_text(ctx, src, pages=pages, max_chars=max_chars)
        raise PdfError(f"action 只认 {ACTIONS},拿到的是「{action}」")
    except PdfError as e:
        return f"没做成:{e}"
    except Exception as e:  # pypdf 深处的意外也要落到人话
        return f"没做成:{type(e).__name__}: {e}"
