"""技能面板(渠道/添加技能)与用量弹窗滚动 v0.9.12 的桥层测试。

技能面板后端 = skills.describe_channels/create_skill + Bridge 三个方法:
  - get_skills:当前生效的技能清单(带渠道归属)
  - get_skill_channels:三个技能目录的存在状态与技能数
  - add_skill:把用户填的表单落盘成 SKILL.md
"""


from minicode import skills as sk
from minicode.gui_bridge import create_app
from minicode.llm import AssistantTurn
from tests.test_gui_bridge import FakeWindow


# ------------------------------------------------------------ skills.py 单元

def test_describe_channels_lists_three_dirs(tmp_path, monkeypatch):
    from minicode import paths as paths_mod
    monkeypatch.setattr(sk, "app_path", lambda *p: tmp_path / "app" / "skills")
    monkeypatch.setattr(paths_mod, "resource_path",
                        lambda *p: tmp_path / "res" / "skills")
    ws_skills = tmp_path / "ws" / "skills"
    ws_skills.mkdir(parents=True)
    (ws_skills / "mine").mkdir()
    (ws_skills / "mine" / "SKILL.md").write_text("---\nname: mine\n---\n正文",
                                                 encoding="utf-8")
    channels = {c["key"]: c for c in sk.describe_channels(tmp_path / "ws")}
    assert set(channels) == {"builtin", "app", "workspace"}
    assert channels["builtin"]["exists"] is False and channels["builtin"]["count"] == 0
    assert channels["app"]["path"].endswith("skills")
    assert channels["workspace"]["count"] == 1


def test_describe_channels_app_dir_param(tmp_path):
    ws_skills = tmp_path / "ws" / "skills"
    ws_skills.mkdir(parents=True)
    (ws_skills / "a.md").write_text("内容", encoding="utf-8")
    channels = {c["key"]: c for c in sk.describe_channels(
        tmp_path / "ws", app_dir=tmp_path / "bridge-app")}
    assert channels["app"]["path"] == str(tmp_path / "bridge-app" / "skills")
    assert channels["workspace"]["count"] == 1      # 单文件布局也算一个技能


def test_create_skill_writes_app_dir(tmp_path, monkeypatch):
    app_skills = tmp_path / "app" / "skills"
    monkeypatch.setattr(sk, "app_path", lambda *p: app_skills)
    r = sk.create_skill(None, "My Skill!", "一句话简介", "# 步骤", "app")
    assert r["ok"], r
    d = app_skills / "My-Skill"
    text = (d / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: My-Skill\ndescription: 一句话简介\n---\n")
    assert "# 步骤" in text


def test_create_skill_app_dir_param_wins(tmp_path):
    """显式传 app_dir 时以它为准(Bridge 用自己的 app_dir,与全局解耦)。"""
    d = tmp_path / "bridge-app" / "skills"
    r = sk.create_skill(None, "via-bridge", "", "正文", "app",
                        app_dir=tmp_path / "bridge-app")
    assert r["ok"], r
    assert (d / "via-bridge" / "SKILL.md").exists()


def test_create_skill_rejects_builtin_and_dup(tmp_path, monkeypatch):
    app_skills = tmp_path / "app" / "skills"
    monkeypatch.setattr(sk, "app_path", lambda *p: app_skills)
    assert sk.create_skill(None, "x", "", "", "builtin")["ok"] is False
    assert sk.create_skill(None, "x", "", "", "nope")["ok"] is False
    assert sk.create_skill(None, "dup", "", "", "app")["ok"]
    dup = sk.create_skill(None, "dup", "", "", "app")
    assert dup["ok"] is False and "已存在" in dup["msg"]
    # 空名(或全是非法字符清洗成空)也要拒绝
    assert sk.create_skill(None, "///", "", "", "app")["ok"] is False


def test_create_skill_workspace_channel(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    r = sk.create_skill(ws, "proj-only", "项目级", "正文", "workspace")
    assert r["ok"], r
    assert (ws / "skills" / "proj-only" / "SKILL.md").exists()


# ------------------------------------------------------------ Bridge 门面

def _app(config, tmp_path):
    """create_app 返回 4 元组(bridge, ui, agent, session);这里只取桥和窗口。"""
    llm = type("L", (), {"stream_chat": lambda self, *a, **k: AssistantTurn(text="")})()
    bridge, ui, _agent, _session = create_app(
        config, tmp_path, llm=llm, sessions_dir=tmp_path / "sessions",
        config_path=tmp_path / "config.json")
    window = FakeWindow()
    ui.attach(window)
    return bridge, window


def test_bridge_get_skills_and_channels(config, tmp_path):
    sk_dir = tmp_path / "skills" / "demo"
    sk_dir.mkdir(parents=True)
    (sk_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 演示技能\n---\n步骤", encoding="utf-8")
    bridge, _w = _app(config, tmp_path)
    skills = bridge.get_skills()
    assert skills["ok"]
    demo = [s for s in skills["skills"] if s["name"] == "demo"]
    assert demo and demo[0]["description"] == "演示技能"
    assert demo[0]["channel"]        # 渠道归属已标注(app/builtin/工作区)

    channels = bridge.get_skill_channels()
    assert channels["ok"]
    keys = {c["key"] for c in channels["channels"]}
    assert keys == {"builtin", "app", "workspace"}
    ws_ch = [c for c in channels["channels"] if c["key"] == "workspace"][0]
    assert ws_ch["count"] == 1       # 工作区渠道能数到 demo


def test_bridge_get_skill_body(config, tmp_path):
    sk_dir = tmp_path / "skills" / "demo"
    sk_dir.mkdir(parents=True)
    (sk_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 演示\n---\n正文内容", encoding="utf-8")
    bridge, _w = _app(config, tmp_path)
    body = bridge.get_skill_body("demo")
    assert body["ok"] and "正文内容" in body["body"]
    assert bridge.get_skill_body("nope")["ok"] is False


def test_bridge_add_skill_persists_and_broadcasts(config, tmp_path):
    """添加的技能要真正落盘到应用渠道(所有工作区共用),而不是只存在内存里。"""
    bridge, _w = _app(config, tmp_path)
    r = bridge.add_skill("note-taking", "记笔记规范", "先列大纲再写", "app")
    assert r["ok"], r
    from minicode.skills import app_skills_dir
    skill_dir = app_skills_dir(bridge.app_dir) / "note-taking"
    assert (skill_dir / "SKILL.md").exists()
    assert "先列大纲再写" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")


def test_bridge_add_skill_broadcasts_event(config, tmp_path):
    bridge, window = _app(config, tmp_path)
    r = bridge.add_skill("note-taking", "记笔记规范", "先列大纲再写", "app")
    assert r["ok"], r
    assert window.of_type("skills_changed"), "添加成功要广播 skills_changed"
    assert window.of_type("info"), "并给用户一条可读提示"
    # 失败路径:同名重复不落盘、不广播
    r2 = bridge.add_skill("note-taking", "", "", "app")
    assert r2["ok"] is False
    assert len(window.of_type("skills_changed")) == 1


def test_add_skill_new_task_picks_it_up(config, tmp_path, monkeypatch):
    """用户故事的关键闭环:添加技能后,新开的任务(会话)系统提示词里要出现它。

    生产里 Bridge.app_dir 就是全局 APP_DIR(exe 旁),这里把 paths.APP_DIR
    一并钉到同一处,让 skill_dirs 的加载路径与 add_skill 的落盘路径一致。
    """
    from minicode import paths as paths_mod
    monkeypatch.setattr(paths_mod, "APP_DIR", tmp_path / "app-dir")
    bridge, _w = _app(config, tmp_path)
    assert bridge.add_skill("fresh", "新技能", "正文", "app")["ok"]
    runner = bridge.open_session()      # 模拟「新建任务」
    assert "fresh —— 新技能" in runner.session.messages[0]["content"]
