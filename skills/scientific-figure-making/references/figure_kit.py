"""论文级配图的零依赖内核:标准库手写 SVG,要 PNG 时走 Pillow(可选)。

为什么不用 matplotlib:本项目 `packaging/gcode.spec` 的 excludes 明确排掉了
matplotlib 与 pandas(体积与启动时间),`minicode/charts.py` 也是因此手写 SVG。
这份内核沿用同一条根 —— **import 只用标准库**;PNG 那一步才 import PIL(项目本来
就依赖 pillow),没装就只落 SVG,并把原因照实说出来。

设计口径来自 figures4papers(ChenLiu-1996)的 scientific-figure-making 技能:
语义化调色板(蓝=主方法/绿=增益/红=对照/灰=基线)、去顶右边框、图例不压数据、
矢量优先、300 dpi 级别的字号与线宽。这里把它落成"能在这个仓库里真跑"的版本。

用法(见 skills/scientific-figure-making/SKILL.md):

    fig = Figure(width=1200, height=700, title="...", source="来源: ...")
    grouped_bars(fig.canvas, fig.box(0.07, 0.16, 0.55, 0.72),
                 ["A", "B"], [[1, 2], [2, 1]], ["X", "Y"], ylabel="分数")
    fig.save("figures/demo")        # 落 demo.svg,能落 PNG 就再落一张
"""
from __future__ import annotations

import math
import re
from pathlib import Path

# ---------------------------------------------------------------- 调色板与版式

PALETTE = {
    "blue_main": "#0F4D92",
    "blue_secondary": "#3775BA",
    "green_1": "#DDF3DE",
    "green_2": "#AADCA9",
    "green_3": "#8BCF8B",
    "red_1": "#F6CFCB",
    "red_2": "#E9A6A1",
    "red_strong": "#B64342",
    "neutral": "#CFCECE",
    "highlight": "#FFD700",
    "teal": "#42949E",
    "violet": "#9A4D8E",
    "ink": "#272727",
    "muted": "#767676",
    "grid": "#E4E4E4",
    "white": "#FFFFFF",
}

# 不传 colors 时的默认顺序(与 api.md 的 DEFAULT_COLORS 同序)。
DEFAULT_COLORS = [PALETTE["blue_main"], PALETTE["green_3"], PALETTE["red_strong"],
                  PALETTE["teal"], PALETTE["violet"], PALETTE["neutral"]]

# 支持度编码:给热力图用。值越大越"支持"。
LEVELS = {3: "完全支持", 2: "部分/受限", 1: "未核实", 0: "明确没有"}
LEVEL_COLORS = {3: PALETTE["green_3"], 2: PALETTE["green_1"],
                1: "#F1F1F1", 0: PALETTE["red_1"]}

FONT_STACK = ("Microsoft YaHei, PingFang SC, Noto Sans CJK SC, "
              "Source Han Sans SC, Arial, Helvetica, sans-serif")


def _esc(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _char_w(ch: str, size: float) -> float:
    """粗略字宽:中日韩按 1em、其余按 0.55em。够排版用,不做字体度量。"""
    o = ord(ch)
    wide = (0x2E80 <= o <= 0x9FFF) or (0xAC00 <= o <= 0xD7A3) or (0xF900 <= o <= 0xFAFF)
    return size * (1.0 if wide else 0.55)


def text_width(text: str, size: float) -> float:
    return sum(_char_w(ch, size) for ch in str(text))


def _fit(text: str, room: float, size: float) -> str:
    """放不下就截断加省略号 —— 宁可少几个字,也不让文字压到别的元素上。"""
    text = str(text)
    if text_width(text, size) <= room or not text:
        return text
    out = ""
    for ch in text:
        if text_width(out + ch + "…", size) > room:
            break
        out += ch
    return out + "…"


# ---------------------------------------------------------------------- 画布

class Canvas:
    """一份图:记录绘制原语,再由 save_svg / save_png 落盘。

    坐标为像素(左上角原点),与 SVG 一致;PIL 也是左上角原点,两个后端同一套坐标。
    """

    def __init__(self, width: int = 1200, height: int = 700, bg: str = PALETTE["white"]):
        self.w, self.h, self.bg = int(width), int(height), bg
        self.ops: list[tuple] = []

    # -- 原语 ---------------------------------------------------------------
    def rect(self, x, y, w, h, fill=None, stroke=None, sw=1.0, rx=0):
        self.ops.append(("rect", x, y, w, h, fill, stroke, sw, rx))

    def line(self, x1, y1, x2, y2, stroke=PALETTE["ink"], sw=1.0, dash=None):
        self.ops.append(("line", x1, y1, x2, y2, stroke, sw, dash))

    def polyline(self, pts, stroke=PALETTE["blue_main"], sw=2.0):
        self.ops.append(("poly", list(pts), stroke, sw))

    def circle(self, cx, cy, r, fill=None, stroke=None, sw=1.0):
        self.ops.append(("circle", cx, cy, r, fill, stroke, sw))

    def text(self, x, y, s, size=16, fill=PALETTE["ink"], anchor="start",
             weight="normal", rotate=0):
        self.ops.append(("text", x, y, str(s), size, fill, anchor, weight, rotate))

    # -- 落盘 ---------------------------------------------------------------
    def save_svg(self, path) -> Path:
        path = Path(path)
        if path.suffix.lower() != ".svg":
            path = path.with_suffix(".svg")
        path.parent.mkdir(parents=True, exist_ok=True)
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" '
               f'height="{self.h}" viewBox="0 0 {self.w} {self.h}" '
               f'font-family="{FONT_STACK}">',
               f'<rect width="{self.w}" height="{self.h}" fill="{self.bg}"/>']
        for op in self.ops:
            out.append(_svg_op(op))
        out.append("</svg>")
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return path

    def save_png(self, path, scale: float = 2.0) -> Path:
        """用 Pillow 画同一批原语。没装 Pillow / 找不到中文字体时抛 RuntimeError。"""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:                      # 照实说,不假装落了图
            raise RuntimeError(f"没装 Pillow,只能落 SVG:{exc}") from exc
        font_path = _cjk_font()
        if font_path is None:
            raise RuntimeError("找不到中文字体(msyh.ttc/simhei.ttf 都没有),只能落 SVG")
        path = Path(path)
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (int(self.w * scale), int(self.h * scale)), self.bg)
        dr = ImageDraw.Draw(img)
        cache: dict[int, object] = {}

        def font(size):
            key = int(size * scale)
            if key not in cache:
                cache[key] = ImageFont.truetype(str(font_path), key)
            return cache[key]

        for op in self.ops:
            kind = op[0]
            if kind == "rect":
                _, x, y, w, h, fill, stroke, sw, rx = op
                box = [x * scale, y * scale, (x + w) * scale, (y + h) * scale]
                dr.rectangle(box, fill=fill, outline=stroke,
                             width=max(1, round(sw * scale)))
            elif kind == "line":
                _, x1, y1, x2, y2, stroke, sw, dash = op
                if dash:
                    for seg in _dash_segments(x1, y1, x2, y2, dash):
                        dr.line([p * scale for p in seg], fill=stroke,
                                width=max(1, round(sw * scale)))
                else:
                    dr.line([x1 * scale, y1 * scale, x2 * scale, y2 * scale],
                            fill=stroke, width=max(1, round(sw * scale)))
            elif kind == "poly":
                _, pts, stroke, sw = op
                dr.line([p * scale for pt in pts for p in pt], fill=stroke,
                        width=max(1, round(sw * scale)), joint="curve")
            elif kind == "circle":
                _, cx, cy, r, fill, stroke, sw = op
                dr.ellipse([(cx - r) * scale, (cy - r) * scale,
                            (cx + r) * scale, (cy + r) * scale],
                           fill=fill, outline=stroke, width=max(1, round(sw * scale)))
            elif kind == "text":
                _, x, y, s, size, fill, anchor, weight, rotate = op
                if rotate:
                    continue          # 竖排文字 PNG 不做:SVG 里仍是准的
                a = {"start": "la", "middle": "ma", "end": "ra"}.get(anchor, "la")
                dr.text((x * scale, y * scale), s, font=font(size), fill=fill, anchor=a)
        img.save(path)
        return path


def _dash_segments(x1, y1, x2, y2, dash):
    segs, total = [], math.hypot(x2 - x1, y2 - y1)
    if total <= 0:
        return segs
    on, off = (dash, dash) if isinstance(dash, (int, float)) else dash
    pos, draw = 0.0, True
    while pos < total:
        step = min(on if draw else off, total - pos)
        if draw:
            t0, t1 = pos / total, (pos + step) / total
            segs.append((x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0,
                         x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1))
        pos += step
        draw = not draw
    return segs


def _svg_op(op) -> str:
    kind = op[0]
    if kind == "rect":
        _, x, y, w, h, fill, stroke, sw, rx = op
        bits = [f'x="{x:.2f}" y="{y:.2f}" width="{max(0, w):.2f}" height="{max(0, h):.2f}"']
        bits.append(f'fill="{fill}"' if fill else 'fill="none"')
        if stroke:
            bits += [f'stroke="{stroke}"', f'stroke-width="{sw}"']
        if rx:
            bits.append(f'rx="{rx}"')
        return "<rect " + " ".join(bits) + "/>"
    if kind == "line":
        _, x1, y1, x2, y2, stroke, sw, dash = op
        d = f' stroke-dasharray="{dash}"' if dash else ""
        return (f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                f'stroke="{stroke}" stroke-width="{sw}"{d}/>')
    if kind == "poly":
        _, pts, stroke, sw = op
        p = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
        return (f'<polyline points="{p}" fill="none" stroke="{stroke}" '
                f'stroke-width="{sw}" stroke-linejoin="round"/>')
    if kind == "circle":
        _, cx, cy, r, fill, stroke, sw = op
        bits = [f'cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}"']
        bits.append(f'fill="{fill}"' if fill else 'fill="none"')
        if stroke:
            bits += [f'stroke="{stroke}"', f'stroke-width="{sw}"']
        return "<circle " + " ".join(bits) + "/>"
    if kind == "text":
        _, x, y, s, size, fill, anchor, weight, rotate = op
        tr = f' transform="rotate({rotate} {x:.2f} {y:.2f})"' if rotate else ""
        return (f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" fill="{fill}" '
                f'text-anchor="{anchor}" font-weight="{weight}"{tr}>{_esc(s)}</text>')
    return ""


def _cjk_font():
    import os
    for cand in (os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", n)
                 for n in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc")):
        if Path(cand).is_file():
            return Path(cand)
    return None


# ---------------------------------------------------------------------- 图

class Figure:
    """画布 + 标题/副标题/来源脚注 + 归一化取框。"""

    def __init__(self, width=1200, height=700, title="", subtitle="", source="",
                 bg=PALETTE["white"]):
        self.canvas = Canvas(width, height, bg)
        self.w, self.h = self.canvas.w, self.canvas.h
        self.source = source
        y = 34
        if title:
            self.canvas.text(28, y + 8, title, size=24, weight="bold")
            y += 40
        if subtitle:
            self.canvas.text(28, y + 6, subtitle, size=14, fill=PALETTE["muted"])

    def box(self, x, y, w, h):
        """归一化(0~1)→ 像素框,给下面几个绘图函数用。"""
        return (x * self.w, y * self.h, w * self.w, h * self.h)

    def save(self, stem, scale=2.0) -> list[Path]:
        """落 `<stem>.svg`,能落 PNG 就再落一张;返回真正写出去的文件清单。"""
        stem = Path(stem)
        if self.source:
            self.canvas.text(28, self.h - 16, self.source, size=12, fill=PALETTE["muted"])
        saved = [self.canvas.save_svg(stem.with_suffix(".svg"))]
        try:
            saved.append(self.canvas.save_png(stem.with_suffix(".png"), scale=scale))
        except RuntimeError:
            pass
        return saved


def _axis_frame(cv, box, ylabel=""):
    x, y, w, h = box
    cv.line(x, y + h, x + w, y + h, stroke=PALETTE["ink"], sw=1.6)
    cv.line(x, y, x, y + h, stroke=PALETTE["ink"], sw=1.6)
    if ylabel:
        cv.text(x - 10, y + h / 2, ylabel, size=14, fill=PALETTE["muted"],
                anchor="middle", rotate=-90)


def _nice_ticks(lo, hi, want=5):
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(1, want)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            step = m * mag
            break
    else:
        step = 10 * mag
    out, v = [], math.ceil(lo / step) * step
    while v <= hi + step * 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def grouped_bars(cv, box, categories, series, labels, ylabel="", colors=None,
                 annotate=False, ylim=None, value_fmt="{:.2f}"):
    """分组柱状图:`series` 每个元素是一条与 `categories` 等长的数值数组。"""
    x, y, w, h = box
    n, groups = len(categories), len(series)
    if n == 0 or groups == 0:
        return
    colors = colors or DEFAULT_COLORS
    flat = [v for s in series for v in s]
    lo = 0.0
    hi = max(flat) if flat else 1.0
    if ylim:
        lo, hi = ylim
    else:
        hi = hi * 1.12 if hi > 0 else 1.0
    _axis_frame(cv, box, ylabel)
    for t in _nice_ticks(lo, hi):
        ty = y + h - (t - lo) / (hi - lo) * h
        cv.line(x, ty, x + w, ty, stroke=PALETTE["grid"], sw=1.0, dash=4)
        cv.text(x - 8, ty + 4, f"{t:g}", size=12, fill=PALETTE["muted"], anchor="end")
    slot = w / n
    bw = slot * 0.72 / groups
    for i, cat in enumerate(categories):
        for j, s in enumerate(series):
            v = s[i]
            bh = max(0.0, (v - lo) / (hi - lo) * h)
            bx = x + slot * i + slot * 0.14 + bw * j
            cv.rect(bx, y + h - bh, bw, bh, fill=colors[j % len(colors)],
                    stroke=PALETTE["ink"], sw=0.8)
            if annotate:
                cv.text(bx + bw / 2, y + h - bh - 6, value_fmt.format(v), size=11,
                        anchor="middle")
        cv.text(x + slot * i + slot / 2, y + h + 20,
                _fit(cat, slot, 13), size=13, anchor="middle")
    legend(cv, (x, y - 26), labels, colors)


def h_bars(cv, box, labels, values, colors=None, xlabel="", annotate=True,
           value_fmt="{:g}", highlight=None, label_room=None, show_labels=True):
    """横向条形图:一行一个标签。单系列默认同一个色,`highlight` 里的那几条提亮。

    与热力图并排当"边际小条"用时传 `show_labels=False` —— 行标签由热力图那侧出,
    两栏按同一顺序对齐,不必把同一串名字画两遍。
    """
    x, y, w, h = box
    n = len(labels)
    if n == 0:
        return
    base = colors[0] if colors else PALETTE["blue_secondary"]
    hi = max(values) if values else 1.0
    hi = hi * 1.08 if hi > 0 else 1.0
    slot = h / n
    cv.line(x, y, x, y + h, stroke=PALETTE["ink"], sw=1.6)
    for i, (lab, v) in enumerate(zip(labels, values)):
        bh = slot * 0.62
        by = y + slot * i + (slot - bh) / 2
        bw = max(0.0, v / hi * w)
        col = PALETTE["blue_main"] if (highlight and lab in highlight) else base
        cv.rect(x, by, bw, bh, fill=col, stroke=PALETTE["ink"], sw=0.7)
        if show_labels:
            room = label_room if label_room is not None else (x - 16)
            cv.text(x - 8, by + bh * 0.72, _fit(lab, room, 13), size=13, anchor="end")
        if annotate:
            cv.text(x + bw + 6, by + bh * 0.72, value_fmt.format(v), size=12,
                    fill=PALETTE["muted"])
    if xlabel:
        cv.text(x + w / 2, y + h + 26, xlabel, size=13,
                fill=PALETTE["muted"], anchor="middle")


def heatmap(cv, box, matrix, x_labels, y_labels, levels=None, annotate=True,
            xlabel="", cell_text=None):
    """离散色阶热力图。`matrix[i][j]` 是行 i、列 j 的等级(默认 0~3)。"""
    x, y, w, h = box
    rows, cols = len(matrix), len(matrix[0]) if matrix else 0
    if not rows or not cols:
        return
    levels = levels or LEVEL_COLORS
    cw, ch = w / cols, h / rows
    for i in range(rows):
        for j in range(cols):
            v = matrix[i][j]
            cv.rect(x + cw * j, y + ch * i, cw, ch,
                    fill=levels.get(v, PALETTE["white"]),
                    stroke=PALETTE["white"], sw=1.2)
            if annotate and cell_text:
                cv.text(x + cw * (j + 0.5), y + ch * (i + 0.72),
                        cell_text(v), size=min(13, ch * 0.6), anchor="middle")
    for j, lab in enumerate(x_labels):
        cv.text(x + cw * (j + 0.5), y - 8, _fit(lab, cw, 12), size=12,
                fill=PALETTE["muted"], anchor="middle")
    for i, lab in enumerate(y_labels):
        cv.text(x - 8, y + ch * (i + 0.72), _fit(lab, x - 16, 12), size=12,
                anchor="end")
    if xlabel:
        cv.text(x + w / 2, y + h + 26, xlabel, size=13,
                fill=PALETTE["muted"], anchor="middle")


def trend_lines(cv, box, xs, y_series, labels, ylabel="", xlabel="", colors=None):
    """折线图:每一条与 `xs` 等长。不做插值,不做平滑。"""
    x, y, w, h = box
    colors = colors or DEFAULT_COLORS
    flat = [v for s in y_series for v in s]
    if not flat:
        return
    lo, hi = min(flat), max(flat)
    pad = (hi - lo) * 0.1 or 1.0
    lo, hi = lo - pad, hi + pad
    _axis_frame(cv, box, ylabel)
    for t in _nice_ticks(lo, hi):
        ty = y + h - (t - lo) / (hi - lo) * h
        cv.line(x, ty, x + w, ty, stroke=PALETTE["grid"], sw=1.0, dash=4)
        cv.text(x - 8, ty + 4, f"{t:g}", size=12, fill=PALETTE["muted"], anchor="end")
    span = (max(xs) - min(xs)) or 1.0
    for k, s in enumerate(y_series):
        pts = [(x + (xv - min(xs)) / span * w,
                y + h - (yv - lo) / (hi - lo) * h) for xv, yv in zip(xs, s)]
        cv.polyline(pts, stroke=colors[k % len(colors)], sw=2.4)
    if xlabel:
        cv.text(x + w / 2, y + h + 28, xlabel, size=13,
                fill=PALETTE["muted"], anchor="middle")
    legend(cv, (x, y - 26), labels, colors)


def legend(cv, xy, labels, colors, size=13, gap=18):
    """一行小图例(色块 + 文字)。放在图外,不压数据。"""
    x, y = xy
    for i, lab in enumerate(labels):
        cv.rect(x, y - 10, 12, 12, fill=colors[i % len(colors)],
                stroke=PALETTE["ink"], sw=0.6)
        cv.text(x + 18, y, lab, size=size)
        x += 18 + text_width(lab, size) + gap


def slugify(text: str, fallback: str = "figure") -> str:
    out = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "-", str(text or "")).strip("-")
    return out or fallback
