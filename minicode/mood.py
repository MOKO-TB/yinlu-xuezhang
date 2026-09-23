"""情绪日记(mood.json):一句话记一下,每周回头看个形状 —— 不做诊断。

设计口径(和"学长不是医生"这条边界一起定的):

- **一天一条,不打卡**。同一天再记就覆盖前一条(回复里会说明覆盖了什么)。
  刻意**没有**连续天数、断签提醒、补签按钮这些东西:情绪记录一旦变成"不能断"的
  东西,记录本身就成了新的压力源。漏了几天不是问题,输出里也绝不出现"你断了 N 天"。
- **不给诊断、不给医疗建议**。这里所有文案只描述"你自己记了什么",不下判断:
  不说"你这是抑郁",不说"建议服药/治疗"。每周回顾末尾固定带一句非医疗声明。
- **该指路的时候指路**。记到自伤/活不下去这类话,或近两周多数日子都很低时,
  把"可以找学校心理健康教育中心"这段摆出来(免费、不用先证明自己够惨才配去)。
  求助内容分两段:**HELP_FACTS** 是给人看的(面板也直接用它,里面明说"号码还没核实过,
  所以不给你猜");**HELP_CAVEATS** 是对模型说话的转述规矩(绝不凭印象报号码),只随工具
  返回给模型,不会出现在给人看的界面上 —— 编错一个号码可能把人在最脆弱的时候导向空号。
- **数据全在本机**,导出/删除都是用户一键能做的动作(见 gui_bridge 的 mood_export /
  mood_clear),不进对话历史之外的任何地方。

文件格式(应用目录,手工可改):
    {"entries": [{"date": "2026-09-22", "score": 3, "note": "…,
                  "tags": ["睡眠"], "created_at": "2026-09-22 21:04"}]}
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from minicode.paths import app_path

MOOD_FILE = "mood.json"
WEEK_DAYS = 7               # 每周回顾的窗口
LOW_SCORE = 2               # ≤2 算"很低"(1-5 档里下面两档)
LOW_WINDOW = 14             # 看最近两周
LOW_TRIGGER = 3             # 两周里 ≥3 天很低 → 提一次求助去处
MIN_FOR_SHAPE = 3           # 窗口里少于 3 条就不谈"形状",只回读原文
MAX_NOTE = 500
MAX_ENTRIES = 730           # 两年,够看趋势,也够让人敢一直记

SCORE_LABELS = {1: "很难受", 2: "不太好", 3: "一般", 4: "还行", 5: "挺好"}

# 需要立刻指路的信号。刻意保守:只认明确说出自伤/活不下去的措辞,
# "烦死了""想哭"这类日常发泄不算 —— 把正常发泄当危机,用户很快就不会再记了。
CRISIS_WORDS = (
    "自杀", "不想活", "活着没意思", "不想醒", "结束生命", "了结自己",
    "割腕", "自伤", "伤害自己", "自我伤害", "撑不下去", "不如死",
)

# 求助去处:分两段。**HELP_FACTS 是可以直接给人看的**(面板也用它);
# HELP_CAVEATS 是对模型说话的转述规矩,不能出现在给人看的界面上。
HELP_FACTS = (
    "可以找学校的心理健康教育中心(校内咨询通常免费,不需要"
    "先证明自己「够严重」才配去):\n"
    "- 地址:博雅楼 S2-204(出自学校「广州应用科技学院心理中心」公众号 2024-04-17 "
    "那篇活动通知)。学校有广州/肇庆两个校区,那篇没写是哪个校区,所以以现场或公众号为准。\n"
    "- 预约口径:学校官网/公众号搜「心理咨询」线上预约,或直接去现场问。\n"
    "- 电话:我这里还没有一个核实过的号码,所以不给你猜——在学校公众号里搜「心理咨询」就能看到。\n"
    "- 只想先找同学聊聊:学校有「心理互助会」(校心理健康教育中心指导的学生社团,"
    "内设朋辈咨询部,朋辈咨询员经心理中心培训考核),每学期开学在公众号发招新。\n"
    "- 每年 5 月 25 日前后是「5·25 大学生心理健康活动月」,那阵子活动最好找、人也最多。\n"
    "- 如果此刻就有伤害自己的念头或已经动手:立刻打 120 或 110,"
    "或去最近的医院急诊,或敲身边任何一个人的门 —— 这一条是真的急。"
)

HELP_CAVEATS = (
    "\n(转述上面这段时的两条硬规矩:一,地址那行别替它认校区,通知里没写清;"
    "二,号码那行照实说「我这儿没存到经过核实的号码」,**绝对不要**凭印象报一个电话号码,"
    "也不要拿网上搜来的陌生热线顶替学校心理中心。)"
)

HELP_BLOCK = HELP_FACTS + HELP_CAVEATS


# ------------------------------------------------------------------ 读写

def state_path() -> Path:
    return app_path(MOOD_FILE)


def load_entries() -> list[dict]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return []
    return [e for e in data["entries"] if isinstance(e, dict) and e.get("date")]


def save_entries(entries: list[dict]) -> tuple[bool, str]:
    """原子写:先写 .tmp 再换名,避免写一半掉电把两年的记录变成半截 JSON。"""
    entries = sorted(entries, key=lambda e: str(e.get("date") or ""))[-MAX_ENTRIES:]
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({"entries": entries}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
        return True, ""
    except OSError as exc:
        return False, f"没能写入 {p.name}({type(exc).__name__}):这台机器的数据目录写不进东西"


def _today(today: date | None = None) -> date:
    return today or date.today()


def _norm_score(raw) -> int | None:
    """只认 1-5 的整数与几个常见说法;认不出来返回 None(只记文字,不硬凑分数)。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and 1 <= int(raw) <= 5:
        return int(raw)
    text = str(raw or "").strip()
    if text.isdigit() and 1 <= int(text) <= 5:
        return int(text)
    for n, label in SCORE_LABELS.items():
        if text == label:
            return n
    return None


def _norm_date(raw, today: date | None = None) -> tuple[str, str]:
    """返回 (ISO 日期, 说明)。只认确凿写法,认不出来不猜 —— 猜错一天,「那周」就少一天。"""
    text = str(raw or "").strip()
    now = _today(today)
    if not text:
        return now.isoformat(), ""
    if text in ("今天", "今日"):
        return now.isoformat(), ""
    if text in ("昨天", "昨日"):
        return (now - timedelta(days=1)).isoformat(), "记的是昨天"
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat(), ""
        except ValueError:
            continue
    return "", f"没认出「{raw}」是哪天,这条先不存(日期给我个 2026-09-22 这样的写法)"


# ------------------------------------------------------------------ 记录

def log(score=None, note: str = "", day: str = "", tags=None,
        today: date | None = None) -> dict:
    """记一天。同一天再记 = 覆盖,并在回复里说清覆盖了什么。"""
    iso, hint = _norm_date(day, today)
    if not iso:
        return {"ok": False, "msg": hint}
    if iso > _today(today).isoformat():
        return {"ok": False,
                "msg": f"{iso} 还没到,情绪不能提前记。(要记今天直接说「今天」就行)"}
    note = str(note or "").strip()[:MAX_NOTE]
    value = _norm_score(score)
    if score is not None and value is None and not note:
        return {"ok": False,
                "msg": "这个分数我认不出来(只收 1~5),而文字又是空的 —— "
                       "光一句「还行」这种就够记了,不用非给个数字。"}
    if not note and value is None:
        return {"ok": False, "msg": "分和话都没给,这条不记。今天怎么样?一句话就行。"}

    entries = load_entries()
    prev = next((e for e in entries if e.get("date") == iso), None)
    kept_tags = [str(t) for t in (tags or []) if str(t).strip()][:6]
    row = {"date": iso, "score": value, "note": note, "tags": kept_tags,
           "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    entries = [e for e in entries if e.get("date") != iso] + [row]
    ok, err = save_entries(entries)
    if not ok:
        return {"ok": False, "msg": err}

    lines = [f"已记下 {iso}:" + (f" {value} 分({SCORE_LABELS[value]})" if value else "")
             + (f" —— {note}" if note else "")]
    if hint:
        lines.append(f"({hint})")
    if value is None and score is not None:
        lines.append(f"(分数「{score}」我认不出,只按文字存了;要打分给 1~5。)")
    if prev:
        old = str(prev.get("note") or "") or f"{prev.get('score')} 分"
        lines.append(f"(覆盖了这天原来记的「{old}」;要留着原来那条就说一声)")
    if value is None:
        lines.append("(没给分数,只存了文字 —— 这样也行)")
    out = "\n".join(lines)
    r = {"ok": True, "msg": out, "entry": row}
    if _is_crisis(note):
        # msg 里只放**给人看的**那段;转述规矩另存一个 key,只有工具层会拼回去。
        r["msg"] = out + "\n\n" + _CRISIS_REPLY
        r["crisis"] = True
        r["caveat"] = HELP_CAVEATS
    return r


_CRISIS_REPLY = (
    "你写下这句话,我不当它是一般的心情不好。\n"
    "我不看病、也不判断你到了什么程度,但有件事想请你现在就做:" + "\n" + HELP_FACTS
)


def _is_crisis(text: str) -> bool:
    body = str(text or "")
    return any(w in body for w in CRISIS_WORDS)


def list_recent(days: int = 30, today: date | None = None) -> list[dict]:
    cutoff = (_today(today) - timedelta(days=max(1, int(days)) - 1)).isoformat()
    return [e for e in load_entries() if str(e.get("date") or "") >= cutoff]


# ------------------------------------------------------------------ 每周回顾

def weekly_review(today: date | None = None, days: int = WEEK_DAYS) -> dict:
    """这一周你自己记了什么。只回读,不判断 —— 判断归专业的人。"""
    today = _today(today)
    rows = []
    for e in load_entries():
        try:
            gap = (today - date.fromisoformat(str(e.get("date")))).days
        except ValueError:
            continue        # 手工改坏的日期:跳过这一条,不让整份回顾崩掉
        if 0 <= gap < days:
            rows.append(e)
    rows.sort(key=lambda e: e["date"])
    scored = [e for e in rows if isinstance(e.get("score"), int)]

    if not rows:
        return {"ok": True, "days": days, "count": 0, "text":
                f"最近 {days} 天没记过情绪。不想记就不记 —— 这东西不是任务,"
                "没有「这周没打卡」这回事。哪天想记一句,直接说「今天 3 分,因为…」就行。"}

    lines = [f"最近 {days} 天你记了 {len(rows)} 天:"]
    for e in rows:
        d = str(e["date"])[5:]
        raw = e.get("score")
        label = SCORE_LABELS.get(raw)
        mark = f"{raw} 分" + (f"({label})" if label else "") if raw else "只写了话"
        note = str(e.get("note") or "").strip()
        lines.append(f"- {d} {mark}" + (f" —— {note}" if note else ""))

    if len(scored) >= MIN_FOR_SHAPE:
        avg = sum(e["score"] for e in scored) / len(scored)
        low = [e for e in scored if e["score"] <= LOW_SCORE]
        lines.append(f"这几天的分平均 {avg:.1f}(1~5,你自己打的,不是任何量表)。")
        if low:
            lines.append(f"其中 {len(low)} 天你打了两分以下 —— "
                         "我不解释这是为什么,也不给它起名字,那是咨询师/医生干的活。")
        else:
            lines.append("没有哪天低到两分以下。")
    else:
        lines.append(f"记的条数还不够看出什么形状({len(scored)} 条带分),"
                     "这很正常,不代表你没在照顾自己。")

    recent_low = [e for e in list_recent(days=LOW_WINDOW, today=today)
                  if isinstance(e.get("score"), int) and e["score"] <= LOW_SCORE]
    if len(recent_low) >= LOW_TRIGGER:
        lines.append(f"\n最近 {LOW_WINDOW} 天里有 {len(recent_low)} 天你打了两分以下。"
                     "连着好多天都低,是去找学校心理中心聊聊的一个足够好的理由 —— "
                     "不用等到「更严重」才配去。")
        lines.append(HELP_BLOCK)
    if any(_is_crisis(e.get("note")) for e in rows):
        lines.append("\n这一周里有哪天你写下了很让人担心的话,那段话我不想只当成记录放着:"
                     + "\n" + HELP_BLOCK)

    lines.append("\n(以上只是把你自己记的东西回读给你看。我不是医生,这也不是诊断或"
                 "医疗建议;要看状况、要用药、要评估,请找学校心理中心或医院。)")
    return {"ok": True, "days": days, "count": len(rows), "text": "\n".join(lines)}


def mood_prompt_section(today: date | None = None) -> str:
    """系统提示词里的情绪段:只在**最近一周真记了东西**时才注入。

    没记的人不该每次开窗都被提示"你可以记录情绪" —— 那是广告,不是帮助。
    """
    today = _today(today)
    rows = list_recent(days=WEEK_DAYS, today=today)
    if not rows:
        return ""
    scored = [e for e in rows if isinstance(e.get("score"), int)]
    lines = ["\n# 情绪日记(本机 mood.json,最近一周)"]
    for e in rows:
        mark = f"{e['score']} 分" if e.get("score") else "只写了话"
        note = str(e.get("note") or "").strip()
        lines.append(f"- {e['date']} {mark}" + (f":{note}" if note else ""))
    if any(_is_crisis(e.get("note")) for e in rows):
        lines.append("- ⚠ 这一周里有记录写到了自伤/活不下去这类话。这一条**优先于**其它"
                     "所有回答风格:先接住人,再把下面这段求助去处**照原样**说给他"
                     "(号码那行照实说没存到,**不要**自己编一个号码),"
                     "不要讲道理、不要分析原因、不要建议「想开点」。\n" + HELP_BLOCK)
    lines += [
        "- 他问「我最近怎么样 / 这周状态如何」时,照上面这几行回读,"
        "并说明这是他自己记的内容、不是评估结果。",
        "- **绝不**给他下诊断、不说「你这是抑郁症/焦虑症」、不建议吃什么药,"
        "也不替他判断「要不要去看医生」以外的医疗决定;要往那边走,一律指向学校心理中心或医院。",
        "- 不催他记录,不说「你已经 N 天没记了」,不做连续打卡那套。",
        f"- 带分数的有 {len(scored)} 条;要看完整一周的口径用 mood(action=review)。",
    ]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ 导出 / 删除

def export_text() -> str:
    rows = load_entries()
    if not rows:
        return ""
    lines = ["# 我的情绪记录", "",
             f"共 {len(rows)} 条,导出于 {date.today().isoformat()}。",
             "这些是你自己记的话,原样导出,没有任何加工。", ""]
    for e in rows:
        mark = f"{e['score']} 分({SCORE_LABELS.get(e['score'], '')})" if e.get("score") else "—"
        lines.append(f"- {e['date']} {mark}"
                     + (f":{e['note']}" if e.get("note") else "")
                     + (f"  #{' #'.join(e['tags'])}" if e.get("tags") else ""))
    lines += ["", "(这不是医疗记录,也不能当医疗记录用。)"]
    return "\n".join(lines)


def clear_all() -> dict:
    n = len(load_entries())
    ok, err = save_entries([])
    if not ok:
        return {"ok": False, "msg": err}
    return {"ok": True, "msg": f"已清掉 {n} 条情绪记录(mood.json 现在是空的)。"
            "这一步没备份 —— 想先留个底的话,下次导出后再清。"}


# ------------------------------------------------------------------ 工具入口

def mood(ctx, action: str = "log", score=None, note: str = "", day: str = "",
         tags=None, days: int = WEEK_DAYS, reason: str = "") -> str:
    """mood 工具:记一条 / 回读一周 / 看清单 / 导出文本 / 删一条。"""
    action = (action or "log").strip().lower()
    if action == "log":
        r = log(score=score, note=note, day=day, tags=tags)
        if not r.get("ok"):
            return "没记下:" + r.get("msg", "未知原因")
        if r.get("crisis"):
            return (r["msg"] + "\n" + r.get("caveat", "") +
                    "\n(把上面那段照原样说给他,现在就说明白该找谁;别讲道理、"
                    "别分析原因、别建议「想开点」,也别追问细节。)")
        return r["msg"]
    if action == "review":
        return weekly_review(days=int(days or WEEK_DAYS))["text"]
    if action == "list":
        rows = list_recent(days=int(days or 30))
        if not rows:
            return f"最近 {days} 天没有情绪记录。想记一句就直接说,不用先问能不能记。"
        return weekly_review(days=int(days or 30))["text"]
    if action == "export":
        text = export_text()
        return text or "还没有任何记录,没有可导出的内容。"
    if action == "delete":
        iso, hint = _norm_date(day)
        if not iso:
            return hint
        entries = load_entries()
        keep = [e for e in entries if e.get("date") != iso]
        if len(keep) == len(entries):
            return f"{iso} 没有记录,没删任何东西。"
        ok, err = save_entries(keep)
        return err or f"已删掉 {iso} 那一条(其它 {len(keep)} 条原样保留)。"
    if action == "help":
        return HELP_BLOCK
    return (f"错误:未知 action '{action}'。可用:log(记一条)/review(看这一周)/"
            "list(看清单)/export(导出全文)/delete(删某天)/help(求助去处)")
