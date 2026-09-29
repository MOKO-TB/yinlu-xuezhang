"""校验打包产物里编译进去的 minicode 代码确实是最新版(TARGETS 即当版针)。

PyInstaller 把纯 Python 模块压进 PYZ 归档(嵌在 exe 里),磁盘上看不到 .py 文件,
所以走 PyInstaller 自己的读取器把模块字节码取出来,核对新增符号是否存在。

用法: .venv/Scripts/python.exe scripts/check_frozen_version.py [exe路径]

清单默认对准 **onedir 的 CLI exe**(`dist/引路学长/引路学长-CLI.exe`)。拿单文件版
(`dist/引路学长-单文件版.exe`)核对时,`minicode.web_profile` 那一条必然假红 ——
单文件版是纯桌面形态,入口不 import 网页层,那个模块压根不在它的 PYZ 里。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def _src_version() -> str:
    """版本号从源码里读,别在这份脚本里再抄一遍 —— 抄漏一次就是一句假绿的旧话。"""
    p = Path(__file__).resolve().parent.parent / "minicode" / "__init__.py"
    try:
        m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']',
                      p.read_text(encoding="utf-8"), re.M)
    except OSError:
        return "?"
    return m.group(1) if m else "?"


# 每个模块要求存在的新增符号(v0.9.13:模型清单/导出搜索;v0.9.14:成长闭环/校历/绩点/搜索)
TARGETS: dict[str, tuple[str, ...]] = {
    "minicode.config": ("model_entries", "enabled_models", "sync_model",
                        "_dedupe_models", "_models_from_raw", "PROVIDER_PRESETS",
                        # v0.9.39:两条时限是真的在拦东西(漏收 = 配置文件里的数字没人读)
                        "turn_time_limit_seconds", "subagent_time_limit_seconds",
                        # v0.9.39 B0/B1:回灌开关(漏收 = config.json 里写了没人读)
                        "feedback_recall",
                        # v0.9.40 D2:回合步数上限(漏收 = 配置文件里的数字没人读)
                        "max_steps"),
    "minicode.gui_bridge": ("test_model", "fetch_models", "get_provider_presets",
                            "export_session", "search_sessions",
                            "rate_message", "get_countdowns",
                            "_provider_credentials",
                            # v0.9.22:按模型能力判定看图 + 写盘失败要说清 + 关窗答复
                            "_vision_on", "_persist_config", "set_vision",
                            "close_choice",
                            # v0.9.23:教务课表导入 + 首次运行向导
                            "schedule_import_open", "schedule_import_read",
                            "schedule_import_model", "schedule_import_confirm",
                            "schedule_import_clear", "schedule_import_status",
                            "_jw_flow", "get_onboarding", "dismiss_onboarding",
                            # v0.9.59:成绩导入三步向导(漏收 = 面板按钮点了没反应,
                            # 或读到了成绩却永远写不进 scores.json)
                            "scores_import_open", "scores_import_read",
                            "scores_import_model", "scores_import_confirm",
                            "scores_import_clear", "scores_import_status",
                            "_scores_flow",
                            # v0.9.24:计划面板数据源(只读 + 派发文案)
                            "get_plan",
                            # v0.9.30:「我的数据」面板 + 迁移包(漏收一条 = 装好的包里
                            # 点「数据」没反应;漏 _ask_open_path = 导入第 1 步选不了包)
                            "mydata_inventory", "mydata_export",
                            "mydata_import_pick", "mydata_import_apply",
                            "mydata_forget_import", "mydata_open_dir",
                            "_ask_open_path",
                            # v0.9.32:情绪面板五根线(漏一条 = 点了按钮没反应)
                            "mood_review", "mood_log", "mood_export",
                            "mood_clear", "mood_help",
                            # v0.9.34:右侧「本次会话」面板两根线(漏 get_activity =
                            # 点开一栏空白;漏 reveal_file = 产出行点不动,只能看)
                            "get_activity", "reveal_file",
                            # v0.9.75:课表面板「导出到日历」(漏 export_ics =
                            # 按钮点了没反应,GUI 没有导出入口的老缺口回来了)
                            "export_ics",
                            # v0.9.37:任务监控栏的开合与折叠偏好(漏 get_monitor_pref =
                            # 每次开机都回到默认收起,「记住我的选择」这句成了空话;
                            # 漏 set_monitor_pref = 点段头有反应但下次全忘)
                            "get_monitor_pref", "set_monitor_pref",
                            # v0.9.39 p1:右键粘贴走本机剪贴板(漏 read_clipboard =
                            # 前端那句"桥在不在"的判定永远为假,静默退回浏览器授权条,
                            # 弹条这个 bug 装了包还在);get_skill_body 是同一轮补的
                            # 漏转发(桥里有、门面没接 → 桌面版点技能卡片只有 TypeError)
                            "read_clipboard", "get_skill_body",
                            # v0.9.39 p2:标题栏染色 + 改字(漏 set_chrome = 深色界面顶上
                            # 仍顶着一条白标题栏,用户点名的就是那一条;开关都不通,
                            # 前端只会静默什么也不做)
                            "set_chrome",
                            # v0.9.69:恢复会话的附件标签(漏收 = 切回旧会话时
                            # resumed 载荷永远不带 atts,缩略图一个都不出现)
                            "restore_user_atts"),
    # v0.9.39 p2:标题栏整模块。漏收 = 桥里那句 `from minicode import window_chrome`
    # 在装好的包里 ImportError,而它被前端那个 try/catch 吞掉 → 表现为"改了个寂寞"。
    "minicode.window_chrome": ("apply", "chrome_for", "blend", "colorref",
                               "IMMERSIVE_DARK_MODE", "CAPTION_COLOR",
                               "TEXT_COLOR", "BORDER_COLOR"),
    # v0.9.39 p1:剪贴板整模块。漏收 = 装好的包里右键粘贴永远走不到本机这条,
    # 那条「xx 想要查看复制到剪贴板的文本和图像」的横条照旧弹(功能"看起来在",
    # 只是每次都违和),所以连 bmp_from_dib 一起钉 —— 它是图片那半的唯一出口。
    "minicode.clipboard": ("read_clipboard", "bmp_from_dib", "dib_to_file",
                           "CF_UNICODETEXT", "CF_DIB", "TEXT", "IMAGE",
                           "EMPTY", "UNAVAILABLE"),
    # v0.9.34:活动面板的数据源整模块(漏收 = 装好的包右边那一栏永远"读不出来")
    # v0.9.37:这一栏改叫「任务监控」,多了此刻与时间线两段(漏 timeline =
    # 那一整段消失,用户仍然只能看到五组按类别堆的行,看不到"先做了什么、
    # 哪一步失败了";漏 now_line = 正在跑那一步永远不显示)
    "minicode.activity": ("session_activity", "GROUPS", "FAILED_PREFIXES",
                          "FILE_TOOLS", "URL_TOOLS", "QUERY_TOOLS",
                          "timeline", "now_line", "_turn_split", "_pair_times",
                          "TURN_LIMIT", "TURNS_LIMIT"),
    # v0.9.37:工具口径只有一份真相 —— 聊天里的工具卡片、右侧时间线、实时表三处
    # 都调它。漏收 = 三处措辞各自漂移(时间线里写「读取」,卡片里写「读文件」),
    # 而配对靠的是同一个 summarize 结果,漂了就再也对不上,行行都显示不了耗时。
    # v0.9.39 Z1:这里还多了"这一步成没成"的唯一口径(漏 is_failure = 终端 / 工具卡片 /
    # 监控栏三处又各自写一份前缀判断,而这三处一致是"不能只当装饰"的硬要求)
    "minicode.toolsum": ("summarize", "verb", "short_name", "agent_type",
                         "SUBAGENT_PREFIX_SEP", "FAILED_PREFIXES", "ERROR_PREVIEW",
                         "is_failure", "error_preview"),
    # v0.9.37:折叠偏好整模块(漏收 = 点段头直接抛异常,整栏打不开)
    "minicode.monitor": ("read_pref", "write_pref", "pref_path", "SECTIONS",
                         "DEFAULTS", "PREF_FILE", "_clean_folded"),
    # v0.9.37:实时耗时表(漏 live_rows = 时间线一行时刻都没有;漏 live_abandon =
    # 按了停止还在转圈,那一行永远装作在跑)
    "minicode.gui_ui": ("confirm_tool",  # v0.9.60:label 参数要能传到 handler
                        "live_rows", "live_now", "live_abandon", "_live_begin",
                        "_live_end", "LIVE_CAP",
                        # v0.9.39:一轮总时限到点要说得出声(漏收 = 停下时界面一个字也不显示,
                        # 用户只看到"它不动了",以为卡死)
                        "turn_time_limited",
                        # v0.9.77:失败连击止损要说得出声(同上)
                        "fail_streak_limit",
                        # v0.9.78:成本闸门到点要说得出声(同上)
                        "token_limit_reached"),
    # v0.9.30:迁移包整模块是新文件,漏收 = 装好的包连面板都打不开
    "minicode.mydata": ("inventory", "pack_plan", "export_package", "read_package",
                        "import_pick", "import_apply", "forget_preview",
                        "deny_reason", "TABLE", "BACKUP_DIR", "MTIME_TOLERANCE",
                        "FORMAT_VERSION"),
    "minicode.model_probe": ("list_models", "explain_status", "remember"),
    # ---- v0.9.22:安装版可用性(数据目录写探针 + 诊断下发) ----
    "minicode.paths": ("_is_writable", "_local_state_dir", "diagnostics",
                       "STATE_BACKEND"),
    # ---- v0.9.22:托盘 + 关窗确认(整套新模块,漏收 = X 又变成直接退出) ----
    "minicode.tray": ("install", "close_choice", "read_pref", "write_pref",
                      "TrayController", "hide_to_tray", "_ensure_icon",
                      "_ensure_parent",
                      # v0.9.25:桌面提醒(漏收 = 最小化后课前提醒彻底没声)
                      "notify", "notify_status", "read_notify_pref",
                      "write_notify_pref", "notify_enabled", "set_notify",
                      "desktop_notify", "KIND_LABELS", "NOTIFY_PREF",
                      # v0.9.57 A4:类别表本身也要在包里(漏收 = review/gaps
                      # 两类在装好的包里关不掉,set_notify 把它们当未知丢掉)
                      "NOTIFY_KINDS",
                      # v0.9.25 修:Win11 不显示托盘气球,真通道是 WinRT toast
                      # (漏收 = 装好的包又回到"通知永远看不见")
                      "_toast", "_balloon", "register_notify_identity",
                      "_powershell", "AUMID",
                      # v0.9.25 修第二轮:显示名要写在注册表默认值上、图标路径
                      # 必须 file:///(漏收 = 装好的包通知顶着一串英文、没图标)
                      "_file_uri",
                      # v0.9.26:开机自启(漏收 = 「开机自动启动」说了没反应,
                      # 或更坏:建了快捷方式却没读回核对,开机弹一个死掉的自启项)
                      "_run_ps", "startup_path", "startup_present",
                      "startup_status", "set_startup", "launch_target",
                      "STARTUP_NAME", "BACKGROUND_FLAG", "starts_hidden",
                      # 漏收 = 自启时窗口藏不进托盘也没人把它显示回来(隐形进程)
                      "start_in_tray",
                      # v0.9.28:action=log 读看门狗运行日志(漏收 = 「提醒为什么
                      # 没发」在装好的包里永远只能靠猜)
                      "wd_log_tail", "watchdog_log_path"),
    # v0.9.72:通知点击直达整模块(漏收 = 装好的包里点通知没反应、协议注册
    # 不发生、激活监听不起来 —— 用户看到的还是"通知只有字,点了等于没点")
    "minicode.activate": ("KIND_PANELS", "panel_uri", "parse_uri",
                          "register_protocol", "handoff", "start_listener",
                          "pending_uri_arg", "take_pending_panel"),
    "minicode.reasoning": ("reasoning_snapshot", "get_capability",
                           "apply_reasoning_choice"),
    "minicode.session": ("to_markdown", "search_logs", "export_filename",
                         # v0.9.40 D0:跨天重建提示词要真换得掉 messages[0]
                         "refresh_system_prompt",
                         # 跨会话记忆:模型侧工具与 CLI /find 共用这一份取数,
                         # 漏收 = 装好的包里学长仍然记不住上次聊过什么
                         "history_hits", "search_history",
                         # 崩溃/强关留下的悬空 tool_calls:漏收 = 装好的包里恢复一次
                         # 中断过的会话,以后每一轮都被端点 400
                         "repair_tool_pairs"),
    # v0.9.40 D1/D2:压缩保头+尾、工具 schema 进估算(漏收 = 装好的包里这两条退化
    # 回"保尾丢头"和"只数消息"的旧尺子,而界面看不出来)
    "minicode.compaction": ("clip_history", "schema_tokens", "HISTORY_HEAD_CAP"),
    "minicode.skills": ("SKILL_CHANNELS", "describe_channels", "create_skill",
                        # v0.9.63:技能使用账(漏收 = 装好的包里用了技能不记账,
                        # 「哪些技能真有人用」永远没有答案)
                        "log_skill_use", "usage_summary", "USAGE_FILE",
                        # v0.9.69:使用账并进技能面板(漏收 = get_skills 永远
                        # 不带 uses/last,面板上「用过 N 次」一行都不出现)
                        "merge_usage"),
    "minicode.web_profile": ("网页版不开放会话导出", "search_sessions",
                             # v0.9.23:教务导入需要本机浏览器窗口,网页版必须挡掉
                             "教务课表导入",
                             # v0.9.25:桌面提醒开的是本机通知,网页版同样挡掉
                             # (钉住 toast 字样 = 挡话是改道后那版,不是旧气球版)
                             "桌面提醒", "WinRT toast"),
    # ---- v0.9.14:知识库成长闭环 + 校园场景小工具 ----
    # v0.9.21:候选审核面板(list_candidates/review_candidate/_merge_into_kb)
    "minicode.growth": ("log_kb_gap", "save_candidate", "log_feedback", "kb_suggest",
                        "list_candidates", "review_candidate", "_merge_into_kb",
                        "REVIEWED_KB_FILE",
                        # v0.9.39 B0/B1:归一化与时间窗是"是不是同一个词/还在不在窗口里"
                        # 的唯一口径(漏收 = 回灌按自己的规矩另判一套)
                        "normalize_query", "within_days", "FEEDBACK_FILE",
                        # v0.9.57 A4:缺口周报的聚合器(漏收 = 周五那条永远
                        # "这周没有缺口",而界面一切正常)
                        "gap_digest",
                        # v0.9.80:检索命中账与逐条命中率(漏收 = 装好的包里
                        # 评价永远绑不到条目,命中率账本永远是空的)
                        "HITS_FILE", "log_kb_hits", "last_hits_since",
                        "kb_appraisal"),
    # v0.9.39 B0/B1:把已经记下来的信号回头用起来(反复搜空 / 差评避让)。
    # 漏收的表现是"装了包之后,连着搜空不再提示、历史差评不再影响答法",而界面一切正常。
    "minicode.feedback_recall": ("empty_hits", "empty_hits_by_call",
                                 "repeat_empty_queries", "repeat_note",
                                 "recent_low_rated", "match_low_rated",
                                 "avoidance_hint", "REPEAT_ASK_N", "MARK_FROM",
                                 "MATCH_DAYS"),
    "minicode.calendar": ("campus_calendar", "calendar_payload",
                          "calendar_prompt_section",
                          # v0.9.32:「每年重复」事件(漏收 = 心理普查那类年度事件不再投影)
                          "is_yearly", "expand_repeats", "_shift_year"),
    # v0.9.32:情绪日记。HELP_FACTS(给人看)/ HELP_CAVEATS(只对模型说)两段都得在 ——
    # 合并在一个常量里,面板就会把"绝不许编号码"这类内部规矩摆到用户面前。
    "minicode.mood": ("log", "weekly_review", "list_recent", "export_text", "clear_all",
                      "mood_prompt_section", "mood", "load_entries", "save_entries",
                      "CRISIS_WORDS", "HELP_FACTS", "HELP_CAVEATS", "HELP_BLOCK"),
    "minicode.gpa": ("gpa_calc",),
    "minicode.tools.crawl": ("web_search", "_resolve_redirect"),
    # ---- v0.9.15:上下文面板 + py_script ----
    "minicode.tools.shell": ("py_script", "_py_runtime_command"),
    # ---- v0.9.39 #84:注册表按领域拆成 6 份目录(registry.py 只管装配顺序)----
    # 漏收一份 = 装好的包里**那个领域的工具整组消失**,而 build_default_registry()
    # 不报错(它只是拼不出那一段),模型侧表现是"这个能力没有了"。
    "minicode.tools.registry": ("build_default_registry",),
    "minicode.tools.catalog_files": ("tools",),
    "minicode.tools.fs": ("repo_outline", "write_file", "edit_file"),
    "minicode.tools.catalog_web": ("tools",),
    "minicode.tools.catalog_knowledge": ("skills_plan_search", "kb_suggest_tool",
                                         "memory_tool"),
    "minicode.tools.catalog_media": ("tools",),
    "minicode.tools.catalog_campus": ("calendar_gpa", "remind_tools"),
    "minicode.tools.catalog_places": ("library_tools", "map_tools"),
    "minicode.model_catalog": ("_context_breakdown", "vision_forced"),
    # ---- v0.9.17:截止提醒 ----
    "minicode.reminders": ("reminder", "add", "due_soon", "parse_due",
                           # v0.9.29:待办带钟点 → 到点前 20 分钟催(漏收 = 记进去了
                           # 但那条细轮询压根不看它,表现是"到点没反应")
                           "parse_clock", "parse_when", "upcoming_due",
                           "late_due", "DUE_LEAD_MINUTES", "LATE_WINDOW_MINUTES",
                           # v0.9.56:循环待办(漏收 = 「每周/每N天」的规则在装好的包里
                           # 静默失明:到期不滚、finish 不推进,清单一整天停在过期的那班)
                           "parse_repeat", "repeat_days", "next_occurrence",
                           "roll_repeats", "REPEAT_WEEKLY"),
    # ---- v0.9.42:定时任务底座(纯函数 + 账本);v0.9.56 A2 起被 proactive 引用,
    #      必须进包 —— 漏收 = import proactive 直接炸,整个看门狗起不来 ----
    "minicode.jobs": ("JobSpec", "due", "due_jobs", "load_state", "last_run",
                      "runs_of", "mark_run", "log_outcome", "STATE_FILE",
                      # v0.9.79:定时任务可观测(漏收 = 装好的包里「这周任务
                      # 跑没跑、为什么没跑」永远答不上来)
                      "snapshot", "rhythm_text"),
    # ---- v0.9.58:.ics 兜底导出(漏收 = 「把课表导进系统日历」在装好的包里
    #      做不到,而对话里什么错都不报——工具压根没注册) ----
    "minicode.ics_export": ("collect", "render", "export", "export_ics_tool",
                            "ICS_FILE_NAME", "_week_runs"),
    # ---- v0.9.59:成绩单存档与导入(漏收 = 「导入成绩」按钮走了个寂寞,
    #      或 scores 工具在包里不存在,绩点永远要手贴) ----
    "minicode.scores": ("load", "save", "clear", "view", "courses_for_gpa",
                        "parse_score_table", "parse_scores_with_model",
                        "scores_tool", "SCORES_FILE"),
    "minicode.scores_import": ("ScoresFlow",),
    # ---- v0.9.18:注入防御(provenance 是函数内 import,必须核它真进包了) ----
    "minicode.provenance": ("wrap", "strip_marks", "is_external",
                            "contains_external", "log_escalation"),
    "minicode.sandbox": ("SECRET", "is_credential_filename",
                         "command_hits_credential", "command_hits_network",
                         # v0.9.39:沙箱第四层(脚本内容级检测)+ 子进程密钥剥离。
                         # 漏收 = 确认框又只看命令字面,`python 一个偷偷读密钥的脚本`
                         # 一路放行(表现是"没弹框就跑了",而不是报错,所以必须有这根针)
                         "scan_script_text", "script_risk_note", "script_hits",
                         "scrub_env", "KNOWN_LIMITS", "SCRIPT_CREDENTIAL_PATTERNS",
                         "SCRIPT_ESCAPE_PATTERNS", "SCRIPT_EGRESS_PATTERNS",
                         "command_touches_outside_path"),
    "minicode.permission": ("note_external", "_is_sensitive_action",
                            "_credential_hit", "_same_site"),
    "minicode.kb": ("crawl_credibility", "host_is_trusted",
                    "CRAWLED_UNVERIFIED",
                    # v0.9.39 B0:"没查到"这句话与判它的那一处必须同时在包里 ——
                    # 只收一个,回灌就静默失灵(界面照旧,只是再也不提示)
                    "NO_HIT_TEXT", "is_no_hit"),
    # ---- v0.9.18:课表持久化 + 天气 chip ----
    "minicode.schedule": ("parse_weeks", "classes_on", "next_class_note",
                          "schedule_prompt_section",
                          # v0.9.19:面板视图 + 课前到点提醒
                          "view", "upcoming_class", "_parse_hm",
                          # v0.9.24:覆盖前自动比对 + 换回上一版
                          "compare", "undo", "describe_changes", "prev_path",
                          "COMPARE_FIELDS",
                          # v0.9.26:开机补发"今天已开课却没提醒过"的那节课
                          # (漏收 = 8:50 开机只补简报,错过的课永远没人提)
                          "_note_key", "LATE_WINDOW_MINUTES", "late_class_note",
                          # v0.9.27:空闲时段 + 本机作息表(漏收 = 面板「填作息表」点了
                          # 没反应、问"我什么时候有空"退回模型心算,口径全丢)
                          "PERIOD_TIMES_FILE", "load_period_times",
                          "save_period_times", "_parse_periods", "_row_periods",
                          "free_slots", "week_gaps", "format_week_gaps",
                          "format_free_slots", "_week_overlay",
                          # v0.9.35:走堂预警接进工具/提示词/面板(漏收 = 装好的包
                          # 里 walk 模块在但没人调,问"赶不赶得及"仍靠模型心算)
                          "risk 的 when", "upcoming_lines", "walk_index_ready",
                          # v0.9.58:课起止钟点的公开口(.ics 导出/后续面板共用;
                          # 漏收 = 导出的日历里课全降级成全天事件)
                          "row_clock",
                          # v0.9.36:面板的处数与"本周最多那天"由后端一次算好(漏收 =
                          # 徽章回退成列表长度、周视图那句汇总干脆不出现)
                          "walk_total", "walk_week",
                          "walk_skipped"),
    "minicode.weather": ("weather_card", "umbrella_tip", "refresh_async",
                         "get_weather"),
    # ---- v0.9.19:拖拽上传(字节落盘,绕开 WebView2 拿不到本地路径) ----
    # v0.9.74:对话型 slash 命令表(漏收 = 装好的包里 / 面板只有动作没有命令)
    "minicode.slash_commands": ("PROMPT_COMMANDS", "expand", "has_arg"),
    "minicode.attachments": ("save_dropped_bytes", "unique_target",
                             # v0.9.69:恢复会话的附件标签(漏收 = 旧会话恢复后
                             # 附件又只剩被剥掉的字面,缩略图永远不出现)
                             "parse_attachment_note"),
    # ---- v0.9.35:走堂换楼预警整模块(漏收 = 装好的包里"要不要早点出发"
    #      全退回模型心算,分钟数/no_gap 口径全丢)----
    "minicode.walk": ("day_risks", "week_risks", "format_risks", "format_week_risks",
                      "upcoming_lines", "worst_item", "item_text", "place_of",
                      "adjacency", "campus_index", "_index", "_walks", "_clashes",
                      # v0.9.36:走堂第二步 —— 真实处数、出门前那一句、整周汇总
                      # (漏收 = 简报与周视图那两句退化成"列表有几行报几处")
                      "total_items", "brief_phrase", "week_summary",
                      "MAX_ITEMS", "MAX_SKIPPED", "_NO_INDEX"),
    # ---- v0.9.20:今日简报聚合 + 推送去重 ----
    "minicode.briefing": ("build", "_schedule_bits", "_weather_bits",
                          "_reminders_bits", "_calendar_bits",
                          # v0.9.36:早上的桌面通知要说得出"最早那一处是哪两栋"
                          "brief_phrase"),
    # ---- v0.9.39(refactor 分支):检索/外接能力/领域路由三批新模块 ----
    # 漏收的后果各不相同,但都属"装好的包里功能静默退化":
    # retrieval 不在 = kb 退回朴素关键词匹配(BM25F/RRF/元数据过滤全丢,还照旧报"已按相关度排序");
    # embed 不在 = 语义档永远起不来;
    # router 不在 = task 的 auto 与四个领域档一起消失(只剩 explore/implement/review)。
    "minicode.retrieval": ("tokenize", "KeywordIndex", "fields_of", "rrf",
                           "order_by_fused", "cosine", "rank_by_vector",
                           "filter_meta", "reason_line", "Hit", "RRF_K",
                           "FIELD_WEIGHTS"),
    "minicode.embed": ("detect", "embed_texts", "ensure_vectors", "cache_path",
                       "load_cache", "save_cache", "entry_key", "entry_text",
                       "EmbedUnavailable", "CACHE_VERSION"),
    "minicode.ocr": ("recognize", "ocr_image", "ocr_probe", "recognize_system",
                     "recognize_vision", "system_ready", "vision_ready",
                     "normalize_system_text", "SYSTEM_WARN", "VISION_PROMPT",
                     "BackendMissing"),
    "minicode.charts": ("chart_file", "render", "normalize_data", "nice_ticks",
                        "render_bar", "render_line", "render_pie", "ChartError",
                        "CHART_TYPES", "MAX_POINTS", "PIE_MAX_SLICES", "slugify"),
    "minicode.images": ("image_generate", "render_card", "call_api", "probe",
                        "api_targets", "local_ready", "CONFIG_SHAPE",
                        "ImageError", "MAX_DOWNLOAD_BYTES", "MAX_CARD_HEIGHT"),
    "minicode.router": ("DOMAINS", "DomainSpec", "classify", "best", "resolve_type",
                        "extra_prompt", "hit_line", "skills_of", "Match"),
    # v0.9.39:一轮总时限是真的在拦东西(漏收 = 配置文件里的数字没人读)
    "minicode.agent": ("run_turn", "turn_time_limited",
                       # v0.9.39 B0/B1:两个挂钩 + 那条"只活在一次请求里"的先验
                       "_messages_for_request", "_note_low_rated_prior",
                       "_mark_repeat_empty",
                       # v0.9.40 D0/D2:跨天重建 + 撞上限后的收尾调用
                       "_refresh_clock", "_wrap_up",
                       # v0.9.77:失败连击止损(漏收 = 装好的包里同一个调用
                       # 连挂 N 次只会烧 token 到 max_steps/整轮时限)
                       "FAIL_STREAK_LIMIT", "FAIL_STREAK_NOTE", "_fail_streak_hit",
                       # v0.9.78:成本闸门(漏收 = config 里的上限在包里没人读,
                       # 跑飞了还是只能等时间限)
                       "TOKEN_LIMIT_NOTE", "session_token_limit"),
    "minicode.subagents": ("type_spec", "_await", "_timeout_note", "TYPE_KEYS",
                           "DOMAIN_KEYS",
                           # v0.9.60:批量放开写类型的两根支柱(漏收 = 混合批次
                           # 静默退回"只读才并行",而工具描述还在许诺能并行写)
                           "MAX_PARALLEL_TASKS", "_TaggedUI",
                           # v0.9.76:产出尾注(漏收 = 子代理写过的文件在监控栏
                           # 「产出」永远隐形 —— 并行写是卖点,产出却看不见)
                           "_files_note"),
    # v0.9.39:确认框要能讲清新工具的代价(漏收 = 弹了框但没说图会被发出去/文件落在哪)
    "minicode.explain": ("risk_notes", "permission_why", "action_reason"),
    # ---- v0.9.16/17/20:主动推荐 + 新版本检查 + 今日简报(合并成一个键,
    #      避免 dict 重复键 last-wins 让早先的 needle 悄悄漏检) ----
    "minicode.proactive": ("check_once", "fetch_latest", "start_loop", "clean_title",
                           "check_update", "push_briefing", "start_tick_loop",
                           "calendar_alerts",
                           # v0.9.25:同一份推送同步发到桌面
                           "KIND_TITLES", "desktop_text", "push_desktop",
                           # v0.9.26:去重集落盘 + 开机补发(漏收 = 重启后
                           # "错过的那条永远不补、弹过的那条再弹一次")
                           "PUSHED_FILE", "pushed_path", "load_pushed",
                           "was_pushed", "mark_pushed",
                           "briefing_pushed_today", "catch_up",
                           # v0.9.28:校历/待办/今天的课 也走按天账本 + 看门狗运行日志
                           # (漏收 = 一天内重启就重复弹,且用户说"提醒没发"时
                           #  action=log 读出来永远是"还没有记录")
                           "_push_once", "_round", "_push_calendar",
                           "_push_reminders", "_push_today_class", "_note_payload",
                           # v0.9.29:课前那条细轮询泛化成"到点提醒器"(课前 + 待办)
                           "_push_class", "_push_due", "_tick",
                           # v0.9.56:定时任务表接线(漏收 = A4 的周期任务在装好的
                           # 包里永远没人跑,而界面一切正常)
                           "JOB_SPECS", "JOB_HANDLERS", "_run_due_jobs",
                           # v0.9.57 A4:两个真任务(漏收 = 周五周日什么都不发生,
                           # 任务表在装好的包里退回装饰品)
                           "_job_weekly_review", "_job_gap_digest",
                           "WATCHDOG_LOG", "watchdog_log_path",
                           "wd_log", "wd_log_tail"),
    # ---- v0.9.23:教务课表导入(整套新模块,漏收 = 点「导入课表」直接报错) ----
    "minicode.schedule_import": ("ImportFlow", "parse_grid", "summarize",
                                 "clear_login_state", "GRID_JS", "_norm_periods",
                                 "MODEL_SYSTEM",
                                 # v0.9.24:通用取材 + 「先认版式再挑文法」那条路
                                 "_parse_any", "page"),
    # ---- v0.9.24:结构化解析从"只对着强智"泛化成独立一层(漏收 = 向导和 page_parse 都炸) ----
    "minicode.site_parse": ("parse_page", "parse_qiangzhi_grid", "parse_generic_grid",
                            "_entry_generic", "_entry_qiangzhi", "_weekday_header",
                            "TABLE_JS", "page_parse", "_render_tool_result", "ROWS_CAP"),
    # ---- v0.9.23:首次运行向导 + 数据目录可视化 ----
    "minicode.onboarding": ("payload", "dismiss", "read", "STEPS", "PREF_FILE",
                            "reset"),
    # ---- v0.9.23:按帧读取 + 可见窗口(教务课表走的就是这几个能力) ----
    "minicode.browser": ("adopt_page", "page_frames", "eval_in_frame",
                         "read_frames", "ensure_visible", "_endpoint_port",
                         "FRAME_MAX_CHARS"),
    # ---- v0.9.24:MCP 三种传输(stdio 之外只有网址的服务器也连得上) ----
    # v0.9.62:校历补录助手(漏收 = 「贴通知录考试周」在包里做不到,
    # 考试周数据继续空置,A5 考试闭环继续卡着)
    "minicode.calendar_edit": ("validate", "preview_text", "commit",
                               "calendar_edit_tool",
                               # v0.9.73:删除与修订(漏收 = 装好的包里录错一条
                               # 校历永远改不了,退回手编 JSON)
                               "remove_preview_text", "remove_commit",
                               "revise_preview_text", "revise_commit",
                               "_resolve_targets", "_backup"),
    "minicode.mcp": ("Transport", "StdioTransport", "HttpTransport", "SseTransport",
                     "make_transport", "_iter_sse", "_safe_url", "_usable_url",
                     "_norm_transport",
                     # v0.9.61:断线重连与自助管理(漏收 = 服务器重启后工具全哑
                     # 到重启应用;「对话里加个 MCP 服务器」在包里做不到)
                     "McpTransportError", "restart", "probe_server",
                     "admin", "admin_tool", "set_admin_hook",
                     # v0.9.70:预设模板库(漏收 = 包里说「没有叫 xx 的预设」,
                     # 对话里加服务器退回手编 JSON)
                     "MCP_PRESETS", "find_preset", "build_preset_config"),
    # ---- v0.9.24:计划面板(漏收 = 顶栏「🗂 计划」点开永远说没计划) ----
    # v0.9.33:对话流里的计划卡片靠 in_progress/running 画出"正在做哪一步"
    # (漏收 = 卡片只剩一堆待办和勾,转圈的那一行永远不出现)
    "minicode.planning": ("plan_state", "dispatch_text", "notify_plan",
                          "in_progress", "running"),
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
    print(f"PASS: 包内后端代码包含 最新版(v{_src_version()})全部新增符号")
    return 0


if __name__ == "__main__":
    sys.exit(main())
