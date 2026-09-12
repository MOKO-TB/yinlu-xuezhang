"""生成「引路学长」应用图标:深色圆角底板 + 青绿描边 + "引" 字 + 命令行光标。

用法: python scripts/make_icon.py  (输出 assets/gcode.ico)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "gcode.ico"

SIZE = 256
DARK = (61, 44, 30, 255)       # 暖调深棕(引路学长文字色)
ORANGE = (249, 115, 22, 255)   # 暖橙主色
CREAM = (253, 246, 236, 255)   # 米白

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",   # 微软雅黑 Bold(中文)
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\segoeuib.ttf",
]


def pick_font(size: int):
    for cand in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(cand, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> None:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 底板与描边(暖橙手账风)
    d.rounded_rectangle([10, 10, 246, 246], radius=56, fill=DARK)
    d.rounded_rectangle([10, 10, 246, 246], radius=56, outline=ORANGE, width=12)

    # 主字 "引"
    d.text((118, 112), "引", font=pick_font(140), fill=CREAM, anchor="mm")

    # 命令行光标 "_"
    d.rounded_rectangle([150, 176, 210, 192], radius=6, fill=ORANGE)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, sizes=[(256, 256), (128, 128), (64, 64), (48, 48),
                         (32, 32), (16, 16)])
    print(f"图标已生成: {OUT}")


if __name__ == "__main__":
    main()
