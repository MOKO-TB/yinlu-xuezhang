"""校园天气(Open-Meteo,免 Key 免注册):首页"今天要不要带伞"chip 与 get_weather 工具。

坐标默认肇庆鼎湖(学校所在地),维护者可改 —— 在应用目录放一个
 weather.json:{"lat": 23.15, "lon": 112.57, "label": "肇庆·鼎湖"}。
数据源固定是 api.open-meteo.com(URL 由程序拼,用户/模型都不能左右它),
结果缓存在应用目录 weather-cache.json,TTL 30 分钟:

- 首页 chip 只读缓存、当场返回,缓存过期时后台线程去刷新(绝不在 UI 线程里等网络);
- get_weather 工具给模型答"这周末下雨吗"这类问题,缓存新鲜就用缓存,否则现拉;
- 任何网络/解析失败一律静默降级:chip 直接不出现,工具返回"查不到"——
  锦上添花的功能不能变成报错源,也不能让模型没数据时编天气。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path

from minicode.paths import app_path

API = "https://api.open-meteo.com/v1/forecast"
CITY_FILE = "weather.json"
CACHE_FILE = "weather-cache.json"
CACHE_TTL = 30 * 60                    # 首页 chip 半小时内的结果算新鲜
FETCH_TIMEOUT = 8.0
DEFAULT_CITY = {"lat": 23.15, "lon": 112.57, "label": "肇庆·鼎湖"}
# WMO 天气代码 → (图标, 中文)。只列 Open-Meteo 常见值,认不出的回"变化"。
_WMO = {
    0: ("☀️", "晴"), 1: ("🌤", "基本晴朗"), 2: ("⛅", "多云"), 3: ("☁️", "阴"),
    45: ("🌫", "雾"), 48: ("🌫", "雾凇"),
    51: ("🌦", "毛毛雨"), 53: ("🌦", "毛毛雨"), 55: ("🌧", "毛毛雨"),
    56: ("🌧", "冻毛毛雨"), 57: ("🌧", "冻毛毛雨"),
    61: ("🌧", "小雨"), 63: ("🌧", "中雨"), 65: ("🌧", "大雨"),
    66: ("🌧", "冻雨"), 67: ("🌧", "冻雨"),
    71: ("🌨", "小雪"), 73: ("🌨", "中雪"), 75: ("❄️", "大雪"), 77: ("❄️", "雪粒"),
    80: ("🌦", "阵雨"), 81: ("🌧", "强阵雨"), 82: ("⛈", "暴雨"),
    85: ("🌨", "阵雨夹雪"), 86: ("❄️", "阵雪"),
    95: ("⛈", "雷阵雨"), 96: ("⛈", "雷雨伴冰雹"), 99: ("⛈", "雷雨伴强冰雹"),
}

_refresh_lock = threading.Lock()
_refreshing = False

# 雨类天气码(降雨/冻雨/雷雨;雾与雪不算"要带伞")
RAIN_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99})


def _places() -> list[dict]:
    """weather.json → 地点列表(至少一条,坏了回落默认坐标)。

    两种写法都认(向后兼容):
    - 旧单对象: {"lat": .., "lon": .., "label": "肇庆·鼎湖"}
    - 多地点:   {"places": [{"name": "学校", "lat": .., "lon": .., "label": ".."}, …],
                 "default": "学校"}
    `name` 是工具里切地点用的键;`default` 只是把谁排到第一位(首页 chip 跟着
    第一位走),没有 default 就按文件里的顺序。**不做自动定位**:地点是用户
    自己命名的,程序不猜他在哪。
    """
    try:
        raw = json.loads(app_path(CITY_FILE).read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    out: list[dict] = []

    def _add(entry, fallback_label: str = "") -> None:
        if not isinstance(entry, dict):
            return
        try:
            lat, lon = float(entry["lat"]), float(entry["lon"])
        except (KeyError, TypeError, ValueError):
            return                      # 坐标坏的条目跳过,不拖垮其余地点
        label = str(entry.get("label") or entry.get("name")
                    or fallback_label).strip()
        if not label:
            return
        name = str(entry.get("name") or label).strip()
        out.append({"name": name or label, "label": label, "lat": lat, "lon": lon})

    if isinstance(raw, dict) and isinstance(raw.get("places"), list):
        for entry in raw["places"]:
            _add(entry)
        default = str(raw.get("default") or "").strip().lower()
        if default:
            for i, p in enumerate(out):
                if p["name"].lower() == default:
                    out.insert(0, out.pop(i))     # 默认地点排第一
                    break
    elif isinstance(raw, dict) and raw.get("lat") is not None:
        _add(raw, DEFAULT_CITY["label"])          # 旧单对象格式
    if not out:
        out = [{**DEFAULT_CITY, "name": DEFAULT_CITY["label"]}]
    return out


def _city(name: str = "") -> dict | None:
    """按名字取一个地点;name 空 = 默认(列表第一位)。认不出返回 None ——
    调用方要如实说"没有这个地点",不能悄悄落回默认(那是编)。"""
    rows = _places()
    if not name or not str(name).strip():
        return rows[0]
    key = str(name).strip().lower()
    for p in rows:
        if p["name"].lower() == key or p["label"].lower() == key:
            return p
    return None


def _cache_path() -> Path:
    return app_path(CACHE_FILE)


def _load_cache() -> dict:
    """读缓存文件原样返回;没有/坏了返回空 dict。

    两种格式:旧 {"ts", "data"}(单地点时代)与新 {"entries": {名: {"ts","data"}}}。
    归一化交给 _cache_entry / _save_entry。
    """
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {}


def _cache_entry(name: str) -> tuple[dict | None, float | None]:
    """(该地点的预报数据, 抓取时刻)。旧格式只有默认地点一条。"""
    raw = _load_cache()
    if not raw:
        return None, None
    entries = raw.get("entries")
    if isinstance(entries, dict):                      # 新格式
        hit = entries.get(name) or {}
    elif raw.get("data") is not None:                  # 旧格式:就是默认那一条
        default = _places()[0]["name"]
        hit = {"ts": raw.get("ts"), "data": raw.get("data")} if name == default else {}
    else:
        hit = {}
    data = hit.get("data")
    ts = hit.get("ts")
    return (data if isinstance(data, dict) else None,
            float(ts) if isinstance(ts, (int, float)) else None)


def _save_entry(name: str, payload: dict) -> None:
    """把一个地点的预报写进缓存(其余地点的条目原样保留)。"""
    try:
        raw = _load_cache()
        entries = raw.get("entries")
        entries = dict(entries) if isinstance(entries, dict) else {}
        if not entries and raw.get("data") is not None:
            # 旧格式迁移:原有那条归到默认地点名下
            default = _places()[0]["name"]
            entries[default] = {"ts": raw.get("ts"), "data": raw.get("data")}
        entries[name] = {"ts": time.time(), "data": payload}
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"entries": entries}, ensure_ascii=False),
                        encoding="utf-8")
    except OSError:
        pass                      # 写不进缓存只是下次再拉,不影响使用


def _fetch(city: dict) -> dict:
    """拉一次预报(今天起 4 天)。任何异常向上抛,由调用方吞。"""
    import httpx
    resp = httpx.get(API, params={
        "latitude": city["lat"], "longitude": city["lon"],
        "current": "temperature_2m,apparent_temperature,precipitation,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max",
        "timezone": "Asia/Shanghai", "forecast_days": 4,
    }, timeout=FETCH_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or "daily" not in data:
        raise RuntimeError("天气服务返回结构不对")
    return data


def refresh_async(name: str = "") -> None:
    """后台线程刷新缓存;同一进程同一地点只允许一个在途请求。

    name 空 = 默认地点(首页 chip 就是它)。断网/超时静默:chip 保持旧值或不出现。
    """
    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True

    def run() -> None:
        global _refreshing
        try:
            city = _city(name)
            if city is not None:
                _save_entry(city["name"], _fetch(city))
        except Exception:
            pass                  # 断网/超时:静默,chip 保持旧值或不出现
        finally:
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=run, daemon=True, name="yinluxs-weather").start()


# ---------------------------------------------------------------- 表述

def _wmo(code):
    try:
        return _WMO.get(int(code), ("🌡", "天气变化"))
    except (TypeError, ValueError):
        return ("🌡", "天气变化")


def _today_index(data: dict, today: date | None = None) -> int:
    today = today or date.today()
    dates = [str(d) for d in ((data.get("daily") or {}).get("time") or [])]
    return dates.index(today.isoformat()) if today.isoformat() in dates else 0


def _day_row(data: dict, i: int) -> dict | None:
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    if i >= len(times):
        return None
    def _num(key):
        rows = daily.get(key) or []
        return rows[i] if i < len(rows) else None
    code = (daily.get("weather_code") or [None])[i] if daily.get("weather_code") else None
    return {"date": str(times[i]), "code": code,
            "t_min": _num("temperature_2m_min"), "t_max": _num("temperature_2m_max"),
            "rain_pct": _num("precipitation_probability_max")}


def umbrella_tip(data: dict, today: date | None = None) -> str:
    """一句话建议:带伞 / 防晒 / 温差大。取第一条命中的,都没有就空串。"""
    d0 = _day_row(data, _today_index(data, today))
    if not d0:
        return ""
    cur = data.get("current") or {}
    try:
        precip = float(cur.get("precipitation") or 0)
    except (TypeError, ValueError):
        precip = 0.0
    pct = d0.get("rain_pct")
    if precip > 0.05 or (pct is not None and float(pct) >= 40):
        return "今天大概率有雨,记得带伞"
    try:
        t_max = float(d0.get("t_max") or 0)
    except (TypeError, ValueError):
        t_max = 0.0
    if t_max >= 33:
        return "今天高温,防晒多喝水"
    d1 = _day_row(data, _today_index(data, today) + 1)
    try:
        if (d1 and d0.get("t_min") is not None and d1.get("t_min") is not None
                and float(d0["t_min"]) - float(d1["t_min"]) >= 6):
            return "明天气温骤降,加件外套"
    except (TypeError, ValueError):
        pass
    return ""


def describe_day(data: dict, i: int, label: str = "") -> str:
    row = _day_row(data, i)
    if not row:
        return ""
    icon, name = _wmo(row["code"])
    span = ""
    if row.get("t_min") is not None and row.get("t_max") is not None:
        span = f" {row['t_min']}~{row['t_max']}℃"
    rain = (f" · 降水概率 {int(float(row['rain_pct']))}%"
            if row.get("rain_pct") is not None else "")
    head = label or row["date"]
    return f"{head}: {icon} {name}{span}{rain}"


def card_text(data: dict, today: date | None = None) -> str:
    """chip 上那半行短语:"⛅ 27℃ · 今天 24~31℃"。"""
    cur = data.get("current") or {}
    icon, name = _wmo((data.get("daily") or {}).get("weather_code", [None])[
        _today_index(data, today)] if (data.get("daily") or {}).get("weather_code") else None)
    try:
        temp = f"{round(float(cur['temperature_2m']))}℃"
    except (KeyError, TypeError, ValueError):
        temp = name
    d0 = _day_row(data, _today_index(data, today))
    span = ""
    if d0 and d0.get("t_min") is not None and d0.get("t_max") is not None:
        span = f" · 今天 {d0['t_min']}~{d0['t_max']}℃"
    return f"{icon} {temp}{span}"


# ---------------------------------------------------------------- 出口

def weather_card(today: date | None = None) -> dict | None:
    """首页 chip 的数据:只读缓存、绝不当场等网络(地点恒为默认那一个)。

    返回 {icon,text,tip,city,refreshing};缓存完全不存在时**顺手 kick 一次
    后台刷新**并返回 None(前端看到 refreshing 会过几秒再要一次)。
    """
    city = _places()[0]
    data, ts = _cache_entry(city["name"])
    fresh = ts is not None and (time.time() - ts < CACHE_TTL)
    if not fresh:
        refresh_async(city["name"])
    if data is None:
        return None
    icon_text = card_text(data, today)
    icon = icon_text.split(" ")[0] if icon_text else "🌡"
    rest = icon_text[len(icon):].strip()
    ago = "" if fresh else f"(缓存于 {time.strftime('%H:%M', time.localtime(ts or 0))})"
    return {"icon": icon, "text": rest,
            "tip": " · ".join(x for x in (umbrella_tip(data, today),
                                          city.get("label", "") + ago) if x),
            "umbrella": "带伞" in umbrella_tip(data, today),
            "refreshing": bool(not fresh)}


def cached_data(name: str = "") -> dict | None:
    """只读缓存(不联网、不触发刷新):新鲜返回该地点的预报,过期/没有返回 None。

    给看门狗的"明天有雨"提醒用 —— 提醒绝不为检查天气去联网(简报同款纪律),
    缓存是旧的就这一轮跳过,6 小时后下一轮大概率是新的。
    """
    city = _city(name)
    if city is None:
        return None
    data, ts = _cache_entry(city["name"])
    if data is None or ts is None:
        return None
    return data if (time.time() - ts < CACHE_TTL) else None


def tomorrow_rain(data: dict, today: date | None = None) -> dict | None:
    """明天会不会下雨:降水概率 ≥40% 或预报码是雨类(与今天带伞同一条线)。

    数据是**预报**,不是事实 —— 调用方的文案要带"预报"字样。明天不在预报
    范围内(理论上不会,fetch 拉 4 天)返回 None。
    """
    row = _day_row(data, _today_index(data, today) + 1)
    if not row:
        return None
    try:
        code = int(row["code"]) if row.get("code") is not None else None
    except (TypeError, ValueError):
        code = None
    pct = row.get("rain_pct")
    try:
        prob = int(float(pct)) if pct is not None else None
    except (TypeError, ValueError):
        prob = None
    if not ((prob is not None and prob >= 40) or (code in RAIN_CODES)):
        return None
    return {"date": str(row.get("date") or ""), "pct": prob, "code": code}


def get_weather(ctx, days: int = 1, place: str = "") -> str:
    """get_weather 工具处理器:今天起 N 天(0-3)的预报文本;place 可切地点。"""
    days = max(0, min(3, int(days or 0)))
    city = _city(place)
    if city is None:
        names = "、".join(p["name"] for p in _places())
        return (f"错误: 没有叫「{place}」的地点。可用地点:{names}"
                "(要增删地点请编辑数据目录里的 weather.json)。")
    data, ts = _cache_entry(city["name"])
    fresh = ts is not None and (time.time() - ts < CACHE_TTL)
    if data is None or not fresh:
        try:
            fetched = _fetch(city)
            _save_entry(city["name"], fetched)
            data = fetched["data"] if isinstance(fetched.get("data"), dict) else fetched
        except Exception:
            return ("错误: 天气服务暂时连不上(校园网外/断网)。"
                    "如实告诉同学查不到天气,凭体感经验给建议,不要编数字。")
    i0 = _today_index(data)
    lines = [describe_day(data, i0, "今天")]
    for k in range(1, days + 1):
        row = describe_day(data, i0 + k, f"第 {k} 天后({str((data.get('daily') or {}).get('time') or [''])[i0 + k][5:]})")
        if row:
            lines.append(row)
    cur = data.get("current") or {}
    try:
        lines[0] += f"(现在 {round(float(cur['temperature_2m']))}℃" + (
            f",体感 {round(float(cur['apparent_temperature']))}℃"
            if cur.get("apparent_temperature") is not None else "") + ")"
    except (KeyError, TypeError, ValueError):
        pass
    tip = umbrella_tip(data)
    out = " · ".join(x for x in lines if x)
    if tip:
        out += f"。建议:{tip}"
    where = "" if city["name"] == _places()[0]["name"] else f"({city['label']})"
    return out + f"{where}。(数据:Open-Meteo,仅供参考)"
