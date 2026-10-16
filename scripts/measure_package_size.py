# -*- coding: utf-8 -*-
"""量"这个包里到底装了什么" —— 一份**只读**的容量账,给增量更新那条路用。

为什么要有这么一只脚本(而不是临时写一遍就扔):`docs/roadmap-增量更新.md` 里每一个
"省了多少"的数字都必须**可复量**。发布说明要写"从 878 MB 降到 X MB",那个 X 不能是
按比例推的;而"按比例推"正是这个项目反复清退的那类假数。三条口径:

- **只读**:打开 zip 的中央目录、打开 exe 的归档表,一个字节都不写、不删、不解压;
- **分桶互斥**:每条条目只进一个桶,最后**合计必须等于 `zipfile` 报的总数**,不等就报错
  (抽样估出来的表比没有表更坏);
- **量具不改被测物**:PyInstaller 的归档用**它自己的读取器**开(`CArchiveReader`),
  不自己解析字节头 —— 格式随版本变,而读取器跟着版本走。

跑法(项目根目录,**要带 utf-8**,否则那句中文表头在 GBK 控制台炸):

    ./.venv/Scripts/python.exe -X utf8 scripts/measure_package_size.py
    ./.venv/Scripts/python.exe -X utf8 scripts/measure_package_size.py --zip dist/xxx.zip
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 分桶规则(顺序即优先级,一条条目只进第一个命中的桶)。
# 为什么这么切:L3 是"跨版本基本不变的数据与内核",L2 是"第三方运行时",L1 是"我们
# 自己写的东西"。增量更新的整个论点就是"典型一轮只改 L1",所以这三层的**比例**才是
# 决策依据 —— 别的切法(按目录、按扩展名)量出来看不出这件事。
def bucket(n: str) -> str:
    if "/crawled/" in n:
        return "L3 馆藏库(crawled/)"
    if "ms-playwright" in n:
        return "L3 浏览器内核(ms-playwright/)"
    if "_internal/frontend" in n:
        return "L1 前端(index.html + 图)"
    if n.endswith("使用说明.txt"):
        return "L1 手册"
    if Path(n).name in ("引路学长.exe", "引路学长-CLI.exe", "yinlu-xuezhang.exe"):
        return "L1 主程序 exe(内含 PYZ)"
    return "L2 第三方运行时"


def measure_zip(zp: Path) -> None:
    z = zipfile.ZipFile(str(zp))
    info = z.infolist()
    groups: dict[str, list[int]] = {}
    tot_u = tot_c = 0
    for i in info:
        n = i.filename.replace("\\", "/")
        g = bucket(n)
        row = groups.setdefault(g, [0, 0, 0])
        row[0] += i.file_size
        row[1] += i.compress_size
        row[2] += 1
        tot_u += i.file_size
        tot_c += i.compress_size
    # 合计必须等于总数:这条不是洁癖,是"这张表能不能拿来决策"的分界线
    s_u = sum(r[0] for r in groups.values())
    s_c = sum(r[1] for r in groups.values())
    if (s_u, s_c) != (tot_u, tot_c):
        print(f"✗ 分桶合计对不上总数(未压缩 {s_u:,} vs {tot_u:,})—— 表不可信")
        raise SystemExit(2)
    print(f"\n=== {zp.name} ===")
    print(f"未压缩 {tot_u:,} B ({tot_u / 1073741824:.2f} GiB) / "
          f"压缩后 {tot_c:,} B ({tot_c / 1073741824:.2f} GiB) / {len(info)} 条"
          f"  ← 分桶合计逐字节相等,非抽样")
    print(f"{'分组':30s} {'未压缩':>15s} {'压缩后':>15s} {'占压缩后':>9s} {'条目':>6s}")
    for g, (u, c, n) in sorted(groups.items(), key=lambda kv: -kv[1][1]):
        print(f"{g:30s} {u:>15,} {c:>15,} {c / tot_c * 100:>8.2f}% {n:>6}")
    l3 = sum(r[1] for g, r in groups.items() if g.startswith("L3"))
    l1 = sum(r[1] for g, r in groups.items() if g.startswith("L1"))
    print(f"→ L3(数据+内核)= {l3:,} B = {l3 / tot_c * 100:.1f}%   "
          f"L1(我们自己的)= {l1:,} B = {l1 / tot_c * 100:.1f}%")
    print(f"→ 若把 L3 从更新流量里摘出去,这一件会从 {tot_c:,} B 降到 {tot_c - l3:,} B"
          f"({(tot_c - l3) / 1073741824:.2f} GiB)")


def measure_pyz(exe: Path) -> None:
    """从 exe 里量"我们自己的代码到底多大"——它不在磁盘上,在 PYZ 归档里。

    为什么单独量这个:整个增量论证的关键数是"一轮真实变化 ≈ 多少字节",而这个数
    **看目录看不出来**(源码在仓库里,包里没有 .py)。上一轮就是这么推的,所以这次用
    PyInstaller 自己的读取器把它抠出来。
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except Exception as e:                       # 构建环境之外跑也要能出话
        print(f"✗ 读不了归档(缺 PyInstaller?):{type(e).__name__}: {e}")
        return
    car = CArchiveReader(str(exe))
    pyz_name = next((n for n in car.toc if n.lower().endswith(".pyz")), None)
    if pyz_name is None:
        print(f"✗ {exe.name} 的 CArchive 里没有 PYZ,条目:{list(car.toc)[:8]}")
        return
    pyz = car.open_embedded_archive(pyz_name)
    toc = pyz.toc
    mine = {k: v for k, v in toc.items() if k == "minicode" or k.startswith("minicode.")}
    allc = sum(v[2] for v in toc.values())
    myc = sum(v[2] for v in mine.values())
    print(f"\n=== {exe.name} 里的 PYZ ===")
    print(f"模块总数 {len(toc)},其中 minicode.* {len(mine)} 个")
    print(f"  我们的代码(压缩后)  {myc:>12,} B = {myc / 1048576:.2f} MiB")
    print(f"  第三方纯 Python     {allc - myc:>12,} B = {(allc - myc) / 1048576:.2f} MiB")
    print(f"  它所在的可替换单元   {exe.stat().st_size:>12,} B = "
          f"{exe.stat().st_size / 1048576:.2f} MiB  ← ⚠ 改一行就整块重下的那个整体")
    print(f"  ⇒ 我们的代码只占这个单元的 {myc / exe.stat().st_size * 100:.1f}%;"
          f"这就是『manifest 逐件替换』的下限为什么不是 2 MB 而是几十 MB")


def main() -> int:
    ap = argparse.ArgumentParser(description="量包内容量账(只读)")
    ap.add_argument("--zip", default=str(ROOT / "dist" / "引路学长-0.9.177-win64.zip"))
    ap.add_argument("--exe", default=str(ROOT / "dist" / "引路学长" / "引路学长-CLI.exe"))
    a = ap.parse_args()
    zp, ep = Path(a.zip), Path(a.exe)
    if not zp.exists():
        print(f"✗ 找不到 zip:{zp.name}(先出包,或用 --zip 指一份)")
    else:
        measure_zip(zp)
    if not ep.exists():
        print(f"✗ 找不到 exe:{ep.name}(同上)")
    else:
        measure_pyz(ep)
    print("\n注:这只脚本一个字节都不写 —— 它量的是**已经打好的包**,而包的形状由"
          " `scripts/build_exe.py` 决定;要改体积请改那边,别改这里。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
