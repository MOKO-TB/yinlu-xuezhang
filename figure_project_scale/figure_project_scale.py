"""把「引路学长」本仓的工程规模画成一张出版级配图。

这不是手抄数据:脚本**直接扫仓库**再渲染 —— 代码改了,重跑这张图就跟着改。
三个面板共用一条基线:
  左  `minicode/*.py` 的代码行数 Top 12(谁最大)
  中  `tests/test_*.py` 的用例数 Top 12(谁测得最多)
  右  规模集中度:把模块/测试文件按规模降序排,看前百分之几吃掉了百分之几
      —— 这一栏回答的是"这个仓是均匀的还是一根独苗撑着的"。

用法(仓库根目录):

    python figure_project_scale/figure_project_scale.py

产物落在 figure_project_scale/figures/ 下的 .svg 与 .png。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "skills" / "scientific-figure-making" / "references"
sys.path.insert(0, str(KIT))

from figure_kit import (Figure, PALETTE, h_bars, legend,  # noqa: E402
                        _axis_frame, _nice_ticks)

TOP_N = 12
CANVAS_W, CANVAS_H = 2100, 820


# ------------------------------------------------------------------ 取数
def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def _tests(path: Path) -> int:
    """只数模块级的 `def test_`,与 `pytest --collect-only` 的口径一致到函数这一层。"""
    return sum(1 for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()
               if ln.startswith("def test_"))


def scan():
    mods = sorted(((_lines(p), p.name) for p in (ROOT / "minicode").glob("*.py")),
                  reverse=True)
    tests = sorted(((_tests(p), p.name) for p in (ROOT / "tests").glob("test_*.py")),
                   reverse=True)
    return mods, tests


def cumulative(values):
    """降序数组 → 每个位置上的累积占比(%),用于集中度曲线。"""
    total = sum(values)
    out, acc = [], 0
    for v in values:
        acc += v
        out.append(acc / total * 100 if total else 0.0)
    return out


def percentile(n: int):
    return [i / (n - 1) * 100 for i in range(n)] if n > 1 else [100.0]


# ------------------------------------------------------------------ 画
def _panel_title(cv, box, text):
    cv.text(box[0], box[1] - 16, text, size=15, weight="bold")


def _concentration(cv, box, series, xlabel, ylabel, colors, labels):
    """集中度曲线:横轴是按规模降序的百分位,纵轴是累积占比。

    内核的 `trend_lines` 不给 `ylim`,0~100 会被它自带的那圈留白撑到 -10~110,
    刻度就变成 -25/0/…/125 —— 所以这一栏按同一族风格手搭,轴框与刻度仍用内核的。
    多画一条灰色对角线当"完全均匀"的参照:曲线离它越远,说明越靠少数几个撑。
    """
    x, y, w, h = box
    lo, hi = 0.0, 100.0
    _axis_frame(cv, box, ylabel)
    for t in _nice_ticks(lo, hi):
        ty = y + h - (t - lo) / (hi - lo) * h
        cv.line(x, ty, x + w, ty, stroke=PALETTE["grid"], sw=1.0, dash=4)
        cv.text(x - 8, ty + 4, f"{t:g}", size=12, fill=PALETTE["muted"], anchor="end")
    for t in (0, 20, 40, 60, 80, 100):
        cv.text(x + t / 100 * w, y + h + 20, f"{t}", size=12,
                fill=PALETTE["muted"], anchor="middle")
    cv.text(x + w / 2, y + h + 44, xlabel, size=13, fill=PALETTE["muted"], anchor="middle")
    cv.line(x, y + h, x + w, y, stroke=PALETTE["neutral"], sw=1.6, dash=6)
    for pts, col in series:
        n = len(pts)
        xy = [(x + percentile(n)[i] / 100 * w, y + h - v / 100 * h)
              for i, v in enumerate(pts)]
        cv.polyline(xy, stroke=col, sw=2.4)
    legend(cv, (x, y - 40), labels, colors)


def _share_at(series, n, pct):
    """降序序列在"前 pct% 的条目"处累积到多少(线性插值,不脑补趋势)。"""
    pos = pct / 100 * (n - 1)
    i, f = int(pos), pos - int(pos)
    return series[i] + (series[min(i + 1, n - 1)] - series[i]) * f


def _crosshair(cv, box, pct, values, colors, note_lines):
    """在横轴 pct 处竖一条参照线,标出各序列在此处的累积占比,再把实话写在右下空档。"""
    x, y, w, h = box
    vx = x + pct / 100 * w
    cv.line(vx, y, vx, y + h, stroke=PALETTE["muted"], sw=1.0, dash=(4, 4))
    cv.text(vx + 5, y + 14, f"前 {pct:g}%", size=12, fill=PALETTE["muted"])
    for v, col in zip(values, colors):
        vy = y + h - v / 100 * h
        cv.circle(vx, vy, 4.5, fill=col, stroke=PALETTE["white"], sw=1.4)
    tx, ty = x + w * 0.30, y + h * 0.46
    for k, (line, col) in enumerate(zip(note_lines, colors)):
        if col:
            cv.rect(tx, ty + k * 22 - 9, 11, 11, fill=col, stroke=PALETTE["ink"], sw=0.6)
        cv.text(tx + (17 if col else 0), ty + k * 22, line, size=12)


def render(mods, tests, out_stem: Path):
    mod_lines = [n for n, _ in mods]
    test_counts = [n for n, _ in tests]
    total_lines = sum(mod_lines)
    total_tests = sum(test_counts)

    fig = Figure(
        width=CANVAS_W, height=CANVAS_H,
        title="「引路学长」工程规模与测试强度实测",
        subtitle=f"全仓 {len(mods)} 个模块 / {total_lines:,} 行代码 · "
                 f"{len(tests)} 个测试文件 / {total_tests:,} 个用例",
        source="数据来源:本仓工作树直接扫描(2026-10-08)—— "
               "minicode/*.py 的行数、tests/test_*.py 的 def test_ 计数;"
               "本图由 figure_project_scale/figure_project_scale.py 现算现画,无手抄",
    )

    top_mods = mods[:TOP_N]
    top_tests = tests[:TOP_N]

    box_a = fig.box(0.085, 0.245, 0.235, 0.60)
    _panel_title(fig.canvas, box_a, f"minicode 模块代码行数 Top {TOP_N}")
    h_bars(fig.canvas, box_a,
           [name for _, name in top_mods],
           [n for n, _ in top_mods],
           xlabel="代码行数", value_fmt="{:,.0f}",
           highlight={top_mods[0][1]})

    box_b = fig.box(0.400, 0.245, 0.235, 0.60)
    _panel_title(fig.canvas, box_b, f"tests 文件用例数 Top {TOP_N}")
    h_bars(fig.canvas, box_b,
           [name for _, name in top_tests],
           [n for n, _ in top_tests],
           xlabel="def test_ 计数", value_fmt="{:,.0f}",
           highlight={top_tests[0][1]})

    box_c = fig.box(0.735, 0.245, 0.235, 0.60)
    _panel_title(fig.canvas, box_c, "规模集中度(降序累积占比)")
    cum_m, cum_t = cumulative(mod_lines), cumulative(test_counts)
    _concentration(fig.canvas, box_c,
                   [(cum_m, PALETTE["blue_main"]), (cum_t, PALETTE["teal"])],
                   "按规模降序的百分位 (%)", "累积占比 (%)",
                   [PALETTE["blue_main"], PALETTE["teal"], PALETTE["neutral"]],
                   ["代码行数", "测试用例数", "完全均匀(参照)"])

    # 一句能一眼看懂的实话:前 20% 的条目吃掉了多少
    pct = 20
    sm = _share_at(cum_m, len(cum_m), pct)
    st = _share_at(cum_t, len(cum_t), pct)
    _crosshair(fig.canvas, box_c, pct, [sm, st],
               [PALETTE["blue_main"], PALETTE["teal"]],
               [f"前 {pct}% 的模块 → 代码 {sm:.0f}%",
                f"前 {pct}% 的测试文件 → 用例 {st:.0f}%"])
    return fig.save(out_stem)


def main() -> int:
    mods, tests = scan()
    if not mods or not tests:
        print("扫不到 minicode/ 或 tests/,什么都没画")
        return 1
    print(f"minicode:{len(mods)} 个模块 / {sum(n for n, _ in mods):,} 行;"
          f"最大 {mods[0][1]} {mods[0][0]:,} 行")
    print(f"tests:{len(tests)} 个文件 / {sum(n for n, _ in tests):,} 个用例;"
          f"最多 {tests[0][1]} {tests[0][0]:,} 个")
    out = ROOT / "figure_project_scale" / "figures" / "project_scale"
    for path in render(mods, tests, out):
        print(f"已写出 {path.relative_to(ROOT)}({path.stat().st_size:,} 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
