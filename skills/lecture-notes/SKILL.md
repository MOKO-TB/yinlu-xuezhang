---
name: lecture-notes
description: 把课件(.pptx / .pdf)抽成能背的结构化笔记:按章拆点、标页码锚点、列定义/公式/例题/易错点,并生成考前清单。当用户上传课件、说"帮我整理这一章的笔记""这 PPT 太多页"时使用。边界:扫描版 PDF 如实说做不了,不猜内容
libs: pypdf, pptx
---

# 什么时候用

老师一节课 60 页 PPT、或一本 40 页 PDF 讲义,他要一份能复习的笔记;
也可能是期中/期末前"把第 3-6 章合到一张纸上"。

# 口径(决定这份笔记能不能背)

- **笔记内容来自课件**。学长补的解释、举例、记忆法**必须标出来**
  「(补充,非课件原文)」,并且只补课件里已出现的概念 —— 把课外内容混进
  笔记,他考前背的就是考试不考的东西。
- **不编考点**。他没给考纲/老师圈重点时,只说"按页码密度排的重要段落",
  不说"这一定会考"。给了考纲就按考纲的措辞对齐(他说"只考第三章的例题",
  就别把第一章铺满)。
- **抽不到文字就直说**(下面第一步),绝不靠页码和标题脑补出一章内容。
- 页码锚点一律保留(`P12` = 原课件第 12 页),他复习时要能翻回原文核对。

# 第一步:先探能不能抽(30 秒,决定后面走哪条路)

```python
# -*- coding: utf-8 -*-
from pathlib import Path
from pypdf import PdfReader
from pptx import Presentation

def probe(p):
    p = Path(p)
    if p.suffix.lower() == ".pdf":
        r = PdfReader(str(p))
        per = [(i + 1, len((r.pages[i].extract_text() or "").strip()))
               for i in range(len(r.pages))]
        thin = [i for i, n in per if n < 40]
        return {"kind": "pdf", "pages": len(r.pages),
                "chars": sum(n for _, n in per),
                "thin_pages": len(thin), "ratio": len(thin) / max(len(per), 1)}
    if p.suffix.lower() == ".pptx":
        pr = Presentation(str(p))
        return {"kind": "pptx", "pages": len(pr.slides),
                "chars": sum(len(sh.text_frame.text) for s in pr.slides
                             for sh in s.shapes if sh.has_text_frame)}
    return {"kind": "?", "pages": 0, "chars": 0}
```

判定与说法:

- `chars/pages >= 40` → 文字型,正常走下面的流程。
- **PDF 大半页抽不出字(`ratio > 0.5`、每页几十字)→ 这是扫描版或纯图片课件**。
  直说:"这份 PDF 每一页是一整张图片,我抽不到文字,所以做不了笔记。"
  然后给三条他能走的出路:①问老师/同学要**文字版原件或课件源文件**;
  ②他自己用系统自带的图片文字识别(Windows 的「截图工具」现在能提取文字,
  手机相册长按也行)把这几页抠出来贴给我;③他口述/贴教材原文,学长负责排版
  成笔记结构。**别**答"我大致按章节名给你写一版" —— 那是一篇看着像笔记的
  虚构内容,背了最误事。
- PPTX 抽出来字极少(每页几字)但页数不少 → 多半是**整页截图做的 PPT**,
  同样按"抽不到文字"处理。

# 第二步:抽取(顺序 ≠ 插入顺序)

```python
# -*- coding: utf-8 -*-
def slide_texts(prs):
    """返回 [(页码, [(y, x, 文本)])]:PPT 里文本框的添加顺序经常不等于阅读
    顺序(先放标题框再批量放正文),所以要按位置排一次。"""
    out = []
    for i, s in enumerate(prs.slides, 1):
        boxes = []
        for sh in s.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                boxes.append((round((sh.top or 0) / 914400, 1),
                              round((sh.left or 0) / 914400, 1),
                              sh.text_frame.text.strip()))
            elif sh.shape_type == 13:          # 图片:标出来,他知道这页有图
                boxes.append((9999, 0, "【此处有一张图片,内容未抽到】"))
        out.append((i, sorted(boxes)))
    return out

def notes_of(slide):
    """演讲者备注常写着老师真正想强调的话,别丢。"""
    try:
        return slide.notes_slide.notes_text_frame.text.strip()
    except Exception:
        return ""

def pdf_pages(path):
    r = PdfReader(str(path))
    return [(i + 1, (r.pages[i].extract_text() or "").strip())
            for i in range(len(r.pages))]
```

抽不到东西的地方要**如实留标记**,不要空着让他以为课件没写:
`【此处有一张图片,内容未抽到】`、`【SmartArt/图表内文字未抽到】`、
`【公式为图片,未能提取】`。公式和电路图正是最要命的部分 —— 提醒他这几处
必须自己回原文看。

# 第三步:成结构

按课件的天然分段切(节标题页、`第X章`、章节过渡页、老师自己写的目录页),
不要按学长的逻辑重排 —— 他上课听的顺序就是课件顺序,重排会让页码对不上。

```markdown
# 第 3 章 进程的调度(P12–P31)

## 3.1 调度队列模型(P12–P15)
- **定义**:挂起态 = 内存不足时把进程换出到外存(P14)
- 五状态转换图 → P15 是图片,**必须自己看图**:就绪↔运行只由调度/时间片触发,
  阻塞只能由 I/O 请求发起(P15)
- ⚠ 易错:「挂起」不等于「阻塞」,前者在外存、后者在内存等着 I/O(补充,非课件原文)

## 3.2 调度算法(P16–P28)
- 公式:周转时间 = 完成时间 − 到达时间;带权周转 = 周转 / 服务时间(P17)
- 例题 P19(FCFS vs SJF 手算三笔):步骤照课件列,别只写答案
- 结论:SJF 平均等待最短,但会饿死长作业;抢占式最短剩余时间优先是其加强版(P22)

## 背这些(考前一页纸)
| 概念 | 一句话 | 页码 |
|---|---|---|
| FCFS | 先到先服务,非抢占, convoy 效应 | P18 |
```

规则:每章末尾一段「本章老师反复讲的三句」(从备注页与重复出现的句子统计,
没有就写"课件里没有明显重复强调点",**不硬凑**);全章超过 60 页时先只出
目录 + 每章 3 条要点,问他要哪几章展开(一次铺完 60 页的"笔记"他没机会看)。

# 第四步:交付形态

- 默认 `.md`(他要看要看的是能改的文字);要交作业/打印再走 docx(见 docx 技能,
  中文字体设 `eastAsia`)。
- 顺手可选:把「背这些」那几张表导成 Anki 卡片 —— 用 question-bank 技能的
  那份口径(UTF-8、只用 `\n`、字段里不许有 Tab)。
- 报告说清:处理了多少页、有几页是图片没抽到、哪几章展开了、产物文件名。

# 坑

- **加密 PDF**:`PdfReader` 抛 `FileNotDecryptedError` 或读出来 0 字符 →
  说明有密码,直问他要密码或让他在浏览器里打印成无密码 PDF;不要"我读不到,
  你可能没权限"这种含糊话。
- 页码三套编号混用:PDF 物理页 / 课件右下角写的页码 / PPT 幻灯片序号,差 1 到
  差 5 都常见(有封面页和过渡页)。锚点用**物理页**,并在开头一句写明
  「P 后面的数字是文件第几页,不是角上那个页码」。
- 表格:`extract_text` 会把表格拉成一行长串,列对不上。看到明显的表就改成
  「按行还原表格」,或告诉他"这张表我用 markdown 重画了,原表以课件为准"。
- 课件里的动画是**逐步显示**的:同一页里后出现的正文和先出现的可能是同一个
  要点的一体两面,合并时别丢。
- 老师贴的是上一届的课件、内容和他课上说的不一样 —— 这种事只有他知道,
  所以在笔记开头留一行「与课堂口径不一致之处(自己补)」的空表,提醒他填。
