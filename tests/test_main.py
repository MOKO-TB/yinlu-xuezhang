from main import unmanagle_slash_command


def test_unmangles_msys_paths():
    assert unmanagle_slash_command(["D:/git/Git/history"]) == "/history"
    assert unmanagle_slash_command(["C:/Program Files/Git/undo"]) == "/undo"
    assert unmanagle_slash_command(["C:\\Program Files\\Git\\resume"]) == "/resume"


def test_leaves_normal_tasks_alone():
    assert unmanagle_slash_command(["/model"]) == "/model"
    assert unmanagle_slash_command(["修复", "pytest", "报错"]) == "修复 pytest 报错"
    assert unmanagle_slash_command(["运行", "model"]) == "运行 model"
    assert unmanagle_slash_command(["优化", "agent.py", "的性能"]) == "优化 agent.py 的性能"
