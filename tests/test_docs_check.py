"""`scripts/check_docs.py` 的四条对账要能在 pytest 里跑,不能只靠人记得执行。

上一轮的教训:文档里的测试基线数(1188)和实测(1142)漂了一整轮没人发现,
个人数据文件靠人肉记进清理清单 —— 这一轮加 `watchdog.log` 时才发现
`weather.json`、`web-sessions/`、`web-usage.log` 三份从来没人加过。
把"记得跑一下"变成测试,才是真的会跑。

`--collect` 那一项(跑一次 pytest --collect-only)放在测试里会自引用地慢,
所以测试只覆盖四条静态对账;慢的那两项(跑 pytest --collect-only 对基线数、
让 setuptools 真生成一遍元数据)留在提交前手工跑脚本。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)


def test_handoff_header_matches_source_version():
    assert check_docs.check_version() == [], "HANDOFF 抬头的版本号和源码不是一处真相"


def test_every_local_state_file_is_ignored_and_cleaned():
    """新增本机状态文件却忘了清理清单 = 把同学自己的数据随安装包发出去。"""
    assert check_docs.check_state_targets() == []


def test_state_scan_actually_finds_the_real_files():
    """防"检查器扫不到东西所以永远绿":扫出来的必须包含这几份已知本机状态。

    没有这一条,一个坏掉的正则会让整条数据边界检查变成空转。
    """
    found = check_docs.state_targets()
    for name in ("schedule.json", "reminders.json", "period-times.json",
                 "pushed-today.json", "watchdog.log", "weather.json",
                 "web-sessions", "web-usage.log", "migrate-backup"):
        assert name in found, f"{name} 在代码里写进数据目录,却没被扫出来"
    # **根目录那几个入口也在写数据目录**,扫描集不能只看 `minicode/`:
    # `gui_warnings.log` 是 `gui_main.py` 写的,它因此在包里躺了很久 —— 既没进
    # 不外发清单,也没进「我的数据」,而那道闸当时是绿的。
    assert "gui_warnings.log" in found, "入口脚本写的数据目录文件没被扫到(扫描集缩回去了)"
    assert "gui_main.py" in found["gui_warnings.log"], found["gui_warnings.log"]


def test_state_exemptions_are_all_still_needed():
    """例外清单里躺着一份其实没人写的状态 = 例外成了垃圾桶。"""
    found = check_docs.state_targets()
    stale = [n for n in check_docs.STATE_EXEMPT if n not in found]
    assert not stale, f"{stale} 已不在代码里被写入,例外条目该删"


def test_handoff_milestones_stay_archived():
    """§2 只留最近几轮;超长说明这一轮又忘了归档。"""
    assert check_docs.check_handoff_size() == []


def test_archived_milestones_are_not_silently_dropped():
    """归档是搬走不是删掉:主文档 + 归档文件里必须真有那几张表。"""
    archive = ROOT / "docs" / "handoff" / "milestones.md"
    assert archive.exists(), "§2 说归档在 docs/handoff/,文件却不在"
    text = archive.read_text(encoding="utf-8")
    assert "里程碑" in text
    rows = [ln for ln in text.splitlines() if ln.lstrip().startswith("| v")]
    assert len(rows) >= 40, f"归档里只剩 {len(rows)} 条里程碑,不像搬全了"
    # 真搬走了:归档里最新的那条不该还在主文档 §2 的行首(正文里提到 v0.9.22 是另一回事),
    # 当前版本那条该在。
    section2 = (ROOT / "HANDOFF.md").read_text(encoding="utf-8") \
        .split("## 2.", 1)[1].split("\n## 3.", 1)[0]
    heads = {ln.split()[1] for ln in section2.splitlines() if ln.startswith("| v")}
    newest_archived = rows[0].split()[1]                    # 如 v0.9.22
    assert newest_archived not in heads, f"{newest_archived} 两处都有行首 = 没搬干净"
    assert "v" + check_docs.version_from_source() in heads, "最近一轮被归档掉了"


def test_pyproject_is_a_facade_not_a_second_source():
    """项目元数据必须指向真源;写死了就会漂,而漂掉的版本号比没有更误导人。"""
    assert check_docs.check_pyproject() == []


def test_pyproject_check_bites_on_hardcoded_versions():
    """防"检查器扫不到东西所以永远绿":把写死的版本/依赖喂给它必须报出来。"""
    real = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # ① 抄了一份版本号 → 红
    assert any("写死了" in p for p in
               check_docs.pyproject_problems(real + '\nversion = "0.9.99"\n'))
    # ② 把 dynamic 摘掉 → 红(指向真源的那两条一起报)
    no_dynamic = real.replace('dynamic = ["version", "dependencies"]', "")
    got = check_docs.pyproject_problems(no_dynamic)
    assert any('"version"' in p for p in got) and any('"dependencies"' in p for p in got)
    # ③ 版本源改指到别处(比如指着一个不会更新的常量)→ 红
    assert any("minicode.__version__" in p for p in check_docs.pyproject_problems(
        real.replace('attr = "minicode.__version__"', 'attr = "minicode.__stale__"')))


# ---------------- 测试基线条数(--collect 那一项的判定) ----------------

def test_handoff_claims_exactly_one_baseline_number():
    """文档里"全绿 N 项"这个句式只能有一个数:它是当前基线,不是历史账本。"""
    text = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
    claimed = check_docs.claimed_counts(text)
    assert claimed, "一条基线都没写,提交前没人知道该跑多少"
    assert len(claimed) == 1, f"HANDOFF 用「全绿 N 项」写了多个数:{sorted(claimed)}"


def test_count_check_bites_when_an_old_number_lurks_in_the_doc():
    """钉住上一版的漏洞:旧判定是 `actual in claimed`,文档留着历史数时它会绿着放过。

    真实事故形状:本轮实测 1684,文档某处还写着上一轮的 1660 —— 旧规则里
    1684 ∈ {1684, 1660} 于是过关,漂掉的当前基线没人看得见。
    """
    assert check_docs.count_problems(1684, "跑 pytest -q  # 全绿 1684 项") == []
    # ① 多一个历史数 → 红(旧规则在这里是绿的)
    assert check_docs.count_problems(
        1684, "全绿 1684 项\n上一轮 全绿 1660 项") != [], "历史数冒充当前基线就该红"
    # ② 数写漂了 → 红
    assert check_docs.count_problems(1684, "全绿 1700 项") != []
    # ③ 压根没写 → 红(不能"因为扫不到所以永远绿")
    assert check_docs.count_problems(1684, "测试都跑过了,全绿") != []
    # ④ 只认这一种句式:历史条数写成 passed 形态不该被误当成基线
    assert check_docs.count_problems(1684, "全绿 1684 项(实跑 1683 passed / 1 skipped)") == []


# ---------------------------------------------- 档位与数字口径同步(v0.9.83)

TYPES = ["explore", "implement", "review", "code", "writing", "data",
         "english", "campus", "material", "auto"]
LABELS = {"explore": "侦察", "campus": "校园办事", "material": "文档材料"}
_KEYS = "explore implement review code writing data english campus material"
# 一份"三份活文档都同步好了"的样本:闸要能被喂假数据测,不能只测真仓库
GOOD_DOCS = (f"{_KEYS} 并行 最多 6 个",
             "explore implement review code writing data english 校园办事 文档材料"
             " 子代理 最多 6 个",
             f"skills/ 里那 21 份技能;{_KEYS} 侦察")


def test_doc_sync_passes_on_a_consistent_set():
    assert check_docs.doc_sync_problems(TYPES, LABELS, 6, 21, *GOOD_DOCS) == []


def test_doc_sync_flags_a_bucket_that_skipped_the_docs():
    """加一档却没同步活文档 —— v0.9.68 那次《使用说明》就漏了两个领域档。"""
    readme, prompt, manual = GOOD_DOCS
    problems = check_docs.doc_sync_problems(
        TYPES, LABELS, 6, 21, readme.replace("material", ""), prompt, manual)
    assert any("material" in p and "README.md" in p for p in problems)


def test_doc_sync_accepts_the_chinese_label():
    """文档用中文标签提过就算提到 —— 随包说明书本来就不写英文键名。"""
    assert check_docs.doc_sync_problems(
        ["campus"], {"campus": "校园办事"}, 6, 21,
        "校园办事 并行", "校园办事", "校园办事") == []


def test_doc_sync_flags_a_drifted_skill_count():
    """「skills/ 里那 N 份技能」是总数声明:实测 22 而文档写 21 就该红(v0.9.81 撞过)。"""
    problems = check_docs.doc_sync_problems(TYPES, LABELS, 6, 22, *GOOD_DOCS)
    assert any("22 份" in p for p in problems)


def test_doc_sync_reads_the_arrow_form_as_the_current_number():
    """「上限 4→6」是"从 4 放到了 6",取后一个数;写成「上限 4 个」才是漂了。"""
    readme, prompt, manual = GOOD_DOCS
    assert check_docs.doc_sync_problems(
        TYPES, LABELS, 6, 21, readme.replace("最多 6 个", "上限 4→6"),
        prompt, manual) == []
    stale = check_docs.doc_sync_problems(
        TYPES, LABELS, 6, 21, readme.replace("最多 6 个", "上限 4 个"),
        prompt, manual)
    assert any("上限 4" in p for p in stale)


def test_doc_sync_does_not_mind_unrelated_limits():
    """附件那行的「上限 20MiB」与子代理无关:不含"子代理/并行"的行不归这道闸管。"""
    readme, prompt, manual = GOOD_DOCS
    assert check_docs.doc_sync_problems(
        TYPES, LABELS, 6, 21,
        readme + "\n单文件上限 20MiB、解码后不超过 4000 像素", prompt, manual) == []


def test_real_repo_passes_the_doc_sync_gate():
    """真仓库必须过这道闸,而且《使用说明》区域真抠得到 —— 抠空了就是永远绿的假闸。"""
    manual = check_docs._manual_text()
    assert "份技能" in manual, "《使用说明》区域没抠到 —— 这道闸会空转"
    assert check_docs.check_agent_doc_sync() == []


def test_tool_count_gate_follows_the_registry():
    """HANDOFF §1 的「内置 N 工具」是**当前态**数字:加一只工具就得同步,否则这里红。"""
    assert check_docs.tool_count_problems(
        49, [("HANDOFF §1", "已完成:Agent 主循环 + 内置 49 工具(...)")]) == []
    drift = check_docs.tool_count_problems(
        49, [("HANDOFF §1", "已完成:内置 47 工具")])
    assert any("47" in p and "49" in p for p in drift), drift


def test_tool_count_gate_reads_both_word_orders():
    """HANDOFF 写「内置 56 工具」,README 写「56 个内置工具」——只认一种词序,
    另一种就是漏网的那处(v0.9.105 实测 README 正停在 55 而注册表已经是 56)。"""
    ok = check_docs.tool_count_problems(56, [("README", "- **56 个内置工具**(...)")])
    assert ok == [], ok
    drift = check_docs.tool_count_problems(
        56, [("README", "- **55 个内置工具**(实测)")])
    assert any("55" in p and "56" in p for p in drift), drift
    # 出处要写进话里,不然维护者不知道去哪一句改
    assert "README" in drift[0]


def test_tool_count_only_reads_section_one():
    """§2 及以后的历史数字不许被改(那是当时的实情),所以子规则只读 §1 那一段。

    函数本身不认章节 —— 它只吃调用方给的文本,因此"范围对不对"要钉在抠取那一步。
    """
    body = check_docs._handoff_section_1()
    assert body and "## 2" not in body, "§1 抠得不干净,会把历史行一起扫进当前口径"
    assert "已完成事项" not in body      # §2 的标题都不该进来


def test_handoff_section_one_is_actually_found():
    """抠不到 §1 就等于这条子规则永远绿 —— 先证明抠得到。"""
    body = check_docs._handoff_section_1()
    assert "内置" in body and "工具" in body, "HANDOFF §1 没抠到,工具数子规则在空转"
