"""首页建议 chips:根据用户近期聊天记录动态生成,不是固定列表。

思路:维护一个校园主题池(关键词 + 代表问题),对用户最近的消息做关键词
打分;命中过的主题排前面(用户大概率想继续追问同方向),未命中的按默认
热度补齐到 9 条。没有任何历史时,输出即默认主题顺序。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MAX_SUGGESTIONS = 9
RECENT_BOOST = 2          # 最近几条消息权重加倍
RECENT_BOOST_N = 5
MAX_TEXTS = 40            # 最多取多少条用户消息参与打分


@dataclass
class Topic:
    tid: str
    keywords: list[str]
    ask: str
    followup: str = field(default="")   # 聊过该主题时,换一种追问说法


TOPICS: list[Topic] = [
    Topic("活动", ["活动", "比赛", "举办", "晚会", "讲座", "演出", "社团招新"],
          "最近学校有什么活动", "近期活动日程再帮我看看"),
    Topic("四级", ["四级", "cet", "英语四级", "口语", "听力"],
          "英语四级怎么备考", "四级备考还有什么提分技巧"),
    Topic("考研", ["考研", "研究生", "择校", "初试", "复试", "调剂"],
          "考研择校与复习规划", "考研复习规划还要看哪些细节"),
    Topic("奖学金", ["奖学金", "励志", "助学金", "评定", "资助"],
          "奖学金怎么申请", "奖助学金评定还有哪些条件"),
    Topic("社团", ["社团", "协会", "学生会", "招新", "组织"],
          "社团活动有哪些推荐", "社团方面还想了解什么"),
    Topic("竞赛", ["竞赛", "蓝桥", "互联网+", "学科竞赛", "报名", "大赛"],
          "学科竞赛怎么报名", "竞赛报名和备赛还有哪些要注意"),
    Topic("宿舍", ["宿舍", "住宿", "水电", "维修", "空调"],
          "宿舍生活指南", "宿舍水电维修流程是什么"),
    Topic("选课", ["选课", "学分", "绩点", "排课", "重修"],
          "选课有什么建议", "绩点和学分规则怎么算"),
    Topic("转专业", ["转专业", "转入", "转出", "专业调整"],
          "转专业需要什么条件", "转专业流程和时间节点"),
    Topic("实习就业", ["实习", "就业", "校招", "兼职", "找工作", "双选会"],
          "实习和校招信息哪里看", "校招双选会近期有哪几场"),
    Topic("图书馆", ["图书馆", "自习", "借书", "座位", "占座"],
          "图书馆自习座位怎么预约", "图书馆借还书规则是什么"),
    Topic("考试", ["期末考试", "补考", "考试安排", "成绩", "划重点"],
          "期末考试重点怎么划", "补考和成绩复核流程"),
    Topic("入党", ["入党", "团课", "推优", "党课", "积极分子"],
          "入党流程是怎样的", "入党推优条件再讲讲"),
    Topic("心理", ["心理", "咨询", "压力", "情绪"],
          "心理咨询怎么预约", "学校心理健康中心在哪"),
    Topic("校历", ["校历", "放假", "开学", "寒假", "暑假", "校安排"],
          "下学期校历安排", "寒暑假放假时间怎么定"),
    Topic("校址", ["校区", "地址", "导航", "在哪", "鼎湖", "肇庆", "大巴", "交通"],
          "肇庆校区地址和交通怎么走", "校区之间的交通怎么安排"),
]

# 默认顺序(无历史时展示):高频校园问题优先
_DEFAULT_ORDER = ["活动", "四级", "考研", "奖学金", "社团",
                  "竞赛", "图书馆", "宿舍", "选课"]


def collect_user_texts(sessions_dir: Path | None,
                       current_messages: list[dict] | None = None,
                       max_sessions: int = 5) -> list[str]:
    """收集最近的用户消息文本,当前会话最优先,其后按会话文件修改时间倒序。"""
    texts: list[str] = []
    for m in reversed(current_messages or []):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            texts.append(m["content"])
    if sessions_dir and Path(sessions_dir).exists():
        files = sorted(Path(sessions_dir).glob("session_*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:max_sessions]
        for p in files:
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("role") == "user" and isinstance(obj.get("content"), str):
                    texts.append(obj["content"])
    return texts[:MAX_TEXTS]


def build_suggestions(texts: list[str]) -> list[str]:
    """按关键词命中给主题打分,命中的主题用 followup 问法排前,默认主题补位。"""
    if not texts:
        return _default_suggestions()
    scored: list[tuple[int, Topic]] = []
    for topic in TOPICS:
        score = 0
        for i, t in enumerate(texts):
            low = t.lower()
            hits = sum(1 for k in topic.keywords if k.lower() in low)
            if hits:
                score += hits * (RECENT_BOOST if i < RECENT_BOOST_N else 1)
        if score > 0:
            scored.append((score, topic))
    scored.sort(key=lambda x: -x[0])
    out = [(t.followup or t.ask) for _, t in scored][:MAX_SUGGESTIONS]
    hit_ids = {t.tid for _, t in scored}
    for tid in _DEFAULT_ORDER:
        if len(out) >= MAX_SUGGESTIONS:
            break
        if tid not in hit_ids:
            topic = next(t for t in TOPICS if t.tid == tid)
            out.append(topic.ask)
    return out


def _default_suggestions() -> list[str]:
    return [next(t for t in TOPICS if t.tid == tid).ask for tid in _DEFAULT_ORDER]


# ---------------------------------------------------------------- 接着做 chips

CONTINUE_CAP = 3          # 这一行最多几枚;再多就不是"接着做",是刷屏
TITLE_CAP = 24            # 待办/步骤标题超长按省略号截,不重排也不改写原话
_FAR_FUTURE = 0           # days_left > 这个数的一律不进这一行

def _short(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= TITLE_CAP else text[:TITLE_CAP].rstrip() + "…"


def continue_chips(plan: dict | None = None, soon: list[dict] | None = None) -> list[dict]:
    """首页「接着做」那一行:计划里正挂着的那一步 + 今天要交/已逾期的待办。

    与话题 chips 的分工:那行是"可以问什么",这行是"有什么在做、有什么到期"。
    数据**一律读现成出口**(`planning.plan_state` 的调度视图、`reminders.due_soon`
    的临期清单),这里不解析日期、不判依赖、不猜状态 —— 那些都各有一处真相。

    诚实规矩:两样都没有就返回空列表,首页那一行整行不出现。编一条"接着做"出来
    (而用户其实没在做任何事)比没有更糟,这条与 chips 里"没填作息表就不报钟点"同源。
    """
    plan = plan if isinstance(plan, dict) else {}
    soon = soon if isinstance(soon, list) else []
    cands: list[tuple[int, dict]] = []

    for item in soon:
        if not isinstance(item, dict):
            continue
        title = _short(item.get("title"))
        days = item.get("days_left")
        if not title or not isinstance(days, int) or days > _FAR_FUTURE:
            continue        # 三天开外的事归"倒计时"那一行,不在这里重复出现
        clock = ""
        due_at = str(item.get("due_at") or "")
        if len(due_at) >= 16 and due_at[10] == "T":
            clock = due_at[11:16]          # 只有真写了钟点才说钟点,不猜"下午"
        if days < 0:
            cands.append((0, {
                "kind": "due", "label": f"已逾期 {-days} 天：{title}",
                "ask": f"逾期的「{title}」现在怎么处理，还有什么能补救",
                "note": f"截止 {item.get('due')}", "urgent": True}))
        else:
            cands.append((1, {
                "kind": "due", "label": f"今天要交：{title}" + (f"（{clock}）" if clock else ""),
                "ask": f"今天要交的「{title}」帮我拆解一下还差什么",
                "note": "今天截止", "urgent": True}))

    steps = {s.get("n"): s for s in (plan.get("steps") or []) if isinstance(s, dict)}
    running = [n for n in (plan.get("running") or []) if n in steps]
    failed = [n for n in (plan.get("failed") or []) if n in steps]
    ready = [n for n in (plan.get("ready") or []) if n in steps]
    if running:
        n = running[0]
        cands.append((2, {
            "kind": "plan", "label": f"接着做：第 {n} 步「{_short(steps[n].get('title'))}」",
            "ask": f"接着做计划的第 {n} 步「{_short(steps[n].get('title'))}」",
            "note": "计划里标着「进行中」的那一步", "urgent": False}))
    elif failed:
        n = failed[0]
        cands.append((3, {
            "kind": "plan", "label": f"第 {n} 步失败了：「{_short(steps[n].get('title'))}」",
            "ask": f"计划里第 {n} 步失败了，先看那一步是怎么回事",
            "note": "上游没做成的步骤，下游会被判为卡住", "urgent": False})
                 )
    elif ready:
        n = ready[0]
        cands.append((4, {
            "kind": "plan", "label": f"下一步可做：第 {n} 步「{_short(steps[n].get('title'))}」",
            "ask": f"开始做计划的第 {n} 步「{_short(steps[n].get('title'))}」",
            "note": "依赖都已完成，现在动得了", "urgent": False}))

    cands.sort(key=lambda x: x[0])
    return [c for _, c in cands[:CONTINUE_CAP]]
