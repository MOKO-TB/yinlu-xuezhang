---
name: ics-calendar
description: 把课表/考试安排/活动日程导出成 .ics 日历文件(纯标准库生成),可导入手机系统日历,配合 schedule-helper 使用
---

# 什么时候用

> **v0.9.58 起优先用内置 `export_ics` 工具**(一键覆盖课表+待办+校历,UID 稳定
> 重复导入不堆重)。本技能留给工具覆盖不了的定制场景:只要某几门课、
> 加自己的活动、或用户给了课表之外的日程。

同学想要「把课表/考试安排放进手机日历,到点提醒」——生成一个 .ics 文件
发给他的手机(微信传过去 → 用「日历」App 打开 → 全部导入)。

# 工作流

1. 先拿到日程数据:课表(配合 schedule-helper 解析)、考试时间表、活动安排,
   或校历工具查到的固定日期。**每条事件必须有确切日期**,只有"第 X 周"的
   先按 schedule-helper 的规则换算成具体日期,换算不了就如实说明。
2. **write_file 写脚本**(纯标准库,无第三方依赖)→ **py_script 运行** →
   报告文件名与事件条数。

# 模板

```python
from datetime import datetime, timedelta

def ics_escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,") \
               .replace("\n", "\\n")

def make_event(title, start_dt, end_dt, remind_minutes=30, note=""):
    """start_dt/end_dt: datetime(无时区,本地时间)"""
    fmt = "%Y%m%dT%H%M%S"
    lines = ["BEGIN:VEVENT",
             f"UID:yinluxs-{start_dt.strftime(fmt)}-{abs(hash(title))}@gzasc",
             f"DTSTART:{start_dt.strftime(fmt)}",
             f"DTEND:{end_dt.strftime(fmt)}",
             f"SUMMARY:{ics_escape(title)}",
             f"BEGIN:VALARM", f"TRIGGER:-PT{remind_minutes}M",
             "ACTION:DISPLAY", f"DESCRIPTION:{ics_escape(note or title)}",
             "END:VALARM", "END:VEVENT"]
    return "\n".join(lines)

events = [
    make_event("高等数学(weekly)", datetime(2026, 9, 21, 8, 0),
               datetime(2026, 9, 21, 9, 40)),
]
# 周课用「每周重复」:RRULE 一条就够,别循环生成 16 份
weekly = make_event("大学英语", datetime(2026, 9, 22, 10, 0),
                    datetime(2026, 9, 22, 11, 40))
weekly = weekly.replace("DTSTART", "RRULE:FREQ=WEEKLY;COUNT=16;INTERVAL=1\nDTSTART", 1)

calendar = ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//yinluxs//CN\n"
            + "\n".join(events) + "\n" + weekly + "\nEND:VCALENDAR")
open("我的课表.ics", "w", encoding="utf-8").write(calendar)
print("已生成: 我的课表.ics,", len(events) + 1, "条规则")
```

# 规则与坑

- **重复日程用 RRULE**(FREQ=WEEKLY;COUNT=周数;单双周用
  `INTERVAL=2` + 正确的首周日期),不要循环生成几十条——手机日历里删起来是灾难。
- 每条事件**必须唯一 UID**(模板里 hash 已保证),重复 UID 会被手机合并。
- 上课提醒建议 `remind_minutes=20`(提前 20 分钟),考试用 60。
- 时间是本地时间(无 Z 后缀);结束时间按节次算,学校作息钟点没有官方
  数据时用同学给的,他没有就按常见「8:00-9:40」并**明说这是默认值可改**。
- 导入路径写清楚给同学:把 .ics 发到手机 → 用「日历/Calendar」打开 →
  选目标账户 → 导入;iPhone 微信里直接点开即可。

# 口径

- 考试时间以教务处最新通知为准,脚本注释里写上数据来源与生成日期;
- 同学给的不是完整课表时,只导出他确认的部分,缺的说明缺什么。
