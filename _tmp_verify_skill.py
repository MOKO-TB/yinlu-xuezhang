"""临时校验:直接跑那几条闸的纯函数,绕开 pytest 的 tmpdir。跑完即删。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from minicode import router, skills as sk          # noqa: E402

fails = []

# 1) 技能能被加载,且 name 与目录名一致
loaded = sk.load_skills([ROOT / "skills"])
by_name = {s.name: s for s in loaded}
if "scientific-figure-making" not in by_name:
    fails.append("技能没被加载到")
else:
    s = by_name["scientific-figure-making"]
    if s.path.parent.name != "scientific-figure-making":
        fails.append("目录名与 name 不一致")
    if len(s.description) <= 20 or "**" in s.description:
        fails.append(f"description 不合格({len(s.description)} 字)")
    if len(s.body) >= sk.SKILL_BODY_LIMIT:
        fails.append(f"正文 {len(s.body)} 字 ≥ {sk.SKILL_BODY_LIMIT}")
    if s.libs != ("PIL",):
        fails.append(f"libs 声明不对:{s.libs}")
    if s.body.count("\n# ") < 3:
        fails.append("正文分节不足 3 个")

# 2) 路由认领(test_every_repo_skill_is_claimed_by_a_domain 的口径)
have = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
claimed = {x for spec in router.DOMAINS.values() for x in spec.skills}
claimed |= {"skill-creator"}                      # 与那条测试同一条豁免
if have - claimed:
    fails.append(f"技能没档位认领:{sorted(have - claimed)}")
if claimed - have:
    fails.append(f"档位指向不存在的技能:{sorted(claimed - have)}")

# 3) 每份 SKILL.md 的 python 块都能编译(test_content_skills 的口径)
for path in sorted((ROOT / "skills").glob("*/SKILL.md")):
    for i, src in enumerate(re.findall(r"```python\n(.*?)```",
                                       path.read_text(encoding="utf-8"), re.S), 1):
        try:
            compile(src, f"{path}[{i}]", "exec")
        except SyntaxError as e:
            fails.append(f"{path.parent.name} 第 {i} 块语法错:{e.msg}")

# 4) 技能代码块里的第三方 import 都登记过(test_skill_deps_declared 的口径)
IMPORT = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", re.M)
BLOCK = re.compile(r"```[a-zA-Z0-9]*\n(.*?)```", re.S)
mods = set()
for path in sorted((ROOT / "skills").glob("*/SKILL.md")):
    for block in BLOCK.findall(path.read_text(encoding="utf-8")):
        for raw in IMPORT.findall(block):
            mods.add(raw.split(".")[0])
third = mods - set(sys.stdlib_module_names) - {"minicode"}
unknown = sorted(m for m in third if m not in {"PIL", "docx", "openpyxl", "pptx", "pypdf"})
if unknown:
    fails.append(f"技能 import 了没登记的模块:{unknown}")

# 5) 《使用说明》里的技能份数
manual = (ROOT / "scripts" / "build_exe.py").read_text(encoding="utf-8")
start = manual.find('\n        "1. ')
end = manual.find('",\n        encoding="utf-8"', start)
n_skills = len(list((ROOT / "skills").glob("*/SKILL.md")))
for m in re.finditer(r"(\d+)\s*份技能", manual[start:end]):
    if int(m.group(1)) != n_skills:
        fails.append(f"《使用说明》写 {m.group(1)} 份技能,实测 {n_skills}")

print(f"技能总数 {n_skills};data 档认领:{router.DOMAINS['data'].skills}")
print(f"新技能正文 {len(by_name['scientific-figure-making'].body) if 'scientific-figure-making' in by_name else '-'} 字")
if fails:
    print("FAIL:")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL OK")
