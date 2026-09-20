from minicode.tools.registry import build_default_registry


def test_registry_contents():
    tools = build_default_registry()
    names = [t.name for t in tools]
    assert names == ["read_file", "write_file", "edit_file", "bash", "py_script",
                     "glob", "grep", "web_crawl", "web_search",
                     "browser_open", "browser_eval", "browser_snapshot",
                     "browser_click", "browser_fill", "browser_screenshot",
                     "browser_close", "browser_status",
                     "use_skill", "plan",
                     "search_knowledge", "campus_calendar", "gpa_calc",
                     "kb_suggest", "library_search", "library_guide", "memory",
                     "reminder", "schedule", "get_weather",
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
        "py_script": False,          # 内置运行时执行脚本:能读写文件,要确认
        "glob": True,
        "grep": True,
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
        "use_skill": True,
        "search_knowledge": True,
        "campus_calendar": True,      # 校历查询:只读本地数据文件
        "gpa_calc": True,             # 绩点计算:纯计算,不落盘
        "kb_suggest": False,          # 写候选文件:确认框就是"用户同意存入"那一步
        "library_search": True,      # 离线书目库查询,只读
        "library_guide": True,       # 图书馆导览(楼层/空间/流程),只读
        "plan": False,
        "memory": False,
        "reminder": False,           # 待办提醒写本机清单文件,要确认
        "schedule": False,           # 课表存本机文件,同上(保存动作本身即用户授意)
        "get_weather": True,         # 只读天气数据(固定数据源),不驱动任何东西
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
