"""零依赖图表:把数字画成一张自包含的 SVG。

为什么不用 matplotlib —— 打包脚本 `packaging/gcode.spec` 的 excludes 里明确排掉了
matplotlib/numpy(体积与启动时间),所以这里只用标准库手写 SVG。

三条规矩:

1. **数据必须是调用方真给出来的**。本模块不取数、不补数、不猜缺失值;
   少一个数就报错,不会为了"图好看"把空缺插成 0。
2. **图上每个数字都能回溯**。标题、坐标轴刻度、扇区百分比全部由传入的
   values 现算;`source` 会作为脚注印在图里,看图像的人知道数据从哪来。
3. **产物自包含**:输出是一张纯 SVG(无外链、无脚本),浏览器和微信都能直接开;
   同一份输入永远得到同一张图(不写时间戳、不用随机数)。
"""
from __future__ import annotations

import math
import re
from pathlib import Path

CHART_TYPES = ("bar", "line", "pie")
MAX_POINTS = 200
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 420
PALETTE = ("#4C78A8", "#F58518", "#E45756", "#72B7B2", "#54A24B", "#B279A2",
           "#FF9DA6", "#9D755D", "#BAB0AC", "#8CD17D")
NAME_SAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")
# 饼图每块一个颜色:超过配色数量就失去"看得出是同一类"的意义,直接拒绝并指路。
PIE_MAX_SLICES = len(PALETTE)


class ChartError(ValueError):
    """数据或参数不成立。message 里要点名是哪一条,不能只说"格式错误"。"""


# --------------------------------------------------------------------- 校验

def _num(v, where: str) -> float:
    """只接受真数字。NaN/inf 画出来是没意义的图形,一律拒。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ChartError(f"{where} 的值必须是数字,收到的是 {type(v).__name__}:{v!r}")
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        raise ChartError(f"{where} 的值不是有限数字:{v!r}")
    return f


def normalize_data(data) -> list[tuple[str, float]]:
    """把 [{"label":..,"value":..}] / {k:v} / [(k,v)] 统一成 (标签, 数值)。"""
    if isinstance(data, dict):
        pairs = list(data.items())
    elif isinstance(data, (list, tuple)):
        pairs = []
        for i, item in enumerate(data):
            if isinstance(item, dict):
                label = item.get("label", item.get("name", item.get("x")))
                value = item.get("value", item.get("y"))
                if value is None:
                    raise ChartError(f"第 {i + 1} 条数据没有 value 字段:{item!r}")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                label, value = item[0], item[1]
            else:
                raise ChartError(f"第 {i + 1} 条数据既不是 {{label,value}} 也不是 [标签,数值]:"
                                 f"{item!r}")
            pairs.append((label, value))
    else:
        raise ChartError(f"data 只能是列表或字典,收到 {type(data).__name__}")
    if not pairs:
        raise ChartError("data 是空的:没有数据就不画图,改回文字说明")
    if len(pairs) > MAX_POINTS:
        raise ChartError(f"数据 {len(pairs)} 条,超过单图上限 {MAX_POINTS} 条;"
                         "先聚合成关键节点再来")
    out: list[tuple[str, float]] = []
    for i, (label, value) in enumerate(pairs):
        text = str("" if label is None else label).strip() or f"#{i + 1}"
        out.append((text, _num(value, f"第 {i + 1} 条（{text}）")))
    return out


def _clean_negative(rows, chart_type: str) -> None:
    if chart_type == "pie":
        bad = [r for r in rows if r[1] < 0]
        if bad:
            raise ChartError("饼图不接受负值(占比没有负数这回事):"
                             + "、".join(f"{l}={v:g}" for l, v in bad[:3]))
        if sum(v for _l, v in rows) <= 0:
            raise ChartError("饼图所有值加起来不大于 0,没法算占比")


# --------------------------------------------------------------------- 画图

def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _fmt(v: float) -> str:
    """数值标签用:整数不带小数点,大数用千分位。

    极小的非零值走 3 位有效数字 —— 不然 `1e-6` 会被印成 "0",
    图上就凭空多出一个"看着像零"的假数。
    """
    if v and abs(v) < 0.01:
        return f"{v:.3g}"
    if abs(v - round(v)) < 1e-9 and abs(v) < 1e15:
        return f"{int(round(v)):,}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _tick_fmt(v: float) -> str:
    """坐标轴刻度:过大的数换成"万/亿"写法。

    轴刻度是参照物,缩写不影响读数值(柱子上仍印精确原值);
    但把 `9,870,000,000` 直接写在左边距 50px 的轴上,字会伸出画布外(实测会)。
    """
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:g}亿"
    if a >= 1e4:
        return f"{v / 1e4:g}万"
    return _fmt(v)


def _est(text: str, size: float = 11.0) -> float:
    """估一段字要占多宽:中日韩按 1 em、其余按 0.6 em。

    只用来判断"这一格放不放得下一条标签",不做精确排版 ——
    宁可少画一条,也不让两段字叠成一团。
    """
    return sum(1.0 if ord(c) > 0x2E80 else 0.6 for c in str(text)) * size


def _thin(labels: list[str], slot: float, size: float = 11.0) -> int:
    """x 轴标签的隔位步长:一格塞不下最宽的一条标签就隔 n 格画一条。"""
    if slot <= 0:
        return 1
    widest = max((_est(l, size) for l in labels), default=0.0)
    return max(1, int(math.ceil((widest + 4) / slot)))


def _anchor(text: str, x: float, size: float, canvas_w: float) -> float:
    """居中对齐的文字:离左右边太近时往里推(线/柱末端的标签最容易顶出画布)。"""
    half = _est(text, size) / 2
    return min(max(x, 3.0 + half), canvas_w - 3.0 - half)


def nice_ticks(lo: float, hi: float, want: int = 5) -> list[float]:
    """挑一组"人看得懂"的刻度:步长只取 1/2/2.5/5 × 10^n。

    这一步不是为了好看 —— 直接用 (hi-lo)/5 会印出 1373.42857 这种刻度,
    读图的人没法一眼对齐。
    """
    if hi == lo:
        hi = lo + 1
    raw = (hi - lo) / max(1, want)
    exp = math.floor(math.log10(abs(raw))) if raw else 0
    base = 10 ** exp
    step = next((m * base for m in (1, 2, 2.5, 5, 10) if m * base >= raw), 10 * base)
    start = math.floor(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 0.5 and len(ticks) < 40:
        ticks.append(round(v, 10))
        v += step
    return ticks


def _frame(width: int, height: int, title: str, source: str) -> dict:
    top = 46 if title else 20
    bottom = (44 if source else 30)
    return {"w": width, "h": height, "x0": 58, "y0": top,
            "pw": width - 58 - 22, "ph": height - top - bottom,
            "source_y": height - 14}


_FONT = "Microsoft YaHei, PingFang SC, Segoe UI, sans-serif"


def _head(title: str, width: int, height: int) -> list[str]:
    # 这里用 % 拼接而不是 f-string:SVG 属性全是双引号,f-string 里再套引号
    # 在 3.12 之前不合法,写成转义又容易出错(这条是真踩过的语法坑)。
    return [
        ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d"'
         ' viewBox="0 0 %d %d" font-family="%s" font-size="13">'
         % (width, height, width, height, _FONT)),
        '<rect width="%d" height="%d" fill="#ffffff"/>' % (width, height),
    ]


def _foot(title: str, source: str, f: dict, out: list[str]) -> str:
    if title:
        out.append(f'<text x="{f["x0"]}" y="26" font-size="16" fill="#1f2937"'
                   f' font-weight="600">{_esc(title)}</text>')
    if source:
        out.append(f'<text x="{f["x0"]}" y="{f["source_y"]}" font-size="11"'
                   f' fill="#6b7280">数据来源:{_esc(source)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def _axis(f: dict, ticks: list[float], lo: float, hi: float) -> list[str]:
    """画 y 轴网格与刻度。0 线单独加重(有负值时读图靠它)。"""
    lines = []
    for t in ticks:
        y = f["y0"] + f["ph"] - (t - lo) / (hi - lo) * f["ph"]
        weight = ' stroke-width="1.2"' if abs(t) < 1e-12 else ""
        color = "#9ca3af" if abs(t) < 1e-12 else "#e5e7eb"
        lines.append(f'<line x1="{f["x0"]}" y1="{y:.1f}" x2="{f["x0"] + f["pw"]:.1f}"'
                     f' y2="{y:.1f}" stroke="{color}"{weight}/>')
        lines.append(f'<text x="{f["x0"] - 8}" y="{y + 4:.1f}" text-anchor="end"'
                     f' font-size="11" fill="#6b7280">{_tick_fmt(t)}</text>')
    return lines


def render_bar(rows, *, title="", source="", width=DEFAULT_WIDTH,
               height=DEFAULT_HEIGHT, color="") -> str:
    rows = normalize_data(rows)      # 也允许直接传 [{label,value}]:三个渲染函数都能自立
    f = _frame(width, height, title, source)
    values = [v for _l, v in rows]
    lo = min(0.0, min(values))
    hi = max(values)
    ticks = nice_ticks(lo, hi if hi > lo else hi + 1)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])
    out = _head(title, width, height) + _axis(f, ticks, lo, hi)
    n = len(rows)
    slot = f["pw"] / n
    bar_w = max(6.0, min(56.0, slot * 0.62))
    zero_y = f["y0"] + f["ph"] - (0 - lo) / (hi - lo) * f["ph"]
    labels = [_clip(l, 8) for l, _v in rows]
    # 挤的时候隔位画标签,而不是把两段字叠在一起;数值一个都不删,
    # 没画出来的那份留在 <title> 里(浏览器悬停可读,文件里也在)。
    step = _thin(labels, slot)
    vstep = _thin([_fmt(v) for _l, v in rows], slot)
    for i, (label, v) in enumerate(rows):
        x = f["x0"] + slot * (i + 0.5) - bar_w / 2
        y = f["y0"] + f["ph"] - (max(v, 0) - lo) / (hi - lo) * f["ph"]
        h = abs(v) / (hi - lo) * f["ph"]
        fill = color or PALETTE[i % len(PALETTE)]
        cx = f["x0"] + slot * (i + 0.5)
        out.append(f'<rect x="{x:.1f}" y="{min(y, zero_y):.1f}" width="{bar_w:.1f}"'
                   f' height="{max(h, 0.5):.1f}" rx="2" fill="{fill}">'
                   f'<title>{_esc(label)} {_fmt(v)}</title></rect>')
        if i % step == 0:
            out.append(f'<text x="{_anchor(labels[i], cx, 11, width):.1f}"'
                       f' y="{f["y0"] + f["ph"] + 16}" text-anchor="middle"'
                       f' font-size="11" fill="#374151">{_esc(labels[i])}</text>')
        if i % vstep == 0:
            out.append(f'<text x="{_anchor(_fmt(v), cx, 11, width):.1f}"'
                       f' y="{min(y, zero_y) - 5:.1f}" text-anchor="middle"'
                       f' font-size="11" fill="#1f2937">{_fmt(v)}</text>')
    return _foot(title, source, f, out)


def render_line(rows, *, title="", source="", width=DEFAULT_WIDTH,
                height=DEFAULT_HEIGHT, color="#4C78A8") -> str:
    rows = normalize_data(rows)
    f = _frame(width, height, title, source)
    values = [v for _l, v in rows]
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.08 or 1
    ticks = nice_ticks(lo - pad, hi + pad)
    lo, hi = min(lo - pad, ticks[0]), max(hi + pad, ticks[-1])
    out = _head(title, width, height) + _axis(f, ticks, lo, hi)
    n = len(rows)
    dx = f["pw"] / (n - 1) if n > 1 else 0

    def xy(i, v):
        x = f["x0"] + (dx * i if n > 1 else f["pw"] / 2)
        return x, f["y0"] + f["ph"] - (v - lo) / (hi - lo) * f["ph"]

    pts = " ".join("%.1f,%.1f" % xy(i, v) for i, (_l, v) in enumerate(rows))
    out.append(f'<polyline points="{pts}" fill="none" stroke="{color}"'
               f' stroke-width="2" stroke-linejoin="round"/>')
    labels = [_clip(l, 8) for l, _v in rows]
    step = _thin(labels, dx)
    vstep = _thin([_fmt(v) for _l, v in rows], dx)
    for i, (label, v) in enumerate(rows):
        x, y = xy(i, v)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}">'
                   f'<title>{_esc(label)} {_fmt(v)}</title></circle>')
        if i % step == 0:
            out.append(f'<text x="{_anchor(labels[i], x, 11, width):.1f}"'
                       f' y="{f["y0"] + f["ph"] + 16:.1f}"'
                       f' text-anchor="middle" font-size="11" fill="#374151">'
                       f'{_esc(labels[i])}</text>')
        if i % vstep == 0:
            out.append(f'<text x="{_anchor(_fmt(v), x, 11, width):.1f}"'
                       f' y="{y - 9:.1f}" text-anchor="middle"'
                       f' font-size="11" fill="#1f2937">{_fmt(v)}</text>')
    return _foot(title, source, f, out)


def render_pie(rows, *, title="", source="", width=DEFAULT_WIDTH,
               height=DEFAULT_HEIGHT, donut=False) -> str:
    rows = normalize_data(rows)
    f = _frame(width, height, title, source)
    total = sum(v for _l, v in rows)
    cx = f["x0"] + f["pw"] * 0.30
    cy = f["y0"] + f["ph"] / 2
    r = min(f["pw"] * 0.28, f["ph"] / 2 - 4)
    inner = r * 0.55 if donut else 0.0
    out = _head(title, width, height)
    n = len(rows)
    if n > PIE_MAX_SLICES:
        raise ChartError(
            f"饼图最多 {PIE_MAX_SLICES} 块(每块一个颜色,再多就分不出来了),"
            f"现在有 {n} 块:先合并小项,或改用 bar")
    row_h = min(22.0, (f["ph"] - 8) / n)
    size = 12 if row_h >= 16 else 11
    angle = -math.pi / 2
    for i, (label, v) in enumerate(rows):
        share = v / total
        sweep = share * math.pi * 2
        a0, a1 = angle, angle + sweep
        angle = a1
        fill = PALETTE[i % len(PALETTE)]
        large = 1 if sweep > math.pi else 0
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        if donut:
            ix0, iy0 = cx + inner * math.cos(a1), cy + inner * math.sin(a1)
            ix1, iy1 = cx + inner * math.cos(a0), cy + inner * math.sin(a0)
            d = (f"M{x0:.1f},{y0:.1f} A{r:.1f},{r:.1f} 0 {large} 1 {x1:.1f},{y1:.1f}"
                 f" L{ix0:.1f},{iy0:.1f} A{inner:.1f},{inner:.1f} 0 {large} 0"
                 f" {ix1:.1f},{iy1:.1f} Z")
        else:
            d = (f"M{cx:.1f},{cy:.1f} L{x0:.1f},{y0:.1f} A{r:.1f},{r:.1f} 0 {large} 1"
                 f" {x1:.1f},{y1:.1f} Z")
        out.append(f'<path d="{d}" fill="{fill}" stroke="#fff" stroke-width="1"/>')
        ly = f["y0"] + 12 + i * row_h
        lx = f["x0"] + f["pw"] * 0.60
        # 百分比就从传入的 v/total 现算,图上写的和算式对得上
        tail = f" {_fmt(v)}（{share * 100:.1f}%）"
        room = width - 4 - (lx + 18) - _est(tail, size)
        text = _fit(label, room, size) + tail
        out.append(f'<rect x="{lx:.1f}" y="{ly - size + 2:.1f}" width="12"'
                   f' height="{min(12, row_h - 2):.1f}" rx="2" fill="{fill}"/>')
        out.append(f'<text x="{lx + 18:.1f}" y="{ly:.1f}" font-size="{size}"'
                   f' fill="#374151">{_esc(text)}</text>')
    if donut:
        out.append(f'<text x="{cx:.1f}" y="{cy + 5:.1f}" text-anchor="middle"'
                   f' font-size="14" fill="#1f2937">{_fmt(total)}</text>')
    return _foot(title, source, f, out)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _fit(text: str, room: float, size: float = 11.0) -> str:
    """按可用宽度裁字:放不下就截断加省略号,而不是让它伸到画布外面。"""
    text = str(text)
    if room <= _est("…", size):
        return ""
    acc = ""
    for ch in text:
        if _est(acc + ch + ("…" if len(acc) + 1 < len(text) else ""), size) > room:
            break
        acc += ch
    return acc + ("…" if len(acc) < len(text) else "")


def render(chart_type: str, data, *, title: str = "", source: str = "",
           width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT,
           donut: bool = False, color: str = "") -> str:
    """按类型出一张 SVG 文本。数值非法/为空一律抛 ChartError(不静默补 0)。"""
    kind = (chart_type or "").strip().lower()
    if kind not in CHART_TYPES:
        raise ChartError(f"不认识图表类型「{chart_type}」,可选:{'、'.join(CHART_TYPES)}")
    rows = normalize_data(data)
    _clean_negative(rows, kind)
    width = max(240, int(width))
    height = max(200, int(height))
    kw = dict(title=str(title or ""), source=str(source or ""),
              width=width, height=height)
    if kind == "bar":
        return render_bar(rows, color=color, **kw)
    if kind == "line":
        return render_line(rows, color=color or "#4C78A8", **kw)
    return render_pie(rows, donut=donut, **kw)


# --------------------------------------------------------------------- 落盘

def slugify(text: str, fallback: str = "chart") -> str:
    name = NAME_SAFE.sub("-", str(text or "")).strip("-.")
    return name[:48] or fallback


def chart_file(ctx, chart_type: str, data, title: str = "", source: str = "",
               name: str = "", width: int = DEFAULT_WIDTH,
               height: int = DEFAULT_HEIGHT, donut: bool = False) -> str:
    """工具出口:画完写到工作区 charts/ 下,回一段说明文字(含图里每个数的口径)。"""
    workspace = Path(getattr(ctx, "workspace", ".") or ".").expanduser().resolve()
    try:
        svg = render(chart_type, data, title=title, source=source, width=width,
                     height=height, donut=bool(donut))
    except ChartError as e:
        return f"这次没画图:{e}。请先确认数据本身,我不会替你补数或改数。"
    rows = normalize_data(data)
    stem = slugify(name or title or str(chart_type))
    out_dir = workspace / "charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.svg"
    i = 2
    while path.exists():            # 不覆盖上一张:同名就编号,免得把别人的图弄丢
        path = out_dir / f"{stem}-{i}.svg"
        i += 1
    try:
        path.write_text(svg, encoding="utf-8")
    except OSError as e:
        return f"图算好了但写不出去:{path}({type(e).__name__}: {e})"
    total = sum(v for _l, v in rows)
    top = max(rows, key=lambda r: r[1])
    stat = (f"合计 {total:g}｜最大 {top[0]}={top[1]:g}"
            f"（占 {top[1] / total * 100:.1f}%）" if total else f"合计 {total:g}")
    try:
        rel = path.relative_to(workspace).as_posix()
    except ValueError:              # 工作区被换成了别的盘/链接时,退回绝对路径
        rel = str(path)
    return (f"已生成 {chart_type} 图:{rel}（{len(rows)} 个数据点，"
            f"{width}×{height}，自包含 SVG，浏览器直接打开）\n"
            f"图上数字口径：{stat}\n"
            + (f"数据来源（已印在图脚注）：{source}\n" if source else
               "⚠ 没写 source：图上没有任何来源信息，请补数据来源或说明数据是用户给的\n")
            + "注意：图里的每个数值都来自本次传入的 data，程序没有取数、补数或修正。")
