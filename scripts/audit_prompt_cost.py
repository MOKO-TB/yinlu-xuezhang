"""每轮固定成本审计(M1,v0.9.104):system prompt 分段 + 每只工具 schema 的重量。

用法:`python scripts/audit_prompt_cost.py [--json]`
尺子与 `minicode.compaction` 同一条:CJK 一字一 token,其余四字符一 token。
为什么要有它:v0.9.97 装了真实 prompt_tokens 的尺子,但「固定开销由哪些块
组成、谁最重」从来没有数 —— 瘦身前先有账,瘦完后用同一把尺对比。
只读:不碰任何文件,不改任何描述。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _tokens(text: str) -> int:
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    return cjk + (len(text) - cjk) // 4


def main(argv=None) -> int:
    as_json = "--json" in (argv or sys.argv[1:])
    from minicode.app_setup import build_system_prompt
    from minicode.tools.registry import build_default_registry

    sp = build_system_prompt(Path(tempfile.mkdtemp()))
    sp_t = _tokens(sp)
    sections = []
    for chunk in ("\n" + sp).split("\n# "):
        if chunk.strip():
            head = chunk.splitlines()[0].lstrip("# ").strip()[:24] or "(开头段)"
            sections.append({"section": head, "chars": len(chunk),
                             "tokens": _tokens(chunk)})
    sections.sort(key=lambda x: -x["tokens"])

    tools = []
    for t in build_default_registry():
        schema = json.dumps(t.to_openai_schema(), ensure_ascii=False)
        tools.append({"tool": t.name, "chars": len(schema),
                      "tokens": _tokens(schema)})
    tools.sort(key=lambda x: -x["tokens"])
    tools_t = sum(x["tokens"] for x in tools)

    total = sp_t + tools_t
    payload = {
        "system_prompt": {"chars": len(sp), "tokens": sp_t, "sections": sections[:10]},
        "tool_schemas": {"count": len(tools), "tokens": tools_t, "top": tools[:12]},
        "fixed_total_tokens": total,
        "note": ("固定开销 = 每一轮 LLM 请求都要付的部分(system prompt + 全部工具 schema),"
                 "与对话长度无关;按项目自己的尺估算(CJK 一字一 token,其余四字符一 token)。"),
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0
    print(f"每轮固定开销 ≈ {total} tokens(system {sp_t} + 工具 schema {tools_t})")
    print("\nsystem prompt 分段 top:")
    for x in sections[:8]:
        print(f"  {x['tokens']:>6}  {x['section']}")
    print("\n工具 schema top:")
    for x in tools[:12]:
        print(f"  {x['tokens']:>6}  {x['tool']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
