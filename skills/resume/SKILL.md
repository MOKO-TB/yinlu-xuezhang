---
name: resume
description: 生成单页中文简历(.docx 或 PDF)并按岗位定向改写:实习/秋招/评奖评优/入党材料用,内置模板、排版规则与技术岗/非技术岗两套改法,内容只来自用户提供
libs: docx
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

- 每段经历写成「动词开头 + 做了什么 + **量化结果**」:"组织 3 场招新宣讲,
  覆盖 400+ 人,报名转化 120 人",不要"负责……工作"这种空话。
- 经历按与目标岗位的相关度排序,不按时间;与用途无关的条目删掉,保单页。
- 技能写具体("Python:能用 pandas 做数据清洗",而非"精通办公软件")。
- 证件照位置留白即可(`姓名`右侧不排图,同学自行插入),不要用代码往
  docx 里塞用户没给的照片。

# 定向:一份简历只投一类岗位

他往往一份简历投遍所有岗。先问清**这一次投什么**(岗位名称 + JD 原文最好),
按目标出对应的那一版;要投两类,就生成两份文件(`张三-后端.docx`
`张三-运营.docx`)。同一份经历在两版里写法不同是正常的,**内容不能变** ——
只能换"哪一条排第一、突出哪个数字、用什么动词",绝不能给一版加经历。

## 技术岗(开发/算法/测试/数据)

- 顺序:**技能与栈 → 项目/开源 → 实习 → 学科成绩与竞赛 → 其他**。
  HR 与工程师都先扫前两栏,学生工作放最后。
- 每条项目写「做了什么技术决策 + 规模 + 结果」:不写"用 Python 写了个爬虫",
  写成"用 requests+SQLite 抓 12 万条数据,失败重试与断点续存,日均 40 分钟跑完"。
  规模、耗时、并发数这类数字他给不出就说"我们回去量一下",**不许编**。
- 技术名词按 JD 的原词写(JD 写「MySQL」就别只写「数据库」),但**只会调 API
  就别写"精通"**——技术面第一个问题就是这里,答不上来整份简历一起作废。
- 有 GitHub/博客/在线 demo 就放链接(短链接也行);没有就别放占位的
  "github.com/xxx"。
- GPA/排名:高于 3.5 或专业前 30% 才写;低了改写成"主修课程:数据结构 92、
  操作系统 90"(用单科撑,不撒"绩点 3.9"这种谎)。

## 非技术岗(运营/产品/行政/人力/市场/教资/公考/银行)

- 顺序:**实习与校园实践 → 组织与活动 → 技能与证书 → 学业**。项目技术栈没人看。
- 把"做了什么"改成**面向的结果与协作**:"负责公众号" → "运营院号,一学期
  38 篇、总阅读 2.4 万,单篇最高 5200,涨粉 900+"。数字要能对上他的后台截图。
- 岗位关键词优先:JD 里出现"文案/数据复盘/跨部门/活动执行/甲方对接",
  就把他相应的那条经历往前挪、并用这些原词写(很多公司先机器筛关键词)。
- 证书该写就写全:四六级分数、计算机等级、教资、驾照(行政/市场岗常要)、
  党员/预备党员(国企与选调看这个)。
- 意向不明时**不要替他选**:问他"这两份谁先看",按看的那个人所在行业出。

## 两份共用的体检(生成前逐条过)

- 有没有与岗位无关又占半页的条目(学生会某部干事投后端 = 删)。
- 时间线能不能连上,有没有解释不了的 6 个月空档(有就提醒他准备一句话,
  而不是把空档抹平)。
- 邮箱是不是 `nickname@`(求职用一个正经邮箱;没有就先注册一个,别用
  QQ 号 9 位数当邮箱名)。
- 一页放不放得下 —— 放不下删最弱的一条,而不是压行距。

# 坑与口径

- 中文字体设 `eastAsia`(见 docx 技能),否则投影/打印变宋体。
- 超过一页就删内容,别缩字号到 9pt 以下(HR 会直接翻页跳过)。
- **绝不虚构**:GPA、获奖、任职一栏与用户说的不一致时按他说的写;他没给的
  数字用「(此处填写)」占位并当面提醒。
