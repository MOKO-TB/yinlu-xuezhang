"""冻结版真跑一遍「教务课表导入」链路 —— 不是只在 PYZ 里"看见"符号,而是**用包内代码跑完**。

为什么单独要这一条:`check_frozen_version.py` 只核对字节码里的标识符名,
证明不了模块能 import(缺 hiddenimports / 少数据文件都能过名字核对却当场炸)。
本脚本在**打包好的 CLI** 里跑:解析夹具课表 → 过 `schedule.save` 校验 →
出首次运行向导的 payload,三样都得在包内成立才算这版能发给同学。

跑法(在项目根):
    dist/引路学长/引路学长-CLI.exe --py scripts/e2e_frozen_schedule.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

if not getattr(sys, "frozen", False):        # 源码态:scripts/ 在 sys.path 上,仓库根不在
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fixture() -> dict:
    """找 tests/fixtures/jw_xskb_grid.json(冻结版旁边没有仓库,按几种根位置试)。"""
    roots = [Path.cwd(), Path(__file__).resolve().parent.parent,
             Path(sys.executable).parent]
    for r in roots:
        f = r / "tests" / "fixtures" / "jw_xskb_grid.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    raise SystemExit("找不到 tests/fixtures/jw_xskb_grid.json —— 在项目根跑这条")


def main() -> int:
    import minicode
    from minicode import onboarding, paths, schedule, schedule_import

    print("包内版本:", minicode.__version__)
    diag = paths.diagnostics()
    print("包内数据目录:", diag["app_dir"], "| 可写:", diag["writable"],
          "| 后端:", diag["backend"])

    # 1) 包内解析器读得懂课表网格
    res = schedule_import.parse_grid(_fixture())
    sm = schedule_import.summarize(res["classes"])
    print(f"解析:ok={res['ok']} 节次条目={sm['entries']} 门数={sm['courses']} "
          f"读不懂={len(res['unparsed'])} 提示={len(res['notes'])}")
    assert res["ok"], f"包内解析器读不了课表: {res}"
    # 夹具刻意把"节次条目"和"门数"做成不同(8 条 = 8 门;有一格两门课),
    # 数字错了说明包内那份解析代码不是新版
    assert sm["entries"] == 8 and sm["courses"] == 8, f"结果与夹具预期不符: {sm}"
    assert len(res["unparsed"]) == 1, "夹具里那个纯文字格应被如实报成读不懂"

    # 2) 解析结果必须能过 schedule.save 的校验(向导最后一步就是存它)。
    #    先把数据目录钉到临时目录:不钉就会把 schedule.json 写进
    #    dist/引路学长/ —— 那是要拷给同学的文件夹。
    tmp = Path(tempfile.mkdtemp(prefix="frozen-jw-"))
    paths.APP_DIR = tmp
    ok, msg = schedule.save("2026-09-07", res["classes"])
    assert ok, f"解析结果存不进 schedule.save: {msg}"
    assert schedule.state_path().parent == tmp, "save 没落到临时目录(写脏 dist 了)"
    view = schedule.view()
    print(f"存盘:ok=True 已登记={view['registered']} 第1周周一={view['term_start']}")

    # 3) 首次运行向导:没配 Key 时才该弹,且必须把数据目录讲给同学看
    pay = onboarding.payload(key_configured=False, paths=diag)
    assert pay["show"] and len(pay["steps"]) == 3, f"首启向导不对: {pay}"
    assert pay["data_dir"], "向导里没有数据目录 —— 同学遇到问题没法定位"
    print(f"首启向导:show={pay['show']} 步骤={len(pay['steps'])} "
          f"数据目录已下发={bool(pay['data_dir'])}")

    # 4) 三步向导对象能构造(真起浏览器要人配合,这里只验装配)
    flow = schedule_import.ImportFlow()
    for m in ("open_login", "read", "read_with_model"):
        assert callable(getattr(flow, m, None)), f"向导缺 {m}()"

    print("\n=== FROZEN 教务导入链路 PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
