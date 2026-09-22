---
name: question-bank
description: 把同学自己手上那份题目(错题/老师发的练习/他自己抄的往年题)整理成规范题库,导出 Anki TSV / GIFT / Aiken 三种可导入格式。当用户说"把这些题做成卡片""导入 Anki""导成 GIFT""整理错题本"时使用。边界:只整理他给的内容,不去抓别人的真题
---

# 什么时候用

他手上有一堆散着的题(Word 里的、PDF 里的、拍照贴进来的、聊天里一段一段
抄的),想变成能刷的题库:Anki 卡片、导入 Moodle/学习通格式的 GIFT、
或只有单选的 Aiken。

# 硬口径(先说这一条)

- **只处理他给的那份题**。他没给题、却问"帮我把某某大学某某课十年真题扒下来"
  ——**做不了,也不做**:那些卷子多数是学校或老师的材料,成包抓取和传播不是
  "资源共享";而且抓回来的脏题(缺答案、OCR 串行)会毁掉整个题库。
  能做的替代:他把手上的卷子贴进来/上传,学长负责把**那一份**规范化。
- **绝不替他补答案**。题目没有答案就是没有答案,填进导出文件会静默变成
  "他背下了一道错的题"。缺答案/缺选项的题在预览里列出来,让他自己补或删。
- 学校题库与老师试卷的版权不在他手里:自用复习可以,**别打包外传、别上架卖**。

# 先规范化:一份内部记录,三种输出

不管他给的是什么形态,先拍平成同一份记录(这是本轮唯一的事实源,
后面所有导出都从它生成 —— 手改导出文件必然出现"Anki 改了 GIFT 没改"):

```python
# -*- coding: utf-8 -*-
# 一条题目 = 一个 dict;全部字段都是字符串(或 None)
Q = {
    "id": "001",                # 顺序号,别用题号(题号会跳)
    "type": "single",           # single | multi | judge | blank | short
    "stem": "下列哪个不是操作系统的基本功能?",
    "options": {"A": "进程管理", "B": "内存管理", "C": "编译优化", "D": "文件管理"},
    "answer": "C",              # 多选用 "AC";判断题用 True/False;填空用 "答案1|||答案2"
    "explain": "编译优化属于编译原理范畴。",   # 可以是【待补】
    "tags": ["操作系统", "第2章", "错题"],
    "wrong_on": "2026-09-20",   # 错题本日期,没有就留 None
}
```

拍平时的固定动作:全半角标点统一(题干里的 `（）` 保留,选项分隔符归一)、
去掉题号前缀(`1.` `2、` `第3题`)、把" A. B. C. D." 从题干里切回选项、
选择题题干里已经出现答案的(「下列说法正确的是( C )」)先问他是漏删还是
答案就在题干里。

# 导出前必须跑校验(这是本轮最容易出问题的一步)

```python
# -*- coding: utf-8 -*-
def check(qs):
    """返回问题列表(空 = 可以导出)。宁可现在报,不要导进去才发现错位。"""
    bad = []
    for i, q in enumerate(qs, 1):
        t, ans, opt = q.get("type"), (q.get("answer") or ""), q.get("options") or {}
        if not (q.get("stem") or "").strip():
            bad.append(f"第 {i} 题:题干为空")
        if t in ("single", "multi"):
            if len(opt) < 2:
                bad.append(f"第 {i} 题:{t} 只有 {len(opt)} 个选项")
            letters = sorted(opt)
            if letters != [chr(c) for c in range(ord("A"), ord("A") + len(letters))]:
                bad.append(f"第 {i} 题:选项字母不连续({letters}),Aiken 一定会错位")
            if not ans:
                bad.append(f"第 {i} 题:没给答案")
            elif set(ans) - set(letters):
                bad.append(f"第 {i} 题:答案 {ans} 不在选项里")
            if t == "single" and len(ans) > 1:
                bad.append(f"第 {i} 题:单选却给了多个答案 {ans}")
            if t == "multi" and len(ans) < 2:
                bad.append(f"第 {i} 题:多选却只有一个答案 {ans}")
        elif t == "judge" and ans not in ("True", "False"):
            bad.append(f"第 {i} 题:判断题答案要写 True/False,现在是 {ans!r}")
        elif t in ("blank", "short") and not ans:
            bad.append(f"第 {i} 题:没给参考答案")
    return bad
```

有 bad 就先报给他看、问要补还是要删,**补不了的那几条从这次导出里剔掉**并
说明剔了哪几条。

# 三种格式(字段位置不能自己发明)

## 1) Anki TSV —— 刷卡片用这个

```python
import io

def anki_tsv(qs, deck="我的题库", notetype="Basic"):
    out = io.StringIO()
    out.write(f"#separator:tab\n#html:true\n#notetype:{one_line(notetype)}"
              f"\n#deck:{one_line(deck)}\n#tags column:3\n")
    for q in qs:
        front = esc(q["stem"])
        # 三个来源都要过 esc:漏掉解析那一处,一条记录就会被解析里的换行劈成两行,
        # 从这一题往后**全部串行**(Anki 不报错,只是错位)。
        back = "<br>".join(x for x in (
            opts_html(q), "答案:" + esc(q.get("answer") or ""),
            esc(q.get("explain") or "")) if x)
        tags = " ".join(t for t in (one_line(t).replace("<br>", " ").strip()
                                    for t in q.get("tags") or []) if t)
        out.write("\t".join((front, back, tags)) + "\n")
    return out.getvalue()

def one_line(s):
    """Tab 是字段分隔符、换行是行分隔符 —— 字段里出现任一个就把后面全挤歪。"""
    return (s or "").replace("\t", " ").replace("\r", " ").replace("\n", "<br>")

def esc(s):
    return one_line(s)

def opts_html(q):
    return "<br>".join(f"{k}. {esc(v)}" for k, v in sorted((q.get("options") or {}).items()))
```

写盘 `open("bank.txt", "w", encoding="utf-8", newline="\n")`:**UTF-8、不要
BOM**(BOM 会混进 `#separator` 那一行,Anki 就认不出分隔符了)、**换行只用
`\n`**(Windows 的 `\r\n` 会让每条记录多一个空行)。有图的题:`#html:true` 时
在背面写 `<img src="q012.png">`,并把图片文件和 .txt 放同一个文件夹再一起导入。
**导出后自己数一遍行数**:记录数应该等于题目数(多了就是哪里还有漏网的换行)。

## 2) GIFT —— 导进 Moodle/学习通一类平台

```
::操作系统·进程::下列哪个不是操作系统的基本功能?{~进程管理 ~内存管理 ~文件管理 =编译优化}
::判断::栈是先进先出的数据结构{FALSE}
::填空::HTTP 默认端口是 {1:80###1:8080}
```

- 单选:`{~错 ~错 =对}`;**多选/判断题各平台实现不一致**(同一份 GIFT 在
  Moodle 能识别,在学习通可能整题报错)。先只导 3 题试一遍再导全量,别一次
  灌 500 题然后回头看错误报告。
- 要转义的字:`::` `{` `}` `=` `~` `#` `$` `<` `>` 和换行(换行写成 `\n`)。
  题干里有"价格是 100~200 元"这种,`~` 不当选项符时要 `\~`。
- 图片题:GIFT 用 `@@q012@@`,并把图片打包成 zip 一起导。
- 保存 `.gift`,UTF-8。

## 3) Aiken —— 最严格,只有单选

```
Q:下列哪个不是操作系统的基本功能?
A:进程管理
B:内存管理
C:编译优化
D:文件管理
ANSWER: C
```

四道硬规矩:每行一条、**题与题之间恰好一个空行**、`ANSWER:` 后只能是一个
大写字母(多选题在这格式里根本无法表达)、题干不能换行。所以 Aiken 只导
`type == "single"` 且选项为 A-D 的那部分,剩下的**如实告诉他导不进去**,
问要不要改用 GIFT。

# 汇报口径

一次导出后必须说清这几句(顺序别变):

1. 一共多少题、各题型几条(「单选 38 · 判断 6 · 多选 4 · 填空 2」)。
2. 剔掉/待补的哪几条,原因是什么。
3. 产物文件名与格式,以及**导入端要选的那一项**(Anki:分隔符 Tab、允许 HTML;
   Moodle:格式 GIFT、导入到哪个题库)。
4. 提醒他**先小批量导一次**(5-10 题)在 App 里看过一遍,再导全量 ——
   错位、答案串行这类问题在小批量里一眼能看见,在 500 题里要人工检查两小时。

# 坑

- **答案错位比缺答案更危险**:错位是"他背下了一个错误答案",而且不会报错。
  校验函数里的「答案字母必须在选项里」+「选项必须 A 起连续」就是为它写的。
- 重复题干(同一道题抄了两遍)要问:是删掉一条,还是答案不同(那就是题目
  被抄错了)。别静默去重。
- 判断题混进单选导出、填空题混进 Aiken,都会得到"题号对不上"的导入报告。
- PDF 里的双栏题面 `extract_text` 常按行横着串(见 pdf 技能):左右栏串行时
  整题重打,不要将错就错地切。
- 中文标点里 `A．`(全角点)和 `A.` 混用很常见,切选项时两种都要认。
- 别把"解析"栏留成 `nan`/`None` 字符串 —— 那是从 DataFrame 里取空值漏出来的,
  在卡片背面特别显眼。
