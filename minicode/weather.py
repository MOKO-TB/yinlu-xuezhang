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


def _city() -> dict:
    try:
        raw = json.loads(app_path(CITY_FILE).read_text(encoding="utf-8"))
        lat, lon = float(raw["lat"]), float(raw["lon"])
        return {"lat": lat, "lon": lon,
                "label": str(raw.get("label") or DEFAULT_CITY["label"])}
    except Exception:
        return dict(DEFAULT_CITY)


def _cache_path() -> Path:
    return app_path(CACHE_FILE)


def _load_cache() -> dict | None:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def _save_cache(payload: dict) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"ts": time.time(), "data": payload}, ensure_ascii=False),
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


def refresh_async() -> None:
    """后台线程刷新缓存;同一进程同时只允许一个在途请求。"""
    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True

    def run() -> None:
        global _refreshing
        try:
            _save_cache(_fetch(_city()))
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
    """首页 chip 的数据:只读缓存、绝不当场等网络。

    返回 {icon,text,tip,city,refreshing};缓存完全不存在时**顺手 kick 一次
    后台刷新**并返回 None(前端看到 refreshing 会过几秒再要一次)。
    """
    cached = _load_cache()
    fresh = cached and (time.time() - float(cached.get("ts") or 0) < CACHE_TTL)
    if not fresh:
        refresh_async()
    if not cached:
        return None
    data = cached["data"]
    icon_text = card_text(data, today)
    icon = icon_text.split(" ")[0] if icon_text else "🌡"
    rest = icon_text[len(icon):].strip()
    city = _city()
    ago = "" if fresh else f"(缓存于 {time.strftime('%H:%M', time.localtime(float(cached.get('ts') or 0)))})"
    return {"icon": icon, "text": rest,
            "tip": " · ".join(x for x in (umbrella_tip(data, today),
                                          city.get("label", "") + ago) if x),
            "umbrella": "带伞" in umbrella_tip(data, today),
            "refreshing": bool(not fresh)}


def get_weather(ctx, days: int = 1) -> str:
    """get_weather 工具处理器:今天起 N 天(0-3)的预报文本。"""
    days = max(0, min(3, int(days or 0)))
    cached = _load_cache()
    fresh = cached and (time.time() - float(cached.get("ts") or 0) < CACHE_TTL)
    data = cached["data"] if fresh else None
    if data is None:
        try:
            fetched = _fetch(_city())
            _save_cache(fetched)
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
    return out + "。(数据:Open-Meteo,仅供参考)"
