"""图表模块的口径测试:数字对得上、不合规就拒、产物自包含。"""
import math
import re

import pytest

from minicode.charts import (ChartError, chart_file, nice_ticks, render,
                             render_bar, render_pie)


class _Ctx:
    def __init__(self, workspace):
        self.workspace = str(workspace)


DATA = [{"label": "高数", "value": 88}, {"label": "英语", "value": 72},
        {"label": "大物", "value": 95}]


def test_bar_draws_one_rect_and_one_value_label_per_point():
    svg = render_bar(DATA, title="成绩")
    assert svg.count("<rect") == len(DATA) + 1        # +1 是白底
    for row in DATA:
        # 每个数据点都必须在图里留一份"标签 + 原值":挤的时候靠 <title>,不是靠删
        assert f"<title>{row['label']} {row['value']}</title>" in svg
    assert "成绩" in svg


def test_axis_ticks_are_round_numbers_not_float_noise():
    svg = render_bar([{"label": "a", "value": 13734.29},
                      {"label": "b", "value": 12000}], title="")
    assert "1373.42857" not in svg and ".4285" not in svg
    ticks = nice_ticks(0, 14000)
    assert ticks == sorted(ticks) and ticks[0] <= 0 and ticks[-1] >= 14000
    assert all(abs(t) % 500 < 1e-9 for t in ticks), ticks


def test_line_points_match_input_and_are_deterministic():
    svg = render("line", DATA, title="趋势", source="教务系统")
    pts = [p for p in svg.split('points="')[1].split('"')[0].split(" ") if p]
    assert len(pts) == len(DATA)
    assert "数据来源:教务系统" in svg
    assert svg == render("line", DATA, title="趋势", source="教务系统"), \
        "同一份输入必须得到同一张图(里面不许有时间戳/随机数)"


def test_pie_percentages_come_from_the_values():
    svg = render_pie(DATA, title="占比", donut=True)
    total = sum(r["value"] for r in DATA)
    for row in DATA:
        assert f"{row['value'] / total * 100:.1f}%" in svg
    assert f">{total}<" in svg                        # 环心写的是真合计


def test_bar_with_negative_draws_below_the_zero_line():
    svg = render_bar([{"label": "上", "value": 3.5}, {"label": "下", "value": -2}],
                     title="")
    assert "-2<" in svg and "3.5<" in svg
    assert 'stroke-width="1.2"' in svg                # 0 线加重


# ------------------------------------------------------------------ 该拒就拒

def test_empty_data_is_refused_not_drawn_as_blank():
    with pytest.raises(ChartError) as e:
        render("bar", [])
    assert "没有数据就不画图" in str(e.value)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "88分", None, True])
def test_non_finite_or_non_numeric_values_are_rejected(bad):
    with pytest.raises(ChartError) as e:
        render("bar", [{"label": "x", "value": bad}])
    msg = str(e.value)
    assert "第 1 条" in msg and "x" in msg, "报错要点名是哪一条"


def test_pie_rejects_negative_because_share_cannot_be_negative():
    with pytest.raises(ChartError) as e:
        render("pie", [{"label": "亏空", "value": -5}, {"label": "其余", "value": 10}])
    assert "亏空" in str(e.value) and "负值" in str(e.value)


def test_unknown_chart_type_lists_the_choices():
    with pytest.raises(ChartError) as e:
        render("radar", DATA)
    assert "bar" in str(e.value) and "pie" in str(e.value)


def test_malformed_item_says_what_shape_it_wanted():
    with pytest.raises(ChartError) as e:
        render("bar", ["just-a-string"])
    assert "label,value" in str(e.value)


def test_labels_are_escaped_so_text_cannot_break_the_svg():
    # 标签会被裁到 8 字,所以这里用短标签,验的是"转义"不是"长度"
    svg = render("bar", [{"label": "<b>&x", "value": 1}], title='&"危险"')
    assert "<b>" not in svg and "&lt;b&gt;&amp;x" in svg
    assert "&amp;&quot;危险&quot;" in svg
    assert svg.count("<script") == 0 and svg.count("<b>") == 0


def test_output_is_self_contained():
    svg = render("pie", DATA, title="t", source="s")
    assert "<script" not in svg and "href=" not in svg
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")


# ------------------------------------------------------------------ 落盘口径

def test_chart_file_writes_into_workspace_charts(tmp_path):
    out = chart_file(_Ctx(tmp_path), "bar", DATA, title="期末成绩",
                     source="教务系统导出", name="scores")
    path = tmp_path / "charts" / "scores.svg"
    assert path.is_file()
    assert "charts/scores.svg" in out
    assert "合计 255" in out and "大物=95" in out and "占 37.3%" in out
    assert "数据来源" in out
    assert path.read_text(encoding="utf-8").startswith("<svg")


def test_same_name_does_not_overwrite_the_previous_chart(tmp_path):
    chart_file(_Ctx(tmp_path), "bar", DATA, title="a", source="s")
    chart_file(_Ctx(tmp_path), "bar", DATA, title="a", source="s")
    names = sorted(p.name for p in (tmp_path / "charts").iterdir())
    assert len(names) == 2 and any(n.endswith("-2.svg") for n in names), names


def test_missing_source_is_flagged_in_the_reply(tmp_path):
    out = chart_file(_Ctx(tmp_path), "line", DATA, title="趋势")
    assert "没写 source" in out and "⚠" in out


def test_bad_data_writes_no_file(tmp_path):
    out = chart_file(_Ctx(tmp_path), "pie", [{"label": "x", "value": -1}],
                     title="t", source="s")
    assert "这次没画图" in out and "我不会替你补数" in out
    assert not (tmp_path / "charts").exists()


def test_all_values_are_finite_numbers_after_roundtrip(tmp_path):
    """图上印的每个数字,去掉千分位后都要能变回传入值(防止格式化把数写错)。"""
    rows = [{"label": f"科目{i}", "value": v}
            for i, v in enumerate([1234567, 0.5, 99, 10000])]
    svg = render("bar", rows, title="")
    for row in rows:
        want = f"{row['value']:,}"
        assert want in svg, f"{want} 没原样出现在图上"
        assert not math.isnan(float(want.replace(",", "")))


# ------------------------------------------------- 几何核算(替人眼看图的那一步)

def _texts(svg: str):
    """(x, y, 文本, 锚点, 字号) —— 估盒子用的四件套。"""
    out = []
    for m in re.finditer(r'<text x="(-?[\d.]+)" y="(-?[\d.]+)"([^>]*)>([^<]*)<', svg):
        rest = m.group(3)
        if 'text-anchor="middle"' in rest:
            anchor = "middle"
        elif 'text-anchor="end"' in rest:
            anchor = "end"
        else:
            anchor = "start"
        size = re.search(r'font-size="(\d+)"', rest)
        out.append((float(m.group(1)), float(m.group(2)), m.group(4), anchor,
                    float(size.group(1)) if size else 13.0))
    return out


def _box(text: str, size: float) -> float:
    """中文按 1 em、其他按 0.6 em 估宽:宁可在断言里保守一点。"""
    return sum(1.0 if ord(c) > 0x2E80 else 0.6 for c in text) * size


def _shape_coords(svg: str):
    """只从坐标类属性取数(颜色字符串里全是数字,整段扫会误报)。"""
    for m in re.finditer(r'<(rect|path|circle|polyline|line)\b([^>]*)>', svg):
        tag, attrs = m.group(1), m.group(2)
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
        if tag == "rect":
            x, y = float(a.get("x", 0)), float(a.get("y", 0))
            yield x, y
            if "width" in a and "height" in a:
                yield x + float(a["width"]), y + float(a["height"])
        elif tag == "circle":
            cx, cy = float(a["cx"]), float(a["cy"])
            yield cx, cy
        elif tag == "polyline":
            for pair in a.get("points", "").split():
                x, _comma, y = pair.partition(",")
                yield float(x), float(y)
        elif tag == "line":
            yield float(a["x1"]), float(a["y1"])
            yield float(a["x2"]), float(a["y2"])
        elif tag == "path":
            for x, y in re.findall(r"([\d.]+),([\d.]+)", a.get("d", "")):
                yield float(x), float(y)


@pytest.mark.parametrize("kind", ["bar", "line", "pie"])
@pytest.mark.parametrize("rows", [
    [{"label": "只有一项", "value": 7}],
    [{"label": "全零", "value": 0}, {"label": "也零", "value": 0}],
    [{"label": "负", "value": -500}, {"label": "正", "value": 500}],
    [{"label": "极大", "value": 9.87e9}, {"label": "极小", "value": 1e-6}],
    [{"label": f"第{i}周", "value": i * 3} for i in range(40)],
    [{"label": "标签特别长的课程名称超过八个字", "value": 3}] * 6,
])
def test_nothing_is_drawn_outside_the_canvas_or_on_top_of_itself(kind, rows):
    """截图窗口拿不到,就把"看起来对不对"变成硬不变量:
    任何坐标、任何文字都必须落在画布内。"""
    try:
        svg = render(kind, rows, title="标题", source="数据来源")
    except ChartError as e:
        # 饼图对"加起来为 0"和"有负值"是不画的(见 _clean_negative):
        # 这里允许它拒绝,但拒绝的理由必须点名是哪件事。
        assert kind == "pie" and ("负值" in str(e) or "0" in str(e)), e
        return
    W = float(re.search(r'width="(\d+)"', svg).group(1))
    H = float(re.search(r'height="(\d+)"', svg).group(1))
    for x, y in _shape_coords(svg):
        assert -2 <= x <= W + 2 and -2 <= y <= H + 2, \
            f"坐标出界 ({x}, {y}) 画布 {W}x{H}:{rows[0]}"
    for x, y, text, anchor, size in _texts(svg):
        w = _box(text, size)
        left = x - w / 2 if anchor == "middle" else (x - w if anchor == "end" else x)
        assert left >= -2 and left + w <= W + 2, f"文字横向出界:{text!r} @x={x}"
        assert 0 < y < H, f"文字纵向出界:{text!r} @y={y}"


def test_crowded_axis_thins_labels_instead_of_overprinting():
    """40 个点时标签必须隔位出现(挤成一团是最常见的翻车方式),
    但数值本身仍留在文件里(<title> 悬停可读),不是被删掉了。"""
    rows = [{"label": f"第{i}周", "value": i * 3} for i in range(40)]
    svg = render("bar", rows, title="")
    painted = [t for t in _texts(svg) if t[2].startswith("第") and "周" in t[2]]
    assert 0 < len(painted) < len(rows), f"画了 {len(painted)} 个标签,没做隔位"
    xs = sorted(p[0] for p in painted)
    widths = [_box(p[2], p[4]) for p in sorted(painted, key=lambda p: p[0])]
    assert all(b - a >= max(wa, wb) - 1
               for a, b, wa, wb in zip(xs, xs[1:], widths, widths[1:])), "标签仍互相压字"
    assert svg.count("<title>") >= len(rows), "挤掉的数值要用 <title> 留在图里"


def test_x_axis_labels_do_not_collide_when_there_is_room():
    """数据点不多时,x 轴标签必须错开(这是最容易看出来的翻车方式)。"""
    svg = render("bar", [{"label": f"科目{i}", "value": i + 60} for i in range(4)],
                 title="")
    labels = [t for t in _texts(svg) if t[2].startswith("科目")]
    assert len(labels) == 4
    xs = sorted(l[0] for l in labels)
    assert max(b - a for a, b in zip(xs, xs[1:])) >= 40, "标签挤在一起了"


def test_pie_refuses_more_slices_than_it_has_colors():
    rows = [{"label": f"项{i}", "value": 1} for i in range(11)]
    with pytest.raises(ChartError) as e:
        render("pie", rows, title="")
    assert "10 块" in str(e.value) and "bar" in str(e.value)
    assert render("pie", rows[:10], title="")          # 正好 10 块画得出来


def test_tiny_nonzero_value_is_never_printed_as_zero():
    svg = render("bar", [{"label": "微量", "value": 1e-6}, {"label": "常规", "value": 5}],
                 title="")
    assert "1e-06" in svg                      # 印成 "0" 就是凭空造了个零
    assert "<title>微量 1e-06</title>" in svg   # 数值也留在悬停标题里,没被抹平


def test_axis_ticks_switch_to_万_and_亿_for_big_numbers():
    svg = render("bar", [{"label": "营收", "value": 9_870_000_000}], title="")
    assert "亿" in svg                          # 轴上缩写,免得字伸出画布
    assert "9,870,000,000" in svg               # 柱子上仍是精确原值
    svg2 = render("bar", [{"label": "人数", "value": 25_000},
                          {"label": "更少", "value": 3_000}], title="")
    assert "万" in svg2 and "25,000" in svg2
