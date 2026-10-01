---
name: photo-print
description: 证件照排版打印页与换底色:Pillow 实现 1寸/2寸多张排到 6寸相纸,纯色底证件照可换蓝/白/红底,内置 Python 运行时直接跑
libs: PIL
---

# 什么时候用

- 报名/考试要交纸质证件照:把一张证件照**裁成标准尺寸**(1寸 25×35mm、
  小二寸 33×48mm、二寸 35×49mm),**排成 6 寸相纸打印版**(一版 8 张 1 寸
  或 4 张 2 寸),拿到照相馆/自助机直接冲印;
- 换底色:报名系统要求蓝底、手头是白底(或反之),**纯色背景**的照片可以换。

# 工作流

1. 让同学把照片发进来(附件会落到 uploads/,脚本里用那个路径)。
2. **write_file 写脚本**(Pillow,内置环境带)→ **py_script 运行** →
   报告产物文件名。
3. 产物:排好版的 `证件照-6寸打印版.jpg`(300 DPI,正好铺满 6 寸)。

# 排版模板(1 寸 ×8 张到 6 寸相纸)

```python
from PIL import Image

SRC = "uploads/证件照.jpg"        # 附件的实际路径
mm = lambda v: int(v * 300 / 25.4)            # 毫米 → 300DPI 像素
PHOTO = (mm(25), mm(35))                      # 1寸:25×35mm
PAPER = (mm(152), mm(102))                    # 6寸:152×102mm
GAP = mm(2)

img = Image.open(SRC).convert("RGB")
img = img.resize(PHOTO, Image.LANCZOS)
cols = (PAPER[0] + GAP) // (PHOTO[0] + GAP)
rows = (PAPER[1] + GAP) // (PHOTO[1] + GAP)
paper = Image.new("RGB", PAPER, "white")
used_h = rows * PHOTO[1] + (rows - 1) * GAP
top = (PAPER[1] - used_h) // 2                # 垂直居中,四周留裁切余量
for r in range(rows):
    for c in range(cols):
        paper.paste(img, (c * (PHOTO[0] + GAP) + 4, top + r * (PHOTO[1] + GAP)))
paper.save("证件照-6寸打印版.jpg", quality=95)
print("已生成: 证件照-6寸打印版.jpg,", cols, "列 x", rows, "行,", cols*rows, "张")
```
2 寸版把 `PHOTO` 换成 `(mm(35), mm(49))`(6 寸纸正好 2×2=4 张)。

# 换底色(仅限纯色背景)

```python
from PIL import Image
import colorsys
img = Image.open(SRC).convert("RGB")
px = img.load()
corner = px[4, 4]                              # 取角落色当背景色
W, H = img.size
for y in range(H):
    for x in range(W):
        r, g, b = px[x, y]
        # 与背景色的距离小于阈值 → 认为是背景,换成目标色
        if abs(r-corner[0]) + abs(g-corner[1]) + abs(b-corner[2]) < 90:
            px[x, y] = (0, 191, 255)           # 蓝底;白底 (255,255,255) 红底 (255,0,0)
img.save("证件照-换蓝底.jpg")
```

# 边界(如实说,不要硬来)

- 换底色只对**纯色/渐变很弱**的背景奏效;人像边缘会有一圈杂色,复杂背景、
  头发丝多的照片效果差,要先说明"机器换底效果有限,要求高就去照相馆重拍"。
- 阈值 90 可按效果调:碎点太多调小,背景残留调大;改完让用户看图确认。
- 不做美颜、不改五官比例 —— 证件照动了就是合规问题。
- 尺寸要求以**报名系统通知**为准,同学说了别的尺寸按他的来,别硬套 1 寸。
