"""对外三份文档(CHANGELOG / SECURITY / CONTRIBUTING)不能写着写着就变成假话。

这三份是别人判断"能不能信"的东西(v0.9.39 R 批 #86 补的)。它们一旦和代码分家,
比没有更坏:一个人照着 `SECURITY.md` 以为某个目录被拦住了,而代码里那条早就改了。
所以这里钉的全是**同一份事实的两个侧面**:文档里抄了代码的地方,拿代码反过来核。

刻意只做"扫得准"的三条:
- 文档指到的**仓库内路径**必须真存在(指向已删文件是最常见的一种烂掉);
- 敏感路径清单、权限档位名、`SHIP_NEVER` 条数、对账闸的闸名 —— 都以代码/脚本为准;
- 文档里不许出现代码里没有的 `~/某目录`(那等于我在承诺一个不存在的边界)。
"""
import re
from pathlib import Path

from minicode import permission, sandbox, state_files

ROOT = Path(__file__).resolve().parent.parent
SECURITY = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
CONTRIBUTING = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SHARE_DOCS = {"SECURITY.md": SECURITY, "CONTRIBUTING.md": CONTRIBUTING,
              "CHANGELOG.md": CHANGELOG}

# 仓库里真实存在的顶层目录 —— 只有这些开头的反引号片段才算"在指一个文件"。
# 数据目录里那些(config.local.json / credentials.json / sessions/*.jsonl)是**运行时**
# 产物,本来就不在仓库里,不该被这条检查误伤。
REPO_DIRS = ("minicode", "scripts", "tests", "docs", "packaging", "frontend")


def repo_paths(text: str) -> list[str]:
    """抽出文档里指向仓库内文件的反引号路径(去掉 `::符号` 那段;通配写法跳过)。"""
    out = []
    for raw in re.findall(r"`([^`\n]+)`", text):
        path = raw.split("::")[0].strip().rstrip(".,;)")
        if "*" in path or "?" in path:
            continue                          # `packaging/*.spec` 是"这一类文件",不是某个文件
        if "/" in path and path.split("/")[0] in REPO_DIRS:
            out.append(path)
    return out


# ---------------- 1. 三份文档确实在,且 README 指得到 ----------------

def test_share_docs_exist_and_are_pointed_at_from_readme():
    for name in ("CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md"):
        assert (ROOT / name).exists(), f"{name} 不在仓库根"
        assert f"`{name}`" in README, f"README 没指到 {name},别人不会知道它存在"
    assert "HANDOFF.md" in README, "README 没提交接文档"


def test_docs_point_only_at_files_that_exist():
    for name, text in SHARE_DOCS.items():
        for path in repo_paths(text):
            assert (ROOT / path).exists(), f"{name} 指着不存在的 `{path}`"


def test_changelog_header_tells_the_truth_about_how_it_is_made():
    """生成的那份文档必须写明"怎么重生成",否则下一个人只能手改。"""
    assert "scripts/gen_changelog.py" in CHANGELOG
    assert "gen_changelog.py --check" in \
        (ROOT / "scripts" / "gen_changelog.py").read_text(encoding="utf-8")


# ---------------- 2. 抄了代码的那几处,反过来核 ----------------

def test_security_doc_lists_exactly_the_sensitive_dirs():
    """`SENSITIVE_DIRS` 是硬拒绝清单:文档少写一条是漏告,多写一条是空头承诺。"""
    bullet = SECURITY.split("敏感路径永远拒绝")[1].split("**任何权限档位")[0]
    for rel in sandbox.SENSITIVE_DIRS:
        assert rel.split("/")[-1].lower() in bullet.lower(), f"文档没列出硬拒目录 {rel}"
    named = set(re.findall(r"~/([A-Za-z0-9._/-]+)", bullet))
    invented = named - set(sandbox.SENSITIVE_DIRS)
    assert not invented, f"文档承诺了代码里不存在的敏感目录:{sorted(invented)}"


def test_security_doc_names_every_permission_mode_and_label():
    """档位表必须**正好**是代码里那四档:多一栏是编的,少一栏是漏告。"""
    rows = [ln for ln in SECURITY.splitlines()
            if ln.startswith("| `") and ln.count("|") >= 3]
    got = [ln.split("`")[1] for ln in rows]
    assert got == list(permission.PERMISSION_MODES), \
        f"档位表写的是 {got},代码里是 {list(permission.PERMISSION_MODES)}"
    for mode in permission.PERMISSION_MODES:
        assert permission.MODE_LABELS[mode] in SECURITY, \
            f"档位 {mode} 的中文标签「{permission.MODE_LABELS[mode]}」没写"


def test_ship_never_count_cited_in_docs_is_the_real_one():
    """条数写死在文档里就会漂 —— 那就把文档里那句拿清单反过来核。

    找的是"提到 SHIP_NEVER 的那一句里出现的 `N 条`",不是全文第一个数:
    同一句里还可能引到别的数字,但那些都写在同一句内(不做跨句匹配)。
    """
    real = len(state_files.SHIP_NEVER)
    cited = []
    for text in (SECURITY, CONTRIBUTING):
        for sent in text.split("。"):        # 只按句号切:Markdown 正文是折行的,按 \n 切会把一句切断
            if "SHIP_NEVER" in sent:
                cited += [int(n) for n in re.findall(r"(\d{1,4})\s*条", sent)]
    assert cited, "文档里根本没写这份清单的条数(那这条检查就是空转,先补一句人话)"
    assert all(n == real for n in cited), \
        f"文档写的是 {cited} 条,`SHIP_NEVER` 实际 {real} 条"


def test_contributing_lists_every_docs_gate_by_name():
    """`check_docs.py` 加一道闸而 CONTRIBUTING 不提,就等于告诉别人"跑它会红"是玄学。"""
    src = (ROOT / "scripts" / "check_docs.py").read_text(encoding="utf-8")
    labels = re.findall(r'\("([^"]+)",\s*check_\w+\)', src)
    assert len(labels) >= 5, f"从 check_docs.py 里只扫到 {len(labels)} 个闸名,正则该修了"
    for label in labels:
        assert label in CONTRIBUTING, f"对账闸「{label}」没写进 CONTRIBUTING"
