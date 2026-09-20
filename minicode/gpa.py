"""绩点/加权平均计算(纯本地计算,不联网、不落盘)。

各校绩点算法不同,这里不替学校"定标准":同时给出国内两种最常见的算法,
**公式随结果一起输出**,并固定提示"评奖评优/保研以学校教务处文件为准"。
- 常见五分制:绩点 = 分数/10 - 5(60 分 = 1.0,100 分 = 5.0,60 分以下计 0)
- 常见 4.0 制:绩点 = (分数-50)/10,90 分及以上按 4.0 封顶,60 分以下计 0
等级成绩(优/良/中/及格)按常见折算 95/85/75/65,折算表也随输出说明 ——
学校另有规定的以学校为准。
"""
from __future__ import annotations

GRADE_MAP = {"优": 95, "良": 85, "中": 75, "及格": 65, "合格": 65}


def _as_score(value) -> float | None:
    """分数容错:数字或「优/良/中/及格」等级;其余返回 None。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip()
    if text in GRADE_MAP:
        return float(GRADE_MAP[text])
    try:
        return float(text)
    except ValueError:
        return None


def _as_credit(value) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def _gpa5(score: float) -> float:
    return max(0.0, score / 10 - 5) if score >= 60 else 0.0


def _gpa4(score: float) -> float:
    return min(4.0, max(0.0, (score - 50) / 10)) if score >= 60 else 0.0


def gpa_calc(ctx, courses=None) -> str:
    """gpa_calc 工具处理器:courses 是 [{name, score, credit}, ...]。"""
    rows = courses if isinstance(courses, list) else []
    if not rows:
        return ("错误: 没有收到课程成绩。请先把用户给的成绩整理成列表再调用,"
                "每门课 {name: 课程名, score: 分数或等级, credit: 学分}。")
    parsed: list[dict] = []
    skipped: list[str] = []
    for i, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            skipped.append(f"第{i}项格式不对")
            continue
        score = _as_score(item.get("score"))
        credit = _as_credit(item.get("credit"))
        name = str(item.get("name") or f"课程{i}")
        if score is None or not (0 <= score <= 100):
            skipped.append(f"{name}(分数无效)")
            continue
        if credit is None:
            skipped.append(f"{name}(学分无效)")
            continue
        parsed.append({"name": name, "score": score, "credit": credit})
    if not parsed:
        return ("错误: 传入的成绩都解析不了(需要每门课有 0-100 的分数"
                "或「优/良/中/及格」等级,以及大于 0 的学分)。"
                + (f"被跳过: {'、'.join(skipped)}" if skipped else ""))
    total_credit = sum(c["credit"] for c in parsed)
    weighted_score = sum(c["score"] * c["credit"] for c in parsed) / total_credit
    gpa5 = sum(_gpa5(c["score"]) * c["credit"] for c in parsed) / total_credit
    gpa4 = sum(_gpa4(c["score"]) * c["credit"] for c in parsed) / total_credit

    lines = [f"共 {len(parsed)} 门课,总学分 {total_credit:g}。按学分加权:"]
    lines.append(f"- 加权平均分: {weighted_score:.2f}")
    lines.append(f"- 五分制绩点: {gpa5:.2f}(绩点 = 分数/10 - 5,60 分以下计 0)")
    lines.append(f"- 4.0 制绩点: {gpa4:.2f}(绩点 = (分数-50)/10,90 分及以上按 4.0 封顶,60 分以下计 0)")
    lines.append("明细:")
    for c in parsed:
        lines.append(f"- {c['name']} {c['score']:g} 分 × {c['credit']:g} 学分"
                     f" → 五分制 {_gpa5(c['score']):.1f} / 4.0 制 {_gpa4(c['score']):.1f}")
    if skipped:
        lines.append(f"被跳过的条目: {'、'.join(skipped)}。")
    if any(str(item.get("score") or "").strip() in GRADE_MAP for item in rows if isinstance(item, dict)):
        lines.append("等级成绩按常见折算(优=95 / 良=85 / 中=75 / 及格=65);"
                     "学校若另有折算表,以学校为准。")
    lines.append("注意: 各校绩点算法不同(有的用 4.0 分段制、有的五分制),"
                 "评奖学金/保研等请以学校教务处的绩点算法为准 —— 以上两种只是国内常见算法。")
    return "\n".join(lines)
