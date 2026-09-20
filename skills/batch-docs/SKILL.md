---
name: batch-docs
description: 批量生成个人化文档:从名单 Excel 每人生成一份成绩条/获奖证书/参赛证明/录用通知(docx 或 pdf),一条循环搞定全班
---

# 什么时候用

同一份模板要发给很多人、只有名字和信息不同:班级成绩条、奖助学金获奖证书、
竞赛参赛证明、活动录用/入选通知、宿舍检查通知单。名单在 Excel 里
(配合 xlsx 技能读取)。

# 工作流

1. 让同学把**名单 Excel** 和(如有)**Word/PDF 模板**发进来;没有模板就
   先写一份模板给他确认。
2. **write_file 写脚本**(openpyxl 读名单 + python-docx 生成)→
   **py_script 运行** → 报告成功份数与产物文件夹。
3. 产物统一放 `批量输出/` 文件夹,文件名带姓名(如 `成绩条-张三.docx`);
   需要一个 PDF 时生成后用 pdf 技能的 Edge 打印逐份转。

# 模板(读名单 → 逐人生成)

```python
from openpyxl import load_workbook
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn
from pathlib import Path

rows = list(load_workbook("名单.xlsx", data_only=True).active
            .iter_rows(values_only=True))
header, people = rows[0], [r for r in rows[1:] if r and r[0] is not None]
col = {name: i for i, name in enumerate(header)}     # 表头:学号 姓名 高数 英语 …

out = Path("批量输出"); out.mkdir(exist_ok=True)
for p in people:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.size = Pt(12); style.font.name = "宋体"
    style._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    def line(text, bold=False, center=False, size=12):
        para = doc.add_paragraph()
        if center: para.alignment = 1
        run = para.add_run(text)
        run.font.bold = bold; run.font.size = Pt(size)
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    line("2025-2026 学年第一学期个人成绩条", bold=True, center=True, size=15)
    line(f"姓名:{p[col['姓名']]}    学号:{p[col['学号']]}")
    for course in ["高数", "英语"]:
        line(f"{course}:{p[col[course]]}")
    line("如有疑问,contact 班长或辅导员核对。", size=10)
    doc.save(str(out / f"成绩条-{p[col['姓名']]}.docx"))
print("生成", len(people), "份 -> 批量输出/")
```

# 规则与坑

- **列名以名单表头为准**(上面 col 映射),脚本前先 print 表头核对一遍,
  列名对不上要停下来问,不要按猜的顺序取数。
- 名单里**缺失值**(某人是 None/空串):该栏写「/」,并在汇报时说明哪几行缺;
  绝不替他填一个数字。
- 文件名只用姓名+类型;同名人名加学号后四位区分。
- 数据是真实成绩/个人信息:提醒同学这份打包件只发本人或按需脱敏;
  生成完把名单 Excel 留在原处,不要把它也塞进批量输出。
- 份数很大(几百份)时 print 进度;中途报错先看是第几条炸的,改完从断点续跑
  (脚本里 `if (out / f"成绩条-{name}.docx").exists(): continue`)。
