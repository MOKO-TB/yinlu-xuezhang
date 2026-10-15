"""`CHANGELOG.md` 是 git log 的一个视图 —— 视图不能和源分家(v0.9.39 R 批 #86)。

这一轮补 CHANGELOG 时最容易写成"我凭印象总结一下这几版",那正是 HANDOFF 里骂过
很多次的装饰:手抄的变更日志一定会漂,而且漂了没人看得见。所以生成逻辑放在
`scripts/gen_changelog.py`,这里钉两件事:

1. 文件内容 == 脚本从**它尾注里那个提交**生成出来的内容(内容被手改过 = 红;
   比 HEAD 落后不算红,尾注写清收到哪儿了 —— 理由见下面第一条测试的 docstring)。
2. 生成器的判定本身咬得住(喂假日志:没版本标记的、一行两个版本的、顺序)。
"""
import importlib.util
import re
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

def test_changelog_matches_the_commit_it_says_it_was_made_from():
    """文件必须与**它自己尾注里那个提交**对得上;git 不在就抛错,不许静默跳过。

    为什么不是"与 HEAD 对得上":任何标题里点了版本号的提交,提交完成后都会让
    刚生成的那份 CHANGELOG 少它自己那一行 —— 那条断言永远红。尾注写清"收到哪个
    提交为止",落后就不是漂(跑一次脚本即推到最新),而内容被手改过仍然会红。
    """
    m = re.search(r"生成自 git 提交 ([0-9a-f]+)", CHANGELOG)
    assert m, "CHANGELOG.md 少了「生成自 git 提交 …」尾注"
    assert CHANGELOG == gen.generate(m.group(1)), \
        f"CHANGELOG.md 与它记的提交 {m.group(1)} 对不上:跑 `python scripts/gen_changelog.py`"


def test_regenerating_from_head_picks_up_newer_commits():
    """反向钉一下:重跑到 HEAD 时,尾注必须跟着走到 HEAD(否则"落后"永远查不出来)。"""
    head = gen.short_sha()
    assert gen.generate(head).endswith(f"生成自 git 提交 {head};此后的提交还没收进来 -->\n")


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


# ---------------- 3. 版本号空洞必须说出来 ----------------

def test_a_hole_in_the_version_numbers_gets_a_bridge_note():
    """v0.9.163~171 那种"九版没单独成段"要写出来,不能凭空消失。

    这九版当时在工作树里写完一直没提交,最后由一笔「v0.9.172 收编」带走,所以生成器
    扫不到它们 —— 而 CHANGELOG 是给用户看的升级史,一份从 162 直接跳到 172 的索引
    读起来像那九版没发生过。桥接句挂在**收编它们的那一段**下面(代码确实在那里)。
    """
    text = gen.render(gen.entries([
        "2026-10-14\tv0.9.172 收编:工作树里那批没提交的",
        "2026-10-11\tv0.9.162 文档税",
    ]))
    assert "v0.9.163~171 这 9 个版本号没有单独成段" in text
    assert text.index("v0.9.163~171") < text.index("## v0.9.162"), \
        "桥接句必须挂在收编那一段里,不是挂在被跳过的那一侧"
    assert "milestones.md" in text, "要给出处:逐版内容在哪能查到"


def test_contiguous_versions_and_minor_jumps_get_no_note():
    """挨着的版本号不许冒这句(噪声);跨 minor 也不算洞(那是换代不是漏收)。"""
    tight = gen.render(gen.entries([
        "2026-10-14\tv0.9.174 三件", "2026-10-14\tv0.9.173 四条",
        "2026-10-13\tv0.9.172 收编"]))
    assert "没有单独成段" not in tight, "连着三段也报洞 = 这句会没人信"
    jump = gen.render(gen.entries(["2026-09-12\tv0.9.01 起步",
                                   "2026-09-10\tv0.3.0 初始提交"]))
    assert "没有单独成段" not in jump, "跨 minor/跨大版本是换代,不是漏收"


def test_the_real_changelog_says_the_nine_version_hole_out_loud():
    """真实历史里那个洞必须被标出来 —— 这条红就说明桥接逻辑对不上真数据。"""
    assert "v0.9.163~171 这 9 个版本号没有单独成段" in CHANGELOG, \
        "CHANGELOG 从 v0.9.162 直接跳到 v0.9.172 而没说为什么"
