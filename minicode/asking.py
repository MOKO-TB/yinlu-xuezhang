"""「动手前先问一句」的问题与答案口径(v0.9.96)。

要解决的问题:模型在规划中途遇到"选哪条路都行、但代价差很多"的分叉时,以前只有
两条路 —— 自己猜(猜错了整轮白跑),或者把问题抛回聊天区(用户得自己组织语言回答,
而且模型拿到的是又一句话,不是一组确定选项)。别的 agent(Qoder 的 AskUserQuestion、
Codex 动手前那次批准、ZCode 的计划卡)走的是第三条:**弹一张卡,让用户点**。

这个模块只负责两件事,而且刻意不碰任何界面:

1. `normalize()` —— 把模型传来的 questions 拍成**能弹的形状**,坏参数在这里就拒掉,
   一个框都不弹(为了一条坏调用把用户打断是最坏的交易)。规则都写在这个文件里,
   GUI / 终端 / 网页三形态读的是同一份,不各抄一遍。
2. `format_answers()` —— 把用户的选择拍成**进模型上下文的那段话**。这里守着本项目
   最硬的一条口径:**没答就是没答**,不能把"没答"写成"同意",也不能替用户把他
   没选的那一项说成他选的(转述一次,模型后面就当确凿事实用了)。

为什么单独成一个模块而不是塞进 `toolsum.py`:那边管的是"一次工具调用成没成",
这边管的是"一次人机交互的形状与说法",两者都会被 GUI/终端/工具三处消费,
放一起以后谁也说不清哪条该归谁。
"""
from __future__ import annotations

# 一次最多问三题:再多就不是"问一句",是让学生填表(见 §五刻意不做)。
MAX_QUESTIONS = 3
# 每题 2-4 个选项:少于 2 个不构成选择(那是一句确认,走权限框),多于 4 个选不动。
MIN_OPTIONS, MAX_OPTIONS = 2, 4
HEADER_LIMIT = 12          # 卡片角标那行,和选项标题一样按"一眼读完"收
LABEL_LIMIT = 40
NOTE_LIMIT = 120
WHY_LIMIT = 600
FREE_TEXT_LIMIT = 500

# 用户没点选项、改走输入框自由补充时,界面回传里带这个键;
# 三种出口的名字也是口径的一部分:前端按钮、桥、工具结果三处都按它判。
MODE_ANSWER, MODE_RECOMMEND, MODE_UNANSWERED = "answer", "recommend", "unanswered"
RECOMMEND_MARK = "(推荐)"


def _clip(text, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit] + "…"


def _one_question(raw, idx: int) -> tuple[dict | None, str]:
    if not isinstance(raw, dict):
        return None, f"第 {idx + 1} 题不是对象(要的是 {{q, options, ...}})"
    q = _clip(raw.get("q"), 200)
    if not q:
        return None, f"第 {idx + 1} 题没有问题本体 q"
    opts_raw = raw.get("options")
    if not isinstance(opts_raw, list):
        return None, f"第 {idx + 1} 题的 options 必须是数组"
    opts: list[dict] = []
    for o in opts_raw:
        if isinstance(o, str):
            o = {"label": o}
        if not isinstance(o, dict):
            return None, f"第 {idx + 1} 题里有选项既不是对象也不是字符串"
        label = _clip(o.get("label"), LABEL_LIMIT)
        if not label:
            return None, f"第 {idx + 1} 题里有选项没有 label"
        opts.append({"label": label, "note": _clip(o.get("note"), NOTE_LIMIT)})
    if len(opts) < MIN_OPTIONS:
        return None, (f"第 {idx + 1} 题只有 {len(opts)} 个选项:一个选项不构成选择"
                      "(那是一句确认,直接在回复里问就行),至少两个")
    if len(opts) > MAX_OPTIONS:
        return None, (f"第 {idx + 1} 题有 {len(opts)} 个选项,上限 {MAX_OPTIONS}"
                      "(选项太多学生选不动;要合并就并成一题的说明里)")
    if len({o["label"] for o in opts}) != len(opts):
        return None, f"第 {idx + 1} 题有重复的选项 label(界面按 label 回传,重复就分不出)"
    return {"id": idx,
            "header": _clip(raw.get("header"), HEADER_LIMIT),
            "q": q,
            "why": _clip(raw.get("why"), WHY_LIMIT),
            "multi": bool(raw.get("multi")),
            "options": opts}, ""


def normalize(questions) -> tuple[list[dict], str]:
    """返回(可弹的问题清单, 错误说明)。有错误说明时**一题都不弹**。

    推荐项不是结构化字段:它"排在第一项 + label 里写着(推荐)"。界面不替模型画一个
    真没有的徽章,模型也不该指望界面帮它把推荐说圆 —— 推荐得写成一句人话(为什么
    推荐、代价是什么),这正是 `why` 那一栏的作用。
    """
    if isinstance(questions, dict):          # 只问一题时允许直接传对象
        questions = [questions]
    if not isinstance(questions, list) or not questions:
        return [], "questions 必须是非空数组(至少一题)"
    if len(questions) > MAX_QUESTIONS:
        return [], (f"一次最多问 {MAX_QUESTIONS} 题(这次传了 {len(questions)} 题):"
                    "再多就不是问一句,是让人填表;真有多件事要定,先问最关键的那几题")
    out, problems = [], []
    for i, raw in enumerate(questions):
        q, err = _one_question(raw, i)
        if err:
            problems.append(err)
        else:
            out.append(q)
    if problems:
        return [], "问题形状不对,一个框都没弹:" + ";".join(problems)
    return out, ""


def _labels_of(q: dict) -> list[str]:
    return [o["label"] for o in q["options"]]


def format_answers(questions: list[dict], reply: dict | None) -> str:
    """把界面回传拍成进模型上下文的那段话。

    三种出口都要能被区分开,因为它们的下一步动作完全不同:
    - `answer`     用户点了选项(或自己补了一句)→ 照他说的做;
    - `recommend`  用户点了「按推荐继续」→ 照**你列在第一项并标了(推荐)**的那个做,
                   并且知道这是"他懒得逐题选",不是逐题确认过;
    - `unanswered` 没等到回答(停止键 / 关窗 / 非交互环境)→ **不许当成同意**。
    """
    mode = str((reply or {}).get("mode") or MODE_UNANSWERED)
    if mode == MODE_UNANSWERED:
        why = _clip((reply or {}).get("reason"), 80)
        return ("没等到你的回答" + (f"({why})" if why else "") + "。"
                "**这不算同意**:要么按你自己推荐的先做、并在回复里写清"
                "「没等到确认,先按 X 做了」,要么换个时间再问一次;"
                "不许把这一题当成用户选过了往下编。")
    picks = (reply or {}).get("answers")
    if mode == MODE_RECOMMEND:
        lines = []
        for q in questions:
            first = q["options"][0]["label"] if q["options"] else ""
            lines.append(f"第 {q['id'] + 1} 题「{_clip(q['q'], 40)}」→ 按推荐项「{first}」"
                         + ("" if RECOMMEND_MARK in first else "(注意:这一题第一项没标推荐)"))
        return ("用户点了「按推荐继续」,逐题按你标在第一项的推荐走:\n"
                + "\n".join(lines)
                + "\n(他没有逐题点选,所以这等于「方向由你定、他不反对」;"
                  "真出现代价明显超出预期时,该停回来再说一次。)")
    if not isinstance(picks, list):
        return "回答的形状不对(界面没给出选了什么),这一题按未回答处理。"
    by_id = {}
    for item in picks:
        if isinstance(item, dict):
            try:
                by_id[int(item.get("id"))] = item
            except (TypeError, ValueError):
                continue
    lines, seen = [], set()
    for q in questions:
        item = by_id.get(q["id"], {})
        valid = _labels_of(q)
        chosen = item.get("choice")
        if isinstance(chosen, str):
            chosen = [chosen]
        got = [str(c) for c in (chosen or []) if str(c) in valid]
        seen.update(got)
        free = _clip(item.get("free"), FREE_TEXT_LIMIT)
        if q["multi"]:
            head = f"第 {q['id'] + 1} 题「{q['q']}」→ 选了 {'、'.join(got) or '(一个都没选)'}"
        else:
            head = f"第 {q['id'] + 1} 题「{q['q']}」→ {got[0] if got else '(没选)'}"
        lines.append(head + (f";他自己补了一句:{free}" if free else ""))
    stray = [str(c) for item in picks if isinstance(item, dict)
             for c in ([item.get("choice")] if isinstance(item.get("choice"), str)
                       else (item.get("choice") or []))
             if str(c) not in seen]
    out = "用户回答:\n" + "\n".join(lines)
    if stray:
        out += "\n(界面还回了你没列过的项:" + "、".join(stray) + " —— 以用户原话为准,"
        out += "别把它当成某个选项的别名)"
    return out


def ask_user(ctx, questions, note: str = "") -> str:
    """`ask_user` 工具的实现:弹一张卡问用户,把他选的原样带回来。

    三条边界:
    - **坏参数一个框都不弹**(先 `normalize`,不合格就把原因回给模型,不打扰用户);
    - 这个形态没有提问通道时**明说没问出去**,并重申"没问"不等于"同意";
    - 返回值只描述用户做了什么,不替他总结成结论 —— 总结是模型的活,而且要有出处。
    """
    qs, err = normalize(questions)
    if err:
        return f"错误: {err}"
    ask = getattr(getattr(ctx, "ui", None), "ask_user", None)
    if not callable(ask):
        return ("这个界面没有提问通道,这一题**没问出去**"
                "(子代理与网页版都不挂这只工具)。"
                "别把没问当成同意:要么直接在回复里把问题问出来,"
                "要么按你自己推荐的先做并写清「没等到确认」。")
    return format_answers(qs, ask(qs, note=str(note or "")))
