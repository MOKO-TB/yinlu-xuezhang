---
name: docx
description: 生成或读取/修改 Word(.docx):申请书、通知、活动策划、实践报告等,用内置 Python 运行时跑 python-docx
libs: docx
---

# 什么时候用

同学要交 Word 材料:入党申请书/思想汇报、奖助学金申请表、活动策划书与总结、
社会实践报告、请假条;或者给他一个 .docx 要**读内容/统计/改格式**。

# 工作流

1. 先确认用途与格式要求(标题字号、是否要表格、落款)。学校模板有明确格式时
   让同学把要求原样贴出来,按要求设。
2. **write_file 写脚本**(python-docx)→ **py_script 运行** → 检查退出码 0 →
   报告输出文件名。读文档则 print 提取的段落/表格内容。
3. 正文内容按用户提供的信息写;没给的事实(姓名、学号、日期)用「某某」
   「XXXX 年 X 月 X 日」占位,让同学自己填,**不要编造**。

# 从零生成(常见公文格式)

```python
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

doc = Document()
# 默认样式:宋体小四(中文字体必须设 eastAsia,只设 name 对中文无效)
style = doc.styles["Normal"]
style.font.name = "Times New Roman"
style.font.size = Pt(12)
style._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

def title(text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    run.font.size = Pt(16); run.font.bold = True
    run.font.name = "黑体"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")

def para(text, indent=True):
    p = doc.add_paragraph()
    if indent:
        p.paragraph_format.first_line_indent = Pt(24)   # 首行缩进两字符
    p.paragraph_format.line_spacing = 1.5
    p.add_run(text)

title("社会实践报告")
para("暑假期间,我参加了……(正文,每段调一次 para)")
doc.save("社会实践报告.docx")
print("已生成: 社会实践报告.docx")
```

# 表格(申请表/登记表)

```python
table = doc.add_table(rows=3, cols=2)
table.style = "Table Grid"           # 带边框,交材料必备
table.cell(0, 0).text = "姓名"; table.cell(0, 1).text = "某某"
```

# 读取已有文档

```python
from docx import Document
doc = Document("通知.docx")
for i, p in enumerate(doc.paragraphs):
    if p.text.strip():
        print(i, p.style.name, "|", p.text[:80])
for t in doc.tables:
    for row in t.rows:
        print(" | ".join(c.text for c in row.cells))
```

# 坑

- **中文字体**:必须 `rFonts.set(qn("w:eastAsia"), "宋体")`,只设
  `font.name` 对中文无效(英文部分才是 name 管的)。
- `.doc` 老格式打不开(python-docx 只认 .docx);让同学先另存为 .docx。
- 修改已有文档同理:逐 run 替换文本,别整段重写(丢格式)。
- 页边距:`doc.sections[0].top_margin = Cm(2.54)`;学校要求别的边距按要求的来。

# 口径

- 入党申请书、奖助学金申请这类**正式材料**,写完提醒同学:内容要按自己真实
  情况改写并核对格式要求,不要原样上交。
- 不要在材料里替用户虚构经历、家庭情况、成绩。
