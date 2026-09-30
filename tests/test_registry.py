from minicode.tools.registry import build_default_registry


def test_registry_contents():
    tools = build_default_registry()
    names = [t.name for t in tools]
    assert names == ["read_file", "write_file", "edit_file", "bash",
                     "bash_status", "bash_kill", "py_script",
                     "glob", "grep", "repo_outline", "web_crawl", "web_search",
                     "browser_open", "browser_eval", "browser_snapshot",
                     "browser_click", "browser_fill", "browser_screenshot",
                     "browser_close", "browser_status", "page_parse",
                     "mcp",
                     "use_skill", "plan",
                     "search_knowledge", "search_history",
                     "ocr_image", "render_chart", "generate_image",
                     "campus_calendar", "calendar_edit", "gpa_calc",
                     "scores",
                     "kb_suggest", "library_search", "library_guide", "memory",
                     "reminder", "mood", "schedule", "desktop_notify", "get_weather", "campus_notices",
                     "export_ics",
                     "map_find_place", "map_show_list", "map_highlight_building",
                     "map_drop_pin", "map_clear"]
    assert len(set(names)) == len(names)


def test_read_only_flags():
    flags = {t.name: t.read_only for t in build_default_registry()}
    assert flags == {
        "read_file": True,
        "write_file": False,
        "edit_file": False,
        "bash": False,
        "bash_status": True,           # 只读:查后台任务的进度与输出尾部,不该弹确认框
        "bash_kill": False,            # 终止进程是动手面,必须用户点头
        "py_script": False,          # 内置运行时执行脚本:能读写文件,要确认
        "glob": True,
        "grep": True,
        "repo_outline": True,        # 只读大纲:不写任何东西
        "web_crawl": False,
        "web_search": False,         # 联网访问搜索引擎:与 web_crawl 同级要确认
        # 浏览器会带用户的登录态、browser_eval 能在页面里执行 JS:要确认,
        # 不标只读(browser_status 只是查能力,不驱动页面)。
        "browser_open": False,
        "browser_eval": False,
        "browser_snapshot": True,     # 只列出页面上有什么,不驱动页面
        "browser_click": False,
        "browser_fill": False,
        "browser_screenshot": False,
        "browser_close": False,
        "browser_status": True,
        "page_parse": True,           # 只把页面上的表读成结构化文字,不写文件不驱动页面
        "use_skill": True,
        "search_knowledge": True,
        # 翻本机历史会话:只读,且只出用户/助手正文,不下发绝对路径
        "search_history": True,
        # 拍错题识别:不写文件,但 vision 后端会把图发给模型 —— 外发由 trust="external" 管
        "ocr_image": True,
        # 图表:要在 charts/ 落一个文件,和 write_file 同级 —— 但它不联网、不读本机数据
        "render_chart": False,
        # 图片生成:api 一条会把描述发给外部服务并花 Key 额度,card 一条往 images/ 写
        # PNG —— 两条都要确认
        "generate_image": False,
        "campus_calendar": True,      # 校历查询:只读本地数据文件
        "gpa_calc": True,             # 绩点计算:纯计算,不落盘
        "kb_suggest": False,          # 写候选文件:确认框就是"用户同意存入"那一步
        "library_search": True,      # 离线书目库查询,只读
        "library_guide": True,       # 图书馆导览(楼层/空间/流程),只读
        "plan": False,
        "memory": False,
        "reminder": False,           # 待办提醒写本机清单文件,要确认
        "mood": False,               # 情绪记录写本机 mood.json:全 app 最私密的一份,
                                     # 每一次落盘都该在确认框里看得见(与 reminder 同级)
        "schedule": False,           # 课表存本机文件,同上(保存动作本身即用户授意)
        "desktop_notify": False,     # 会改提醒开关(写 notify-pref.json)、还能往桌面弹东西:
                                     # 与 schedule/reminder 同级先确认
        "get_weather": True,         # 只读天气数据(固定数据源),不驱动任何东西
        "campus_notices": True,      # 只读现拉官网通知标题,不提交任何东西
        "export_ics": False,         # 往工作区写 .ics 文件:与 write_file 同一层确认
        "scores": False,             # action=clear 删整份存档:非只读,与 mood 同理
        "mcp": False,                # 改配置并重连外部服务:绝不是只读
        "calendar_edit": False,      # 往校历文件追加条目:非只读
        # 地图工具只改界面显示、不动文件:标只读,免得每次定位都弹确认框
        "map_find_place": True,
        "map_show_list": True,
        "map_highlight_building": True,
        "map_drop_pin": True,
        "map_clear": True,
    }


def test_openai_schema_shape():
    for t in build_default_registry():
        schema = t.to_openai_schema()
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == t.name
        assert fn["parameters"]["type"] == "object"
        assert isinstance(fn["parameters"].get("properties"), dict)
