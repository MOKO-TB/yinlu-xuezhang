"""校园地图:手绘地图建筑索引(地名解析)与 map_* 工具。

数据来源是线下交付包(手绘地图 + 36 张单栋标注图)里的 INDEX.json,副本在
frontend/campus-index.json:36 处建筑/构筑物、203 条别名、20 条真实歧义、
10 处非建筑标注,坐标一律基于源图 2559×1279。建筑图由
scripts/build_campus_assets.py 从单栋标注 PNG 生成到 frontend/img/campus/。

解析规则照抄交付包的 how_to_resolve(归一化 → 别名精确匹配 → 一对多不猜 →
查非建筑登记 → 都没有就明说没有)。**这套数据是位置图,没有路网拓扑**,
所以只能回答"在哪、旁边是什么",不能编"走哪条路、几分钟到"。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from minicode.paths import resource_path

INDEX_REL = ("frontend", "campus-index.json")
COLORS = ("orange", "teal", "red", "blue", "gold")
DEFAULT_IMAGE_DIR = "img/campus/1280"
_CACHE: dict[str, tuple[float, dict]] = {}

# 提问里的动作/疑问词:归一化时先剥掉("图书馆怎么走" → "图书馆")
_ASK_WORDS = (
    "怎么走", "怎么去", "怎么到", "怎么过去", "咋走", "如何去", "怎样走", "去哪走",
    "在哪里", "在哪儿", "在什么地方", "在哪个位置", "在哪个方位", "在哪", "位置",
    "带我去", "我要去", "想去", "远不远", "多远", "离得远吗", "怎么找",
)
_DROP = re.compile(r"[\s,，。、·．\.\-—_/\\|?？!！:：;；()（）\[\]【】“”\"'’‘]+")
_UNIT = re.compile(r"[栋座幛楼]")   # 栋/座/楼 视为等价:两侧都去掉再比


# ------------------------------------------------------------------ 数据加载

def index_path() -> Path:
    return resource_path(*INDEX_REL)


def load_index(path: Path | None = None) -> dict:
    """读取建筑索引;按 mtime 缓存,改索引文件立即生效。"""
    p = Path(path) if path is not None else index_path()
    key = str(p)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {"buildings": [], "not_buildings": [], "alias_conflicts": {},
                "caveats": [], "app_assets": {}, "size": {"width": 2559, "height": 1279}}
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    data = json.loads(p.read_text(encoding="utf-8"))
    _CACHE[key] = (mtime, data)
    return data


def buildings(index: dict) -> list[dict]:
    return [b for b in (index.get("buildings") or []) if b.get("name")]


def by_no(index: dict) -> dict[int, dict]:
    return {int(b["no"]): b for b in buildings(index) if b.get("no") is not None}


def source_size(index: dict) -> dict:
    sm = index.get("source_map") or {}
    return {"width": int(sm.get("width") or 2559), "height": int(sm.get("height") or 1279)}


def image_of(index: dict, b: dict) -> str:
    """建筑图在前端里的相对路径(由 build_campus_assets.py 生成)。"""
    assets = index.get("app_assets") or {}
    base = str(assets.get("building_image_dir") or DEFAULT_IMAGE_DIR).rstrip("/")
    pattern = str(assets.get("building_image_pattern") or "{no:02d}.webp")
    try:
        name = pattern.format(no=int(b.get("no", 0)))
    except (KeyError, ValueError, IndexError):
        name = f"{int(b.get('no', 0)):02d}.webp"
    return f"{base}/{name}"


def frontend_payload(index: dict | None = None) -> dict:
    """下发给前端的图形数据(建筑图路径已算好,前端不必知道命名规则)。"""
    idx = index if index is not None else load_index()
    assets = idx.get("app_assets") or {}
    return {
        "title": "校园地图 · 肇庆校区(手绘)",
        "base_map": str(assets.get("base_map") or "img/campus/map.jpg"),
        "size": source_size(idx),
        "source": ((idx.get("source_map") or {}).get("note") or ""),
        "items": [
            {
                "no": int(b.get("no", 0)),
                "name": str(b.get("name", "")),
                "category": str(b.get("category", "")),
                "bbox": b.get("bbox_on_map") or [],
                "center": b.get("center_on_map") or [],
                "area": str(b.get("area_on_map", "")),
                "nearby": [str(x) for x in (b.get("nearby") or [])],
                "aliases": _aliases_of(b),
                "image": image_of(idx, b),
            }
            for b in buildings(idx)
        ],
        "not_buildings": [
            {"name": str(n.get("name", "")), "type": str(n.get("type", "")),
             "where": str(n.get("where", ""))}
            for n in (idx.get("not_buildings") or [])
        ],
        "caveats": [str(c) for c in (idx.get("caveats") or [])],
    }


# ------------------------------------------------------------------ 地名解析

def normalize(text: str) -> str:
    """归一化提问:剥掉动作词、空格标点,栋/座/楼等价,拉丁字母转小写。"""
    s = str(text or "").lower()
    out = s
    for w in _ASK_WORDS:
        out = out.replace(w, "")
    out = _DROP.sub("", out)
    out = _UNIT.sub("", out)
    return out.strip()


def _alias_pairs(index: dict) -> list[tuple[str, dict]]:
    pairs: list[tuple[str, dict]] = []
    for b in buildings(index):
        names = [str(b.get("name", ""))] + [str(a) for a in (b.get("aliases") or [])]
        for n in names:
            key = normalize(n)
            if key:
                pairs.append((key, b))
    return pairs


def resolve(index: dict, query: str) -> dict:
    """把一个地名解析成建筑/非建筑。返回四态之一。

    hit        唯一命中 → {"building": b}
    ambiguous  真实歧义(如 A栋 = 学思苑A / 集贤苑A座)→ {"alias": 原文, "candidates": [...]}
    not_building 场地/道路/水系 → {"place": {...}}
    miss       索引里没有 → {}
    """
    q = normalize(query)
    if not q:
        return {"status": "miss", "query": str(query or "")}

    pairs = _alias_pairs(index)

    exact = [b for key, b in pairs if key == q]
    hits = _dedupe(exact)
    if hits:
        return _classify(hits, query)

    partial = [b for key, b in pairs if len(key) >= 2 and (key in q or q in key)]
    hits = _dedupe(partial)
    if hits:
        return _classify(hits, query)

    places = index.get("not_buildings") or []
    for p in places:
        key = normalize(p.get("name", ""))
        if key and (key == q or key in q or (len(q) >= 2 and q in key)):
            return {"status": "not_building", "query": str(query or ""), "place": p}
    # 桥名是「宽溪桥／掬溪桥／临溪桥」这种合写的一条,单独按分隔符拆一次
    for p in places:
        for piece in re.split(r"[／/]", str(p.get("name", ""))):
            key = normalize(piece)
            if key and key == q:
                return {"status": "not_building", "query": str(query or ""), "place": p}

    return {"status": "miss", "query": str(query or "")}


def _dedupe(items: list[dict]) -> list[dict]:
    out, seen = [], set()
    for b in items:
        no = int(b.get("no", 0))
        if no not in seen:
            seen.add(no)
            out.append(b)
    return out


def _classify(hits: list[dict], query: str) -> dict:
    if len(hits) == 1:
        return {"status": "hit", "query": str(query or ""), "building": hits[0]}
    return {"status": "ambiguous", "query": str(query or ""),
            "alias": str(query or ""), "candidates": hits}


def _conflicts_for(index: dict, query: str) -> list[str]:
    """交付包给的歧义表里,有没有这一条(用来把话说得更确定)。"""
    conflicts = index.get("alias_conflicts") or {}
    q = normalize(query)
    for alias, names in conflicts.items():
        if normalize(alias) == q:
            return [str(n) for n in names]
    return []


def describe(b: dict) -> str:
    no = b.get("no")
    tail = " · ".join(x for x in (str(b.get("category", "")), str(b.get("area_on_map", ""))) if x)
    return f"{b.get('name')}(No.{no}{' · ' + tail if tail else ''})"


def _aliases_of(b: dict) -> list[str]:
    return [str(a) for a in (b.get("aliases") or [])]


def _item(index: dict, b: dict) -> dict:
    """推给前端的一条图形项。

    带 aliases:前端搜索框要用同一批俗称做本地匹配(不消耗模型调用),
    所以归一化规则必须与 normalize() 保持一致。
    """
    return {
        "no": int(b.get("no", 0)),
        "name": str(b.get("name", "")),
        "category": str(b.get("category", "")),
        "bbox": b.get("bbox_on_map") or [],
        "center": b.get("center_on_map") or [],
        "area": str(b.get("area_on_map", "")),
        "nearby": [str(x) for x in (b.get("nearby") or [])],
        "aliases": _aliases_of(b),
        "image": image_of(index, b),
    }


# ------------------------------------------------------------------ 工具实现

def _push(ctx, payload: dict) -> bool:
    """把图形指令推给 GUI;终端/无 UI 时静默跳过(文字结论仍然返回)。"""
    ui = getattr(ctx, "ui", None)
    if ui is None:
        return False
    try:
        ui.push("map", **payload)
        return True
    except Exception:
        return False


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in re.split(r"[,，、;；\n]+", value) if p.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def _caveat_line(index: dict) -> str:
    return ("这是位置图、不是导航:索引里没有路网,只能给方位与邻近建筑,"
            "不要编路线或步行时间。")


def _not_building_text(p: dict) -> str:
    return (f"「{p.get('name')}」不是建筑,是{p.get('type', '场地')},交付索引里没有它的"
            f"坐标与出图。位置说明:{p.get('where', '')}")


def _miss_text(index: dict, query: str) -> str:
    names = "、".join(str(b.get("name")) for b in buildings(index))
    return (f"索引里没有「{query}」这个标注,"
            f"不要编位置。可用的 36 处标注是:{names}。"
            f"(另有非建筑登记:{'、'.join(str(p.get('name')) for p in (index.get('not_buildings') or []))})")


def map_find_place(ctx, query: str, color: str | None = None) -> str:
    """按用户口语找一处地点:命中就框出来并出单栋标注图,歧义就把候选都摆出来。"""
    index = load_index()
    color = color if color in COLORS else "orange"
    res = resolve(index, query)
    status = res["status"]

    if status == "hit":
        b = res["building"]
        shown = _push(ctx, {"op": "focus", "title": f"定位:{b.get('name')}",
                            "items": [_item(index, b)], "color": color})
        lines = [
            f"已在地图上框出「{b.get('name')}」并显示它的单栋标注图"
            + ("。" if shown else "(当前无图形界面,只给文字结论)。"),
            f"位置:{b.get('area_on_map', '')};类别:{b.get('category', '')};"
            f"邻近:{'、'.join(str(x) for x in (b.get('nearby') or [])) or '—'}。",
            f"学生也常叫:{'、'.join(_aliases_of(b)) or '—'}。",
            _caveat_line(index),
        ]
        return "\n".join(lines)

    if status == "ambiguous":
        cands = res["candidates"]
        fixed = _conflicts_for(index, query)
        shown = _push(ctx, {"op": "focus", "title": f"「{query}」对应 {len(cands)} 处",
                            "items": [_item(index, b) for b in cands],
                            "color": "gold", "ask": True})
        lines = [
            f"「{query}」在地图上对应 {len(cands)} 处:{'、'.join(str(b.get('name')) for b in cands)}"
            + ("。" if shown else "(无图形界面)。" + ""),
            "**不要替用户挑一个** —— 这是真实歧义,两处相距很远,猜错就把人指到反方向。",
            "已把候选都标在图上了,请先问用户指的是哪一栋。",
        ]
        for b in cands:
            lines.append(f"- {describe(b)};学生常叫:{'、'.join(_aliases_of(b)) or '—'}")
        if fixed:
            lines.append(f"(交付索引的歧义表也记着:{' / '.join(fixed)})")
        return "\n".join(lines)

    if status == "not_building":
        p = res["place"]
        _push(ctx, {"op": "notice", "level": "info", "title": str(p.get("name", "")),
                    "text": _not_building_text(p)})
        return _not_building_text(p) + "\n(可以给位置说明,但不要出图、不要编图片。)"

    _push(ctx, {"op": "notice", "level": "warn", "title": "没找到",
                "text": _miss_text(index, query)})
    return _miss_text(index, query)


def map_show_list(ctx, category: str | None = None, query: str | None = None) -> str:
    """列出（可筛选）地图上已标注的地点,并在界面上打开列表。"""
    index = load_index()
    items = buildings(index)
    if category:
        items = [b for b in items if category in str(b.get("category", ""))]
    if query:
        items = [b for b in items
                 if normalize(query) and (normalize(query) in normalize(b.get("name", ""))
                                          or normalize(query) in
                                          " ".join(normalize(a) for a in _aliases_of(b)))]
    if not items:
        return (f"没有匹配的地点。可用类别:"
                f"{'、'.join(sorted({str(b.get('category', '')) for b in buildings(index)}))};"
                f"不传参数可列出全部。")

    title = "地图标注全览" if not (category or query) else \
        "筛选:" + " / ".join(str(x) for x in (category, query) if x)
    _push(ctx, {"op": "list", "title": title,
                "items": [_item(index, b) for b in items]})

    lines = [f"{title}(共 {len(items)} 处),已在地图面板列出。"]
    for b in items:
        lines.append(f"- {b.get('name')}｜{b.get('category', '')}｜{b.get('area_on_map', '')}")
    places = index.get("not_buildings") or []
    if places:
        lines.append("以下是非建筑标注(未出图,问到就口头说明位置):"
                     + "、".join(f"{p.get('name')}({p.get('type', '')})" for p in places))
    lines.append(_caveat_line(index))
    return "\n".join(lines)


def map_highlight_building(ctx, names, color: str | None = None) -> str:
    """一次在地图上框出多处地点(适合"标出所有食堂/所有宿舍"这类批量请求)。"""
    index = load_index()
    color = color if color in COLORS else "orange"
    wanted = _as_list(names)
    if not wanted:
        return "错误: names 为空,请给出地点名,如 ['图书馆']。"

    hits: list[dict] = []
    seen: set[int] = set()
    lines: list[str] = []
    for w in wanted:
        res = resolve(index, w)
        if res["status"] == "hit":
            b = res["building"]
            if int(b.get("no", 0)) not in seen:
                seen.add(int(b.get("no", 0)))
                hits.append(b)
        elif res["status"] == "ambiguous":
            cands = res["candidates"]
            for b in cands:
                if int(b.get("no", 0)) not in seen:
                    seen.add(int(b.get("no", 0)))
                    hits.append(b)
            lines.append(f"「{w}」有歧义,对应 {'、'.join(str(b.get('name')) for b in cands)},"
                         f"已全部标出,回答时要说明是哪一栋、必要时先问用户。")
        elif res["status"] == "not_building":
            lines.append(_not_building_text(res["place"]))
        else:
            lines.append(f"索引里没有「{w}」,不要编位置。")

    if not hits:
        return "\n".join(lines) if lines else _miss_text(index, "、".join(wanted))

    _push(ctx, {"op": "focus", "title": f"标出 {len(hits)} 处",
                "items": [_item(index, b) for b in hits], "color": color})
    head = f"已在地图上框出 {len(hits)} 处:{'、'.join(str(b.get('name')) for b in hits)}"
    return "\n".join([head] + lines + [_caveat_line(index)])


def map_drop_pin(ctx, name: str | None = None, x: float | None = None,
                 y: float | None = None, label: str | None = None,
                 color: str | None = None, note: str | None = None) -> str:
    """在地图上钉一个图钉(按地点名,或直接给源图坐标 0-2559 / 0-1279)。"""
    index = load_index()
    color = color if color in COLORS else "red"
    w, h = source_size(index)["width"], source_size(index)["height"]
    no, px, py = 0, None, None

    if name:
        res = resolve(index, name)
        if res["status"] == "ambiguous":
            return (f"「{name}」有歧义,对应 "
                    f"{'、'.join(str(b.get('name')) for b in res['candidates'])},"
                    "先问用户是哪一栋再钉。")
        if res["status"] == "not_building":
            return _not_building_text(res["place"]) + "(索引里没有坐标,钉不了图钉。)"
        if res["status"] != "hit":
            return _miss_text(index, name)
        b = res["building"]
        no = int(b.get("no", 0))
        c = b.get("center_on_map") or []
        if len(c) != 2:
            return f"「{b.get('name')}」在索引里没有中心点坐标,钉不了图钉。"
        px, py = float(c[0]), float(c[1])
        label = label or str(b.get("name", ""))
        note = note or str(b.get("area_on_map", ""))
    elif x is not None and y is not None:
        px, py = float(x), float(y)
        if not (0 <= px <= w and 0 <= py <= h):
            return f"坐标超出底图范围(0-{w} / 0-{h}):({px},{py})。"
        label = label or "标记点"
    else:
        return "错误: 请给出 name(地点名)或同时给出 x 与 y 坐标。"

    _push(ctx, {"op": "pin", "no": no, "x": px, "y": py, "label": label,
                "color": color, "note": note or ""})
    if no:
        b = next((x for x in buildings(index) if int(x.get("no", 0)) == no), None)
        place = f"「{b.get('name')}」({b.get('area_on_map', '')})" if b else f"「{label}」"
        return f"已在 {place} 钉上图钉,标签「{label}」。"
    return f"已在「{label}」钉上图钉(源图坐标 {int(px)},{int(py)})。"


def map_clear(ctx, scope: str = "all") -> str:
    """清除地图上的标记(标注框 / 图钉 / 列表 / 提示)。"""
    scope = scope if scope in ("all", "highlight", "pin", "list") else "all"
    _push(ctx, {"op": "clear", "scope": scope})
    names = {"all": "全部标记", "highlight": "标注框", "pin": "图钉", "list": "列表"}
    return f"已清除地图上的{names[scope]}。"
