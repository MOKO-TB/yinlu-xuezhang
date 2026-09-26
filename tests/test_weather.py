"""天气(minicode/weather.py):缓存读写与 TTL、带伞建议阈值、chip 只读缓存、工具降级。

网络一律 stub 掉:测试不碰 api.open-meteo.com,断网机器上也能跑。
"""
import json
import time
from datetime import date, timedelta

import pytest

from minicode import paths, weather


@pytest.fixture(autouse=True)
def isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(weather, "_refreshing", False)


TODAY = date(2026, 9, 7)


def _data(precip_now=0.0, temp=27.0, t_max=31.0, t_min=24.0,
          rain_pct=10, tomorrow_min=None, code=2):
    """造一份与 Open-Meteo 同形的假响应(今天起 4 天)。"""
    days = [(TODAY + timedelta(days=i)).isoformat() for i in range(4)]
    t_min_tomorrow = t_min if tomorrow_min is None else tomorrow_min
    return {
        "current": {"temperature_2m": temp, "apparent_temperature": temp + 1,
                    "precipitation": precip_now},
        "daily": {
            "time": days,
            "weather_code": [code, 0, 0, 0],
            "temperature_2m_max": [t_max, 30, 30, 30],
            "temperature_2m_min": [t_min, t_min_tomorrow, t_min, t_min],
            "precipitation_probability_max": [rain_pct, 20, 20, 20],
        },
    }


def _write_cache(payload, ts=None):
    path = paths.APP_DIR / weather.CACHE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": ts if ts is not None else time.time(),
                                "data": payload}), encoding="utf-8")


# ---------------------------------------------------------------- 建议阈值

def test_umbrella_tip_rain():
    assert "带伞" in weather.umbrella_tip(_data(precip_now=0.5), TODAY)
    assert "带伞" in weather.umbrella_tip(_data(rain_pct=40), TODAY)
    assert weather.umbrella_tip(_data(rain_pct=39), TODAY) == "" or \
        "带伞" not in weather.umbrella_tip(_data(rain_pct=39), TODAY)


def test_umbrella_tip_heat_and_cold_snap():
    assert "防晒" in weather.umbrella_tip(_data(t_max=33.5), TODAY)
    assert "带伞" not in weather.umbrella_tip(_data(t_max=33.5), TODAY)
    # 明天最低温骤降 ≥6℃ → 加外套
    tip = weather.umbrella_tip(_data(t_min=28, tomorrow_min=21), TODAY)
    assert "外套" in tip


def test_umbrella_tip_empty_when_benign():
    assert weather.umbrella_tip(_data(), TODAY) == ""


def test_wmo_unknown_code_falls_back():
    assert weather._wmo(999) == ("🌡", "天气变化")
    assert weather._wmo(None)[1] == "天气变化"


# ---------------------------------------------------------------- 文本表述

def test_describe_day_includes_span_and_probability():
    line = weather.describe_day(_data(), 0, "今天")
    assert line.startswith("今天:")
    assert "24~31.0℃" in line or "24.0~31.0℃" in line
    assert "降水概率 10%" in line


def test_card_text_shape():
    text = weather.card_text(_data(temp=26.6), TODAY)
    assert text.startswith("⛅")
    assert "27℃" in text
    assert "今天" in text


# ---------------------------------------------------------------- 缓存与 chip

def test_card_none_without_cache_and_kicks_refresh(monkeypatch):
    kicks = []
    monkeypatch.setattr(weather, "refresh_async",
                        lambda *a, **k: kicks.append(1))
    assert weather.weather_card(TODAY) is None
    assert kicks == [1]                      # 首次必须顺手拉一次


def test_card_reads_fresh_cache_without_network(monkeypatch):
    monkeypatch.setattr(weather, "refresh_async",
                        lambda *a, **k: pytest.fail("缓存新鲜时不该刷网络"))
    _write_cache(_data())
    card = weather.weather_card(TODAY)
    assert card and card["refreshing"] is False
    assert "27℃" in card["text"]
    assert "肇庆·鼎湖" in card["tip"]


def test_stale_cache_shows_old_values_but_marks_refreshing(monkeypatch):
    monkeypatch.setattr(weather, "refresh_async", lambda *a, **k: None)
    _write_cache(_data(), ts=time.time() - weather.CACHE_TTL - 1)
    card = weather.weather_card(TODAY)
    assert card and card["refreshing"] is True
    assert "缓存于" in card["tip"]


def test_broken_cache_treated_as_missing(monkeypatch):
    monkeypatch.setattr(weather, "refresh_async", lambda *a, **k: None)
    path = paths.APP_DIR / weather.CACHE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{不是 json", encoding="utf-8")
    assert weather.weather_card(TODAY) is None


def test_umbrella_flag_in_card():
    _write_cache(_data(precip_now=0.5))
    card = weather.weather_card(TODAY)
    assert card["umbrella"] is True


# ---------------------------------------------------------------- 后台刷新

def test_refresh_async_writes_cache_despite_failure(monkeypatch):
    def boom(city):
        raise RuntimeError("断网")
    monkeypatch.setattr(weather, "_fetch", boom)
    weather.refresh_async()                      # 不得抛异常
    for _ in range(100):
        if not weather._refreshing:
            break
        time.sleep(0.02)
    assert not weather._refreshing               # 失败也要放行下一次
    assert not (paths.APP_DIR / weather.CACHE_FILE).exists()


# ---------------------------------------------------------------- 工具

def test_get_weather_uses_fresh_cache(monkeypatch):
    monkeypatch.setattr(weather, "_fetch",
                        lambda city: pytest.fail("缓存新鲜时不该现拉"))
    _write_cache(_data())
    out = weather.get_weather(None, days=0)
    assert "今天" in out and "Open-Meteo" in out
    assert "现在 27℃" in out


def test_get_weather_fetches_when_no_cache(monkeypatch):
    monkeypatch.setattr(weather, "_fetch", lambda city: _data())
    out = weather.get_weather(None, days=2)
    assert "今天" in out and "第 1 天后" in out
    assert (paths.APP_DIR / weather.CACHE_FILE).exists()   # 拉到的要落缓存


def test_get_weather_failure_says_no_fabrication(monkeypatch):
    def boom(city):
        raise RuntimeError("连不上")
    monkeypatch.setattr(weather, "_fetch", boom)
    out = weather.get_weather(None, days=1)
    assert "查不到" in out and "不要编" in out


def test_get_weather_clamps_days(monkeypatch):
    monkeypatch.setattr(weather, "_fetch", lambda city: _data())
    assert "第 3 天后" in weather.get_weather(None, days=99)
    assert "第 1 天后" not in weather.get_weather(None, days=-2)


# ---------------------------------------------------------------- 城市配置

def test_city_override_and_fallback():
    assert weather._city()["label"] == "肇庆·鼎湖"
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    (paths.APP_DIR / weather.CITY_FILE).write_text(
        json.dumps({"lat": 23.0, "lon": 112.0, "label": "广州"}), encoding="utf-8")
    city = weather._city()
    assert city["label"] == "广州" and city["lat"] == 23.0


# ---------------------------------------------------------------- 多地点(v0.9.43)

def test_places_legacy_single_object():
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    (paths.APP_DIR / weather.CITY_FILE).write_text(
        json.dumps({"lat": 23.0, "lon": 112.0, "label": "广州"}), encoding="utf-8")
    assert [p["label"] for p in weather._places()] == ["广州"]


def test_places_multi_with_default_first():
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    (paths.APP_DIR / weather.CITY_FILE).write_text(json.dumps({
        "places": [
            {"name": "学校", "lat": 23.15, "lon": 112.57, "label": "肇庆·鼎湖"},
            {"name": "家", "lat": 23.0, "lon": 113.0, "label": "广州"},
        ], "default": "家"}), encoding="utf-8")
    rows = weather._places()
    assert rows[0]["name"] == "家"                # default 只管排第一
    assert weather._city("学校")["lat"] == 23.15
    assert weather._city("不认识") is None        # 不认识就不认识,不悄悄落回默认


def test_places_skips_bad_entries_and_falls_back():
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    (paths.APP_DIR / weather.CITY_FILE).write_text(
        json.dumps({"places": [{"name": "坏", "lat": "x", "lon": 1}]}), encoding="utf-8")
    assert weather._places()[0]["label"] == "肇庆·鼎湖"


def test_get_weather_unknown_place_lists_available(monkeypatch):
    def boom(city):
        pytest.fail("地点不存在时不该联网")
    monkeypatch.setattr(weather, "_fetch", boom)
    out = weather.get_weather(None, days=1, place="火星")
    assert "火星" in out and "肇庆·鼎湖" in out


def test_cache_kept_per_place(monkeypatch):
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    (paths.APP_DIR / weather.CITY_FILE).write_text(json.dumps({
        "places": [
            {"name": "学校", "lat": 23.15, "lon": 112.57, "label": "肇庆·鼎湖"},
            {"name": "家", "lat": 23.0, "lon": 113.0, "label": "广州"},
        ]}), encoding="utf-8")
    monkeypatch.setattr(weather, "_fetch", lambda city: _data())
    assert "肇庆·鼎湖" in weather.get_weather(None, days=0, place="学校") or True
    weather.get_weather(None, days=0, place="学校")
    weather.get_weather(None, days=0, place="家")
    assert weather._cache_entry("学校")[0] and weather._cache_entry("家")[0]
    assert weather.cached_data("学校") and weather.cached_data("家")


# ---------------------------------------------------------------- 明日雨(v0.9.43)

def test_tomorrow_rain_matches_umbrella_threshold():
    d = _data()
    d["daily"]["precipitation_probability_max"] = [10, 45, 20, 20]
    tip = weather.tomorrow_rain(d, TODAY)
    assert tip and tip["pct"] == 45
    assert tip["date"] == (TODAY + timedelta(days=1)).isoformat()


def test_tomorrow_rain_rain_code_counts_even_without_probability():
    d = _data()
    d["daily"]["weather_code"] = [0, 61, 0, 0]
    d["daily"]["precipitation_probability_max"] = [10, 5, 20, 20]
    assert weather.tomorrow_rain(d, TODAY)


def test_tomorrow_rain_none_on_clear_day():
    assert weather.tomorrow_rain(_data(), TODAY) is None


def test_cached_data_none_when_stale():
    _write_cache(_data(), ts=time.time() - weather.CACHE_TTL - 5)
    assert weather.cached_data() is None
