---
name: pptx
description: 生成或修改 PPT(.pptx):课程展示、社团招新、活动策划、班级汇报等,用内置 Python 运行时跑 python-pptx
libs: pptx
---

# 什么时候用

同学要做 PPT:课程展示/小组汇报、社团招新宣讲、活动策划案、班委竞选、
评奖评优答辩。可以**从零生成**,也可以**修改已有 .pptx**(替换文字、加页)。

# 工作流(三步,脚本必须真跑,不许只给代码)

1. **先问清/确认大纲**:主题、页数、场合(投影还是交作业)。同学没给大纲时
   先列一版 6-10 页的大纲让他确认,再动手。
2. **write_file 写脚本**(python 源码,`# -*- coding: utf-8 -*-`),
   然后 **py_script 运行**(内置环境已带 python-pptx,机器不用装任何东西)。
3. **验证**:脚本里 `print(输出文件路径)`;运行完检查退出码 0,向用户报文件名。
   出错就改脚本重跑,不要把报错丢给用户。

# 最小模板(从零生成)

```python
from pptx import Presentation
from pptx.util import Cm, Pt

prs = Presentation()
prs.slide_width = Cm(33.87)   # 16:9
prs.slide_height = Cm(19.05)
blank = prs.slide_layouts[6]

def add_title_slide(title, subtitle):
    s = prs.slides.add_slide(blank)
    tb = s.shapes.add_textbox(Cm(2.5), Cm(7), Cm(28.9), Cm(4))
    p = tb.text_frame.paragraphs[0]
    p.text = title
    p.font.size = Pt(44); p.font.bold = True
    p.font.name = "微软雅黑"          # 中文字体必须设,否则投影时变宋体
    tb2 = s.shapes.add_textbox(Cm(2.5), Cm(11.5), Cm(28.9), Cm(2.5))
    p2 = tb2.text_frame.paragraphs[0]
    p2.text = subtitle; p2.font.size = Pt(20); p2.font.name = "微软雅黑"

def add_content_slide(title, bullets):
    s = prs.slides.add_slide(blank)
    tb = s.shapes.add_textbox(Cm(2.5), Cm(1.8), Cm(28.9), Cm(2.8))
    p = tb.text_frame.paragraphs[0]
    p.text = title; p.font.size = Pt(32); p.font.bold = True; p.font.name = "微软雅黑"
    body = s.shapes.add_textbox(Cm(2.5), Cm(5.2), Cm(28.9), Cm(12))
    tf = body.text_frame; tf.word_wrap = True
    for i, line in enumerate(bullets):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.text = "• " + line
        para.font.size = Pt(20); para.font.name = "微软雅黑"

add_title_slide("大学英语四级备考经验", "汇报人:某某 · 2026年9月")
add_content_slide("备考时间线", ["第1-4周:核心词汇两轮", "第5-8周:真题精读", "考前两周:整套模考"])
prs.save("课件.pptx")
print("已生成: 课件.pptx,共", len(prs.slides.__iter__.__self__._sldIdLst), "页")
```
(页数直接 `len(prs.slides._sldIdLst)` 或在循环里数,上面 print 可简化。)

# 修改已有 PPT

```python
from pptx import Presentation
prs = Presentation("原来的.pptx")
for slide in prs.slides:
    for shape in slide.shapes:
        if not shape.has_text_frame: continue
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                if "2025" in run.text:
                    run.text = run.text.replace("2025", "2026")  # 保留原格式
prs.save("改好的.pptx")
```
逐 **run** 替换,不要整段重赋值(会丢加粗/颜色)。

# 坑(都踩过)

- **中文乱回退**:每个 run 都要设 `font.name = "微软雅黑"`;标题可用「微软雅黑」加粗,
  正文别用楷体/仿宋的稀少字重。
- 文本框要 `tf.word_wrap = True`,否则一行字冲出画面。
- `Presentation()` 新建默认 4:3,必须改 `slide_width/height` 成 16:9。
- 图片页:`slide.shapes.add_picture("图.png", Cm(2), Cm(3), width=Cm(20))`——
  只给 width 会按比例缩放,别同时给 width 和 height(会拉伸变形)。
- 路径都相对工作区;脚本里 print 每一步,方便从 py_script 输出定位问题。

# 口径

- 内容不要编造:涉及成绩、获奖名次的页,数据来自用户给的清单;没给的用
  "此处填写…"占位,不要替他编数字。
- 同学只要大纲时,先给大纲等确认,不要直接生成 20 页。
