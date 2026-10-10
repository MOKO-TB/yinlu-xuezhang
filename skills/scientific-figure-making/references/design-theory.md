# 家族风格的来龙去脉

这份口径抄自 [figures4papers](https://github.com/ChenLiu-1996/figures4papers)(ChenLiu-1996)
的 `scientific-figure-making` 技能 —— 那套脚本发在 Nature Machine Intelligence、ICML、
NeurIPS、ECCV 等场子上。这里记的是**能在这个仓库里复现**的那部分,以及一处刻意的偏离。

## 一处刻意的偏离:不用 matplotlib

上游技能是 matplotlib 的。本项目 `packaging/gcode.spec` 的 excludes 明确排掉
`matplotlib` 与 `pandas`(体积与启动时间),`minicode/charts.py` 也是因此手写 SVG。
所以这份技能的内核 `figure_kit.py` 用**标准库**画 SVG,PNG 那一步才用 Pillow
(项目本来就依赖它)。要 PNG 而没装 Pillow / 找不到中文字体时,只落 SVG 并说明原因。

因此:凡是上游技能里靠 matplotlib 才有的能力(雷达图、3D、`usetex`、任意 `cmap`),
这套内核**没有**。要那些图,先直说做不了,再问同学要不要换工具。

## 1. 排版与字体

- 主字体走无衬线中日韩栈:`Microsoft YaHei, PingFang SC, Noto Sans CJK SC, Arial, sans-serif`。
  上游偏 Helvetica;中文图里雅黑/PingFang 的可读性更好,这里换成中日韩优先的栈。
- 字号分两档:**大对比柱 24,紧凑子图 15~16**。线宽同族:大柱 3,紧凑 2。
- 去顶右边框(上游 `axes.spines.top/right = False`)。本内核的 `_axis_frame` 只画左、下两条轴线。

## 2. 导出

- **矢量优先**:SVG(可编辑文本、能进 LaTeX / Word / PPT)。PNG 只作预览。
- `dpi=300` 是标准,密集柱状图到 600。本内核的 `save(scale=...)` 控制 PNG 的像素倍率。
- 文件名稳定、放 `figures/` 目录,便于脚本重跑覆盖。

## 3. 配色

一族语义色,不引入无关调色板:

| 角色 | 色值 | 用在哪 |
|---|---|---|
| 主方法 / 本方案 | `#0F4D92` / `#3775BA` | 要突出的那条 |
| 增益 / 正向 | `#DDF3DE` `#AADCA9` `#8BCF8B` | 改进项、绿档 |
| 对照 / 基线 | `#F6CFCB` `#E9A6A1` `#B64342` | 对照组、负向 |
| 中性 | `#CFCECE` | 背景类别、未涉及 |
| 高亮 | `#FFD700` | **全场只许一处** |
| 点缀 | `#42949E` `#9A4D8E` | 第三、第四条系列 |

## 4. 版式

- **超宽画布**:3~4 个指标并排时,宽:高 ≈ 3~4 : 1,别让柱子和标签挤成竖排。
- **图例不压数据**:放图外一行,或单独占一格。本内核的 `legend` 画在图外。
- **横条优先于竖柱**的场景:类别多、名字长,或想让人一眼看出"谁最高"。
- **热力图用离散色阶**:能力矩阵 / 覆盖度这类本来就只有几档的数据,别用连续色。
  本内核给的是 `LEVEL_COLORS`(3=完全支持 / 2=部分受限 / 1=未核实 / 0=明确没有)。

## 5. 复现清单

1. 去顶右边框、图例放图外、坐标范围按数据收紧;
2. 语义色族,主方法用主蓝,高亮最多一处;
3. 矢量优先,PNG 只作预览;
4. 图上每个数字都要能追到 `source=`;
5. 缺数不补,画不出来就直说。
