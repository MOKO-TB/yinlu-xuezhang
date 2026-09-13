"""把手绘地图交付包(36 张单栋标注 PNG + INDEX.json)转成应用内前端资源。

用法:
    py -3.12 scripts/build_campus_assets.py                 # 默认从 D:\\yy\\单栋标注_原图尺寸 读取
    py -3.12 scripts/build_campus_assets.py --src <交付目录> --full

产出(相对项目根):
    frontend/campus-index.json          索引副本(前后端唯一真源)
    frontend/img/campus/map.jpg         干净底图(原图直接复制,不改像素)
    frontend/img/campus/1280/NN.webp    单栋标注图,宽 1280,面板/灯箱用(约 4MB)
    frontend/img/campus/full/NN.webp    单栋标注图,原尺寸 2559(--full 才出,约 10MB)

为什么默认不出全尺寸:安装包已经 49MB,面板宽度只有 660px,1280 宽的图在灯箱里
放大 4 倍仍然够看。真要原尺寸母版,加 --full 重跑一次即可(或直接用交付目录里的 PNG)。

文件名用两位编号(NN.webp)而不是中文名:file:// 下 WebView2 加载中文路径虽然多半可行,
但没必要赌编码;索引里有 no→name 的映射,人看编号也能在 INDEX.md 里对上。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - 只影响这个离线脚本
    print("需要 Pillow:py -3.12 -m pip install pillow", file=sys.stderr)
    raise

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = Path(r"D:\yy\单栋标注_原图尺寸")
THUMB_WIDTH = 1280
THUMB_QUALITY = 82
FULL_QUALITY = 80


def build(src: Path, with_full: bool) -> int:
    index_path = src / "INDEX.json"
    if not index_path.exists():
        print(f"找不到索引文件: {index_path}", file=sys.stderr)
        return 2
    index = json.loads(index_path.read_text(encoding="utf-8"))

    out_dir = ROOT / "frontend" / "img" / "campus"
    thumb_dir = out_dir / str(THUMB_WIDTH)
    full_dir = out_dir / "full"
    thumb_dir.mkdir(parents=True, exist_ok=True)

    # 1) 干净底图:交付目录里的原始 jpg,直接复制(不改像素)
    src_map = next((p for p in src.glob("*.jpg")), None)
    if src_map is None:
        print(f"交付目录里没有底图 jpg: {src}", file=sys.stderr)
        return 2
    shutil.copyfile(src_map, out_dir / "map.jpg")

    # 2) 单栋标注图 → webp
    made, missing = 0, []
    for b in index.get("buildings", []):
        png = src / str(b["image"])
        if not png.exists():
            missing.append(b["image"])
            continue
        im = Image.open(png).convert("RGB")
        stem = f"{int(b['no']):02d}"
        small = im.resize((THUMB_WIDTH, round(im.height * THUMB_WIDTH / im.width)),
                          Image.LANCZOS)
        small.save(thumb_dir / f"{stem}.webp", "WEBP", quality=THUMB_QUALITY, method=6)
        if with_full:
            full_dir.mkdir(parents=True, exist_ok=True)
            im.save(full_dir / f"{stem}.webp", "WEBP", quality=FULL_QUALITY, method=6)
        made += 1
    if missing:
        print(f"警告: 缺 {len(missing)} 张标注图 {missing[:3]}…", file=sys.stderr)

    # 3) 索引副本:原样保留交付字段(可追溯到交付物),另加 app_assets 说明应用内路径
    index["app_assets"] = {
        "base_map": "img/campus/map.jpg",
        "building_image_dir": f"img/campus/{THUMB_WIDTH}",
        "building_image_pattern": "{no:02d}.webp",
        "full_image_dir": "img/campus/full" if with_full else "",
        "note": ("由 scripts/build_campus_assets.py 从交付包的单栋标注 PNG 生成;"
                 "坐标一律基于源图 2559×1279。完整尺寸母版见交付目录。"),
    }

    # 交付包的 source_map.file 是生成机的本地绝对路径(带个人目录),只留文件名:
    # 该字段仅供追溯来源,没有任何代码读它,没必要把本机路径随公开仓库分发。
    sm = index.get("source_map")
    if isinstance(sm, dict) and sm.get("file"):
        sm["file"] = Path(str(sm["file"])).name

    (ROOT / "frontend" / "campus-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    size = sum(f.stat().st_size for f in thumb_dir.glob("*.webp"))
    print(f"OK 建筑图 {made} 张 → {thumb_dir}({size / 1024 / 1024:.1f} MB)"
          + (f";全尺寸 → {full_dir}" if with_full else ""))
    print(f"OK 底图 → {out_dir / 'map.jpg'}({(out_dir / 'map.jpg').stat().st_size / 1024:.0f} KB)")
    print("OK 索引 → frontend/campus-index.json")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="生成校园地图前端资源")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="交付目录(含 INDEX.json 与标注 PNG)")
    ap.add_argument("--full", action="store_true", help="同时输出 2559 全尺寸 webp(约 +10MB)")
    args = ap.parse_args()
    return build(args.src, args.full)


if __name__ == "__main__":
    raise SystemExit(main())
