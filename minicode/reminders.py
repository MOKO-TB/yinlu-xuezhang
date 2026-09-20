"""个人截止日期提醒(reminders.json):学长能"记住并主动催"的事。

数据在应用目录(跟 notice-watch.json 同级),文件即状态、手工可编辑:
    {"next_id": 3, "items": [{"id": "r1", "title": "交奖学金材料",
                               "due": "2026-10-08", "note": "...",
                               "created_at": "...", "done": false}]}

模型通过 reminder 工具增删改查(用户说「提醒我周五交材料」就记一条),
看门狗(proactive.start_loop)每次运行把临期/逾期的一次性推送出来。
日期只认能确证的写法(2026-10-08 / 2026/10/8 / 10-08 补当前年);口语相对时间
(「周五」「下周三」)解析不了就如实存成无日期事项,只列清单不参与倒计时,
绝不猜一个日期出来。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

from minicode.paths import app_path

REMINDERS_FILE = "reminders.json"
DUE_SOON_DAYS = 3            # 距截止 ≤3 天算"临期"
MAX_ITEMS = 200

_DATE_PATTERNS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")


def state_path() -> Path:
    return app_path(REMINDERS_FILE)


def load_items() -> list[dict]:
    import json
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return [i for i in data["items"] if isinstance(i, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_items(items: list[dict]) -> None:
    import json
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(
            json.dumps({"next_id": _next_id(items), "items": items},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _next_id(items: list[dict]) -> int:
    return max([int(i["id"][1:]) for i in items
                if str(i.get("id", "")).startswith("r")
                and str(i.get("id", "")).lstrip("r").isdigit()] or [0]) + 1


def parse_due(raw) -> tuple[str, str]:
    """把用户给的日期解析成 (ISO 日期, 原文);解析不了返回 ("", 原文)。

    支持 2026-10-08 / 2026/10/8 / 2026.10.8 与 10-08、10/8(补当前年)。
    """
    text = str(raw or "").strip()
    if not text:
        return "", ""
    norm = text.replace("年", "-").replace("月", "-").replace("日", "")
    norm = re.sub(r"[./]", "-", norm)
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", norm)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat(), text
        except ValueError:
            return "", text
    m = re.match(r"^(\d{1,2})-(\d{1,2})$", norm)
    if m:
        try:
            return date(date.today().year, int(m[1]), int(m[2])).isoformat(), text
        except ValueError:
            return "", text
    return "", text


def add(title: str, due: str = "", note: str = "") -> dict:
    items = load_items()
    if len(items) >= MAX_ITEMS:
        return {"ok": False, "msg": f"提醒清单已满({MAX_ITEMS} 条),先清理已完成的"}
    iso, raw = parse_due(due)
    item = {"id": f"r{_next_id(items)}", "title": str(title or "").strip()[:120],
            "due": iso, "due_raw": raw if not iso else "",
            "note": str(note or "").strip()[:200],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "done": False}
    if not item["title"]:
        return {"ok": False, "msg": "事项内容不能为空"}
    items.append(item)
    save_items(items)
    return {"ok": True, "item": item}


def finish(item_id: str) -> dict:
    items = load_items()
    for item in items:
        if item.get("id") == str(item_id).strip():
            item["done"] = True
            save_items(items)
            return {"ok": True, "item": item}
    return {"ok": False, "msg": f"没有这条提醒:{item_id}"}


def delete(item_id: str) -> dict:
    items = load_items()
    kept = [i for i in items if i.get("id") != str(item_id).strip()]
    if len(kept) == len(items):
        return {"ok": False, "msg": f"没有这条提醒:{item_id}"}
    save_items(kept)
    return {"ok": True}


def list_items(include_done: bool = False) -> list[dict]:
    items = load_items()
    return [i for i in items if include_done or not i.get("done")]


def due_soon(items: list[dict] | None = None, today: date | None = None,
             within_days: int = DUE_SOON_DAYS) -> list[dict]:
    """临期与逾期(未完成):逾期排最前,按紧急度排序;无日期的不参与。"""
    today = today or date.today()
    out = []
    for item in (items if items is not None else list_items()):
        if item.get("done") or not item.get("due"):
            continue
        try:
            due = date.fromisoformat(str(item["due"]))
        except ValueError:
            continue
        days = (due - today).days
        if days <= within_days:
            out.append({**item, "days_left": days})
    out.sort(key=lambda x: x["days_left"])       # 逾期(负数)在最前
    return out


def _fmt_item(item: dict) -> str:
    if item.get("days_left") is not None and item.get("due"):
        days = item["days_left"]
        when = ("已过截止 " + str(-days) + " 天") if days < 0 else (
            "就是今天" if days == 0 else f"还有 {days} 天")
    else:
        when = item.get("due") or item.get("due_raw") or "未定日期"
    note = f"({item['note']})" if item.get("note") else ""
    return f"[{item['id']}] {item['title']}(截止 {when}){note}"


def reminder(ctx, action: str = "list", title: str = "", due: str = "",
             note: str = "", item_id: str = "") -> str:
    """reminder 工具处理器:待办/截止日期的增删查。"""
    action = (action or "list").strip().lower()
    if action == "add":
        result = add(title, due=due, note=note)
        if not result.get("ok"):
            return f"错误: {result.get('msg')}"
        item = result["item"]
        tail = ("以后每次启动学长都会检查;临期(3 天内)会主动提醒。"
                if item.get("due") else "(没有可解析的日期,只进清单不参与倒计时——"
                                        "确认到具体日期后可以用 action=finish 前先删掉重加)")
        return f"已记下提醒 [{item['id']}]:{item['title']}" + (
            f"(截止 {item['due']})" if item.get("due") else "") + f"。{tail}"
    if action == "list":
        items = list_items()
        if not items:
            return "提醒清单是空的。用户提到「X 月 X 日要交/要做」时,用 action=add 记一条。"
        urgent = due_soon(items)
        lines = [f"共 {len(items)} 条未完成"
                 + (f",其中 {len(urgent)} 条临期/逾期" if urgent else "") + ":"]
        lines += ["- " + _fmt_item(i) for i in urgent]
        lines += ["- " + _fmt_item(i) for i in items if i not in urgent]
        return "\n".join(lines)
    if action == "finish":
        result = finish(item_id)
        return (f"已完成:{result['item']['title']}" if result.get("ok")
                else f"错误: {result.get('msg')}")
    if action == "delete":
        result = delete(item_id)
        return "已删除该提醒。" if result.get("ok") else f"错误: {result.get('msg')}"
    return ("错误: 未知 action。可用: add(记提醒)/list(看清单)/"
            "finish(标记完成)/delete(删除),finish/delete 要带 item_id。")
