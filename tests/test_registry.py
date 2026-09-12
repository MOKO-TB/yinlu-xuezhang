from minicode.tools.registry import build_default_registry


def test_registry_contents():
    tools = build_default_registry()
    names = [t.name for t in tools]
    assert names == ["read_file", "write_file", "edit_file", "bash", "glob",
                     "grep", "web_crawl", "use_skill", "search_knowledge"]
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
    }


def test_openai_schema_shape():
    for t in build_default_registry():
        schema = t.to_openai_schema()
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == t.name
        assert fn["parameters"]["type"] == "object"
        assert isinstance(fn["parameters"].get("properties"), dict)
