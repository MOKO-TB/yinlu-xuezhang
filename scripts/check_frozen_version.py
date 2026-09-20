"""校验打包产物里编译进去的 minicode 代码确实是最新版(当前 v0.9.14)。

PyInstaller 把纯 Python 模块压进 PYZ 归档(嵌在 exe 里),磁盘上看不到 .py 文件,
所以走 PyInstaller 自己的读取器把模块字节码取出来,核对新增符号是否存在。

用法: .venv/Scripts/python.exe scripts/check_frozen_version.py [exe路径]
"""
from __future__ import annotations

import sys
from pathlib import Path

# 每个模块要求存在的新增符号(v0.9.13:模型清单/导出搜索;v0.9.14:成长闭环/校历/绩点/搜索)
TARGETS: dict[str, tuple[str, ...]] = {
    "minicode.config": ("model_entries", "enabled_models", "sync_model",
                        "_dedupe_models", "_models_from_raw", "PROVIDER_PRESETS"),
    "minicode.gui_bridge": ("test_model", "fetch_models", "get_provider_presets",
                            "export_session", "search_sessions",
                            "rate_message", "get_countdowns",
                            "_provider_credentials"),
    "minicode.model_probe": ("list_models", "explain_status", "remember"),
    "minicode.session": ("to_markdown", "search_logs", "export_filename"),
    "minicode.skills": ("SKILL_CHANNELS", "describe_channels", "create_skill"),
    "minicode.web_profile": ("网页版不开放会话导出", "search_sessions"),
    # ---- v0.9.14:知识库成长闭环 + 校园场景小工具 ----
    # v0.9.21:候选审核面板(list_candidates/review_candidate/_merge_into_kb)
    "minicode.growth": ("log_kb_gap", "save_candidate", "log_feedback", "kb_suggest",
                        "list_candidates", "review_candidate", "_merge_into_kb",
                        "REVIEWED_KB_FILE"),
    "minicode.calendar": ("campus_calendar", "calendar_payload",
                          "calendar_prompt_section"),
    "minicode.gpa": ("gpa_calc",),
    "minicode.tools.crawl": ("web_search", "_resolve_redirect"),
    # ---- v0.9.15:上下文面板 + py_script ----
    "minicode.tools.shell": ("py_script", "_py_runtime_command"),
    "minicode.model_catalog": ("_context_breakdown",),
    # ---- v0.9.17:截止提醒 ----
    "minicode.reminders": ("reminder", "add", "due_soon", "parse_due"),
    # ---- v0.9.18:注入防御(provenance 是函数内 import,必须核它真进包了) ----
    "minicode.provenance": ("wrap", "strip_marks", "is_external",
                            "contains_external", "log_escalation"),
    "minicode.sandbox": ("SECRET", "is_credential_filename",
                         "command_hits_credential", "command_hits_network"),
    "minicode.permission": ("note_external", "_is_sensitive_action",
                            "_credential_hit", "_same_site"),
    "minicode.kb": ("crawl_credibility", "host_is_trusted",
                    "CRAWLED_UNVERIFIED"),
    # ---- v0.9.18:课表持久化 + 天气 chip ----
    "minicode.schedule": ("parse_weeks", "classes_on", "next_class_note",
                          "schedule_prompt_section",
                          # v0.9.19:面板视图 + 课前到点提醒
                          "view", "upcoming_class", "_parse_hm"),
    "minicode.weather": ("weather_card", "umbrella_tip", "refresh_async",
                         "get_weather"),
    # ---- v0.9.19:拖拽上传(字节落盘,绕开 WebView2 拿不到本地路径) ----
    "minicode.attachments": ("save_dropped_bytes", "unique_target"),
    # ---- v0.9.20:今日简报聚合 + 推送去重 ----
    "minicode.briefing": ("build", "_schedule_bits", "_weather_bits",
                          "_reminders_bits", "_calendar_bits"),
    # ---- v0.9.16/17/20:主动推荐 + 新版本检查 + 今日简报(合并成一个键,
    #      避免 dict 重复键 last-wins 让早先的 needle 悄悄漏检) ----
    "minicode.proactive": ("check_once", "fetch_latest", "start_loop", "clean_title",
                           "check_update", "push_briefing", "start_class_loop",
                           "calendar_alerts"),
}


def _code_names(code) -> str:
    """把一个 code 对象(含嵌套)里所有标识符/常量拼成一个大字符串。"""
    chunks: list[str] = []
    stack = [code]
    while stack:
        c = stack.pop()
        chunks.append(repr(c.co_names))
        chunks.append(repr(c.co_varnames))
        chunks.append(repr(c.co_consts))
        stack.extend(x for x in c.co_consts if hasattr(x, "co_code"))
    return "\n".join(chunks)


def main() -> int:
    exe = Path(sys.argv[1] if len(sys.argv) > 1 else
               r"dist\引路学长\引路学长-CLI.exe")
    if not exe.exists():
        print(f"找不到产物: {exe}")
        return 2
    from PyInstaller.archive.readers import CArchiveReader

    car = CArchiveReader(str(exe))
    pyz_name = next((n for n in car.toc if n.lower().endswith(".pyz")), None)
    if pyz_name is None:
        print(f"CArchive 里没有 PYZ,条目: {list(car.toc)[:10]}")
        return 2
    pyz = car.open_embedded_archive(pyz_name)     # 已定位好的子归档读取器

    failures: list[str] = []
    for mod, needles in TARGETS.items():
        if mod not in pyz.toc:
            print(f"  ✗ {mod} 不在 PYZ 里")
            failures.append(mod)
            continue
        code = pyz.extract(mod)
        haystack = _code_names(code)
        for needle in needles:
            ok = needle in haystack
            print(("  ✓ " if ok else "  ✗ ") + f"{mod}: {needle}")
            if not ok:
                failures.append(f"{mod}:{needle}")
    print()
    if failures:
        print("FAIL: 包内不是最新版代码 →", failures)
        return 1
    print("PASS: 包内后端代码包含 最新版(v0.9.21)全部新增符号")
    return 0


if __name__ == "__main__":
    sys.exit(main())
