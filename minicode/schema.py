"""`--output-schema` 那一份 schema 的读法、问法与验法(v0.9.136)。

无头模式给脚本读的那份 JSON 里,`response` 是一句人话。CI 里想拿"这次评测的结论 +
分数"这种**结构化**结果,今天得自己从人话里再抠一遍 —— 竞品 Codex 有 `--output-schema`
这一格,补上。

**这里做的是"问它要 + 验回来",不是网关侧的受约束解码。** 说清差别,因为它决定
失败时长什么样:网关侧 grammar 约束(部分端点的 `response_format: json_schema`)能
**保证**模型只能吐出合 schema 的字节;我们不知道接的这家端点支不支持、也不想知道,
所以这里只保证两件事 —— ①**照 schema 问过它**,②**回来不合就明说**(退出码 6 与
`schema_violations` 那份清单)。把后者说成前者,是这一路最容易犯的"界面上说安全、
实际没拦"。

三条规矩:
1. **不认识的结构型关键字一律拒收**,不是忽略。`anyOf`/`$ref`/`pattern`/`minimum`
   这些被忽略的后果是"验过了、其实没验" —— 那是假绿里最贵的一种。只允许忽略
   **不影响合法性判定**的那些(`description`/`title`/`default`/`examples`/`$schema`/`$id`)。
2. **不合 schema 不许悄悄退化**成"没 schema 那份结果":字段照样给全,
   `structured_output` 为 null、`schema_violations` 说清哪一条不通。
3. **模型会加代码围栏、会前言后语**,所以取 JSON 这一步要容错(先整段试、再从第一个
   `{`/`[` 平衡截一段试);三种都解不出来,violation 里就写"回答里找不到 JSON 对象",
   而不是假装它没回答。
"""
from __future__ import annotations

import json
import re

# 支持的类型关键字就这六个(JSON Schema 的七种里 `null` 单独判,不当"类型"处理)。
TYPES = {"object": dict, "array": list, "string": str,
         "boolean": bool, "integer": int, "number": (int, float)}
# 忽略无害的:它们不改变"什么算合法"。
IGNORABLE = {"$schema", "$id", "title", "description", "default", "examples",
             "$comment", "readOnly", "writeOnly", "deprecated"}
# 结构型关键字:认得的按下表实现,认不了的**必须当场拒**,不能装成"这条不用验"。
SUPPORTED = {"type", "properties", "required", "items", "enum",
             "additionalProperties", "anyOf", "oneOf", "allOf"}

_MAX_ANSWER_SCAN = 200000      # 回答长过这个数就不再试着解 JSON(防自己把内存吃掉)


class SchemaError(Exception):
    """这份 schema 本身不能用:哪里不认识就说哪里。"""


def load(raw: str) -> dict:
    """从**内联 JSON 或文件路径**读出 schema,并当场核对它用的关键字我们都验。

    为什么在这里就拒:走到"问模型"那一步才发现 schema 有问题,用户已经等了一整轮
    的额度;而一句"这一轮没按你的 schema 验"比一句"没做完"更难查。
    """
    text = (raw or "").strip()
    if not text:
        raise SchemaError("给了一份空的 schema")
    body = text
    # 只有"看起来是 JSON"的两 shapes 才当内联,其余当文件路径。判据不能只看 `{`:
    # 有人会把一个数组 `[]` 直接写进来,那时它该报"顶层要是一个对象",而不是
    # "读不到这个文件" —— 两句错话说的是完全不同的两件事,后者会让人去查路径。
    if text[:1] not in ("{", "["):
        # 不是 JSON 对象就当文件路径读:CI 里 schema 通常是个文件
        try:
            with open(text, encoding="utf-8") as f:
                body = f.read()
        except OSError as e:
            raise SchemaError(f"既不是 JSON 也读不到这个文件({type(e).__name__})") from e
    try:
        data = json.loads(body)
    except ValueError as e:
        raise SchemaError(f"不是合法 JSON:{e}") from e
    if not isinstance(data, dict):
        raise SchemaError(f"schema 顶层要是一个对象,给的是 {type(data).__name__}")
    _walk_check(data, "")
    return data


def _walk_check(node: dict, path: str) -> None:
    here = path or "(顶层)"
    for key in node:
        if key in IGNORABLE or key in SUPPORTED:
            continue
        raise SchemaError(
            f"{here} 用了这一版不验的关键字「{key}」 —— 忽略它等于当着你说"
            f"\"按 schema 验过了\"而其实没验。认得的只有:{'、'.join(sorted(SUPPORTED))}")
    for sub_name, sub in (node.get("properties") or {}).items():
        if isinstance(sub, dict):
            _walk_check(sub, f"{path}.properties.{sub_name}" if path else
                        f"properties.{sub_name}")
    items = node.get("items")
    if isinstance(items, dict):
        _walk_check(items, f"{path}.items" if path else "items")
    for branch in ("anyOf", "oneOf", "allOf"):
        for i, sub in enumerate(node.get(branch) or []):
            if isinstance(sub, dict):
                _walk_check(sub, f"{path}.{branch}[{i}]" if path else f"{branch}[{i}]")


def instruction(schema: dict) -> str:
    """给模型的那句要求。它走"只活在这一回合"的通道,不写进留档里的用户原话。"""
    shape = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return ("〔本次调用的输出要求(由调用方用 --output-schema 附加,不是用户原话)〕"
            "整段回答**只包含一个 JSON 对象**,严格符合下面这份 JSON Schema:"
            f"{shape}。不要加解释文字、不要 Markdown 代码块、不要前后空行之外的一切;"
            "字段名与类型以这份 schema 为准,拿不准的字段宁可省略(只要它不是 required)。")


def extract(answer: str):
    """从回答里取那个 JSON 值。返回 (值, 错误说明) —— 两个只有一个不为空。

    容错顺序是刻意的:先整段试(最干净),再退一步剥代码围栏,最后才在文本里找第一个
    平衡的 `{...}`/`[...]`。前两种成了就不走第三种 —— 拿正则去"猜" JSON 边界会把
    字符串里的一对花括号当成结束,那是解错而不是解不出。
    """
    text = (answer or "").strip()
    if not text:
        return None, "模型这回合没有给出正文,无从取值"
    if len(text) > _MAX_ANSWER_SCAN:
        return None, f"回答有 {len(text)} 字,太长就不再试着解 JSON(怕把内存吃掉)"
    for cand in _candidates(text):
        try:
            return json.loads(cand), None
        except ValueError:
            continue
    return None, "回答里找不到能解析的 JSON 对象(原样回话太长时只报这一句,不塞回来)"


def _candidates(text: str):
    yield text
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        yield fenced.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    yield text[start:i + 1]
                    break


def validate(value, schema: dict, path: str = "") -> list:
    """按这份 schema 验一个值,返回**人话**的问题清单(空 = 合)。

    只验 `load()` 认下的那几个关键字,所以这里的完备性是对外承诺过的那一份;
    每个失败都带路径(`data.items[3].score`),否则"不合 schema"这句话没法修。
    """
    problems: list[str] = []
    here = path or "根"
    want = schema.get("type")
    if want is not None and not _type_ok(value, want):
        return [f"{here}:类型应是 {_name_of(want)},实际是 {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{here}:值 {json.dumps(value, ensure_ascii=False)[:60]} "
                        f"不在 enum 允许的那几个里({'、'.join(json.dumps(e, ensure_ascii=False) for e in schema['enum'])})")
    if isinstance(value, dict):
        for need in schema.get("required") or []:
            if need not in value:
                problems.append(f"{here}:缺必填字段「{need}」")
        props = schema.get("properties")
        if isinstance(props, dict):
            for key, sub in value.items():
                if key in props and isinstance(props[key], dict):
                    problems += validate(sub, props[key],
                                        f"{path}.{key}" if path else key)
                elif props.get(key) is False or schema.get("additionalProperties") is False:
                    problems.append(f"{here}:多了 schema 里没有的字段「{key}」"
                                    "(additionalProperties: false)")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            problems += validate(item, schema["items"], f"{path}[{i}]" if path else f"[{i}]")
    for branch, need in (("anyOf", any), ("oneOf", _exactly_one), ("allOf", all)):
        subs = schema.get(branch)
        if isinstance(subs, list) and subs:
            oks = [not validate(value, s, path) for s in subs if isinstance(s, dict)]
            good = need(oks) if branch != "oneOf" else sum(oks) == 1
            if not good:
                problems.append(f"{here}:不满足 {branch} 里的任何一个分支")
    return problems


def _type_ok(value, want) -> bool:
    types = want if isinstance(want, list) else [want]
    for t in types:
        if t == "null" and value is None:
            return True
        py = TYPES.get(t)
        if py and isinstance(value, py):
            # bool 是 int 的子类,而 JSON 里 true 不是数字:不拆开就会把 True 判成 integer
            if py is int and isinstance(value, bool):
                continue
            if py is (int, float) and isinstance(value, bool):
                continue
            return True
    return False


def _name_of(want) -> str:
    return " 或 ".join(want) if isinstance(want, list) else str(want)


def _exactly_one(flags) -> bool:
    return sum(1 for f in flags if f) == 1
