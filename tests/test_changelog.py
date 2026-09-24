"""`CHANGELOG.md` 是 git log 的一个视图 —— 视图不能和源分家(v0.9.39 R 批 #86)。

这一轮补 CHANGELOG 时最容易写成"我凭印象总结一下这几版",那正是 HANDOFF 里骂过
很多次的装饰:手抄的变更日志一定会漂,而且漂了没人看得见。所以生成逻辑放在
`scripts/gen_changelog.py`,这里钉两件事:

1. 文件内容 == 脚本现在从 git log 生成出来的内容(改了提交没重生成 = 红)。
2. 生成器的判定本身咬得住(喂假日志:没版本标记的、一行两个版本的、顺序)。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "gen_changelog", ROOT / "scripts" / "gen_changelog.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


# ---------------- 1. 文件与 git log 对得上 ----------------

def test_changelog_file_matches_git_log():
    """防"提交了新的一版,变更日志还停在上一版"。git 不在就抛错,不许静默跳过。"""
    assert CHANGELOG == gen.generate(), \
        "CHANGELOG.md 漂了:跑 `python scripts/gen_changelog.py` 重新生成"


def test_changelog_actually_covers_the_history():
    """防"生成器扫不到东西所以永远绿":一份只写着标题的 CHANGELOG 比没有更骗人。"""
    from minicode import __version__
    sections = [ln for ln in CHANGELOG.splitlines() if ln.startswith("## v")]
    assert len(sections) >= 20, f"CHANGELOG 只有 {len(sections)} 个版本节,不像真的历史"
    keys = [gen.version_of(ln)[0] for ln in sections]
    assert keys == sorted(keys, reverse=True), \
        f"版本节不是从新到旧排的(按数值):{sections[:5]}"
    assert f"## v{__version__}" in CHANGELOG, f"当前版本 v{__version__} 没进 CHANGELOG"
    assert "初始提交" in CHANGELOG, "第一轮提交没被收进来"


# ---------------- 2. 判定咬得住 ----------------

def test_only_versioned_subjects_are_collected():
    """没点版本的过程性提交(文档/跑测/重出包)不进里程碑索引。"""
    lines = ["2026-09-24\tdocs(handoff): 补三条坑",
             "2026-09-24\tchore: 重出包",
             "2026-09-23\tv0.9.37: 右侧「任务监控」栏"]
    got = gen.entries(lines)
    assert [g[1] for g in got] == ["v0.9.37"], "该收的没收或把过程性提交也收了"
    assert "右侧「任务监控」栏" in got[0][2] and "2026-09-23" in got[0][2]


def test_a_subject_naming_two_versions_is_bucked_under_the_first_verbatim():
    """`v0.9.18-v0.9.21: 课表/天气/简报…` 这种一行管四个版本的是真历史。

    挂在第一个版本下、**原文照抄**:区间本来就写在标题里。拆开复制四份是我在编历史。
    """
    key, label, line = gen.entries(
        ["2026-09-20\tv0.9.18-v0.9.21: 课表/天气/简报 + 候选审核面板"])[0]
    assert key == (0, 9, 18) and label == "v0.9.18"
    assert "v0.9.21" in line, "标题原文被动过了"


def test_two_part_version_label_is_not_padded_out():
    """当年有人写 `v0.9`(没有第三段),标题里怎么写这里就怎么标 —— 补成 v0.9.0 是编。"""
    assert gen.version_of("feat: v0.9 —— 网页版") == ((0, 9, 0), "v0.9")
    assert gen.version_of("docs: 没有版本号的一句话") is None


def test_render_orders_versions_new_first_and_keeps_commit_order_inside():
    pairs = gen.entries([
        "2026-09-24\tv0.9.39 p3: 后一条",
        "2026-09-24\tv0.9.39 p1: 先一条",
        "2026-09-23\tv0.9.37: 上一版",
    ])
    text = gen.render(pairs)
    assert text.index("## v0.9.39") < text.index("## v0.9.37")
    body = text.split("## v0.9.39")[1].split("## v0.9.37")[0]
    assert body.index("p3") < body.index("p1"), "同一版内被重排过了"
