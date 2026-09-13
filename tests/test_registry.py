from minicode.tools.registry import build_default_registry


def test_registry_contents():
    tools = build_default_registry()
    names = [t.name for t in tools]
    assert names == ["read_file", "write_file", "edit_file", "bash", "glob",
                     "grep", "web_crawl", "use_skill", "plan",
                     "search_knowledge", "library_search", "library_guide", "memory",
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
        "glob": True,
        "grep": True,
        "web_crawl": False,
        "use_skill": True,
        "search_knowledge": True,
        "library_search": True,      # 离线书目库查询,只读
        "library_guide": True,       # 图书馆导览(楼层/空间/流程),只读
        "plan": False,
        "memory": False,
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
