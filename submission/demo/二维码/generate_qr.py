# -*- coding: utf-8 -*-
"""把下载/部署链接生成二维码 PNG,供答辩演示扫码体验。

用法:
    pip install qrcode pillow        # 若未安装
    py -3.12 generate_qr.py "https://你的网盘或部署地址" 二维码.png
"""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    url = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "二维码.png"
    try:
        import qrcode
    except ImportError:
        print("缺少 qrcode:请先 pip install qrcode pillow")
        return 1
    img = qrcode.make(url)
    img.save(out)
    print(f"已生成 {out}  内容: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
