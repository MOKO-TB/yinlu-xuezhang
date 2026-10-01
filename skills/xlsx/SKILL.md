---
name: xlsx
description: 生成或读取/统计 Excel(.xlsx):成绩单、值班表、报名汇总、账目统计,用内置 Python 运行时跑 openpyxl
libs: openpyxl
---

# 什么时候用

班委/社团/个人要 Excel:成绩登记与统计(平均分、排名、不及格名单)、活动
报名汇总、宿舍值班表、班费账目;或者给一个 .xlsx 要**读数、统计、筛选**。

# 工作流

1. 写数据类:确认列名(表头)与行数;统计类:先读文件把结构 print 出来
   (别猜列顺序),再按用户要的口径算。
2. **write_file 写脚本**(openpyxl)→ **py_script 运行** → 检查退出码 0 →
   报告文件名与关键统计结果。
3. 统计结论要在回复里直接给出(不止丢一个文件):如"共 42 人,平均分 81.3,
   3 人不及格"。

# 生成(带格式)

```python
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill

wb = Workbook()
ws = wb.active
ws.title = "成绩单"
headers = ["学号", "姓名", "平时分", "期末分", "总评"]
ws.append(headers)
for c in ws[1]:
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor="3FBDAE")
    c.alignment = Alignment(horizontal="center")
data = [["2025001", "张三", 88, 76, None]]
for row in data:
    ws.append(row)
# 总评 = 平时 40% + 期末 60%(公式,Excel 里自动算)
for r in range(2, ws.max_row + 1):
    ws.cell(r, 5).value = f"=C{r}*0.4+D{r}*0.6"
ws.column_dimensions["A"].width = 12
ws.column_dimensions["B"].width = 10
wb.save("成绩单.xlsx")
print("已生成: 成绩单.xlsx", ws.max_row - 1, "行")
```

# 读取与统计

```python
from openpyxl import load_workbook
wb = load_workbook("报名汇总.xlsx", data_only=True)   # data_only: 公式取缓存值
ws = wb.active
rows = list(ws.iter_rows(values_only=True))
header, body = rows[0], rows[1:]
print("表头:", header)
print("数据行数:", len(body))
# 统计示例:第二列分组计数
from collections import Counter
print(Counter(r[1] for r in body if r[1] is not None))
```

# 坑

- 只认 **.xlsx**;`.xls` 老格式 openpyxl 打不开 → 让同学用 Excel/WPS 另存为 .xlsx。
- `load_workbook(data_only=True)` 取的是**公式缓存值**:文件若从未被 Excel/WPS
  打开保存过,公式单元格读到 None —— 此时提示用户先打开保存一次,或改在
  脚本里自己算。
- 单元格的日期读出来是 `datetime`,打印要 `strftime("%Y-%m-%d")`。
- 大于 3 万行的表逐行循环会慢,先数一下行数再决定口径。
- 排序别覆盖原表:统计结果写到新 sheet(`wb.create_sheet("统计")`)或新文件。

# 口径

- 成绩、名单等是真实数据:只按用户给的算,统计口径(加权?去重?缺考算不算?)
  有歧义时先问一句再算,不要替他决定。
- 涉及他人成绩/个人信息的表,提醒同学别外传。
