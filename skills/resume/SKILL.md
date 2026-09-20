---
name: resume
description: 生成单页中文简历(.docx 或 PDF):实习/秋招/评奖评优/入党材料用,内置模板与排版规则,内容只来自用户提供
---

# 什么时候用

同学要一份简历:投实习/秋招春招、评奖评优附件、社团招新骨干申请、入党材料
个人简历页。

# 工作流

1. **先收信息再动手**,缺什么问什么:姓名/联系方式、教育背景(学校·学院·
   专业·入学年月)、目标岗位或用途、经历条目(实习/项目/学生工作,每条
   时间+角色+做了什么+结果)、技能证书、获奖。**简历内容 100% 来自用户**,
   他没说的经历绝不替他编。
2. **write_file 写脚本**(python-docx,排版规则见下)→ **py_script 运行** →
   检查退出码 0 → 报告文件名。
3. 要 PDF 时:docx 生成后用 pdf 技能的 Edge 无头打印路线
   (`msedge --headless --print-to-pdf`),或在脚本里生成 HTML 版再转。

# 排版规则(单页,中文简历通行格式)

```python
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

doc = Document()
sec = doc.sections[0]
sec.top_margin = sec.bottom_margin = Cm(1.4)      # 单页简历边距收窄
sec.left_margin = sec.right_margin = Cm(1.8)

def _cn(run, font="微软雅黑"):
    run.font.name = font
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font)

def name_line(text):
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text); r.font.size = Pt(22); r.font.bold = True; _cn(r)

def section(title_text):
    p = doc.add_paragraph()
    r = p.add_run(title_text); r.font.size = Pt(13); r.font.bold = True
    r.font.color.rgb = RGBColor(0x1F, 0x7A, 0x70); _cn(r)
    p.paragraph_format.space_before = Pt(8)

def item(head, body):
    p = doc.add_paragraph()
    r1 = p.add_run(head + "  "); r1.font.bold = True; r1.font.size = Pt(11); _cn(r1)
    r2 = p.add_run(body); r2.font.size = Pt(11); _cn(r2)

name_line("张三")
item("电话:", "138xxxxxxxx · 邮箱: xxx@xx.com")
section("教育背景")
item("广州应用科技学院 · 计算机科学与技术", "2023.09 – 2027.06(本科)")
section("实习与项目经历")       # 每条:时间+组织+角色 / 一两行成果(量化)
doc.save("张三-简历.docx")
print("已生成: 张三-简历.docx")
```

# 内容规则(决定简历好坏的部分)

- 每段经历写成「动词开头 + 做了什么 + **量化结果**」:」"组织 3 场招新宣讲,
  覆盖 400+ 人,报名转化 120 人",不要"负责……工作"这种空话。
- 经历按与目标岗位的相关度排序,不按时间;与用途无关的条目删掉,保单页。
- 技能写具体("Python:能用 pandas 做数据清洗",而非"精通办公软件")。
- 证件照位置留白即可(`姓名`右侧不排图,同学自行插入),不要用代码往
  docx 里塞用户没给的照片。

# 坑与口径

- 中文字体设 `eastAsia`(见 docx 技能),否则投影/打印变宋体。
- 超过一页就删内容,别缩字号到 9pt 以下(HR 会直接翻页跳过)。
- **绝不虚构**:GPA、获奖、任职一栏与用户说的不一致时按他说的写;他没给的
  数字用「(此处填写)」占位并当面提醒。
