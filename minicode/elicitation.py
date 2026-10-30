"""MCP elicitation 的 schema 编译与校验(v0.9.191,计划书阶段 7)。

MCP 服务端会在工具执行中途发 `elicitation/create` 向用户要结构化输入。这一版
接**安全子集**:把 requestedSchema 编译成界面那张表单卡,用户填完回给服务器。

子集的边界(每条都有测试):
- 根必须是 `{"type":"object"}` 且带 `properties`(没有字段可填 = 没什么可问);
- 字段 ≤ MAX_FIELDS(一张卡超过 8 个格子就是另一类交互,不是表单);
- 字段类型只认 `string`(+枚举/maxLength)、`number`/`integer`(+min/max)、
  `boolean`;`array`/`object`/未知类型**整份拒** —— 嵌套结构是代码执行的前厅;
- `enum` ≤ MAX_ENUM 个选项,且都得是字符串/数字(渲染成下拉);
- `format`/`pattern`/`additionalProperties` 这类一概不认:认不全的字段宁可整份拒,
  也不把"看不懂的约束"静默当没有。

**表单数据只回给当次 MCP 请求**:不落盘、不进日志(mcp 端只记 action)、不进
会话留档、不进长期记忆 —— 服务端要的是答案,不是一份用户输入的存档。

超时:`WAIT_SECONDS`(默认 300 s)内没人答就按 decline 回 —— MCP 服务端自己的
请求也有超时,让它干等是我们这边不讲理;关窗/停止立即 decline(与确认框同一
条 `_wait_event` 路)。默认仍是 decline:没有表单通道的一方(CLI/网页版/子代理)
什么都不会变。
"""
from __future__ import annotations

MAX_FIELDS = 8
MAX_ENUM = 8
MAX_STR = 500
WAIT_SECONDS = 300.0
_TYPES = ("string", "number", "integer", "boolean")


def compile_schema(schema) -> tuple[list[dict], str]:
    """requestedSchema → 字段清单;不支持回 ([], 一句原因)。

    字段:{"name", "label", "type", "required", "enum", "max_len",
          "minimum", "maximum", "description", "default"}
    `enum`/`max_len`/`minimum`/`maximum`/`default` 没有时是 None。
    """
    if not isinstance(schema, dict):
        return [], "schema 不是一份对象"
    if str(schema.get("type") or "object") != "object":
        return [], f"根类型要是 object(拿到的是 {schema.get('type')!r})"
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return [], "没有 properties(没有字段可填)"
    if len(props) > MAX_FIELDS:
        return [], f"字段数 {len(props)} 超过一张表单的上限 {MAX_FIELDS}"
    required = schema.get("required")
    required = set(required) if isinstance(required, list) else set()
    fields: list[dict] = []
    for name, raw in props.items():
        name = str(name)
        if not isinstance(raw, dict):
            return [], f"字段 {name} 的定义不是一份对象"
        ftype = str(raw.get("type") or "")
        if ftype not in _TYPES:
            return [], (f"字段 {name} 的类型 {ftype or '(缺 type)'} 本版不支持"
                        "(只认 string/number/integer/boolean;")
        if "format" in raw or "pattern" in raw:
            return [], f"字段 {name} 带本版不认的约束(format/pattern)"
        field = {"name": name,
                 "label": str(raw.get("title") or name),
                 "type": ftype,
                 "required": name in required,
                 "description": str(raw.get("description") or ""),
                 "enum": None, "max_len": None,
                 "minimum": None, "maximum": None, "default": None}
        if ftype == "string":
            enum = raw.get("enum")
            if enum is not None:
                if not isinstance(enum, list) or not enum or len(enum) > MAX_ENUM:
                    return [], (f"字段 {name} 的枚举为空或超过 {MAX_ENUM} 个选项")
                if not all(isinstance(v, (str, int, float, bool)) for v in enum):
                    return [], f"字段 {name} 的枚举里有非基础类型的选项"
                field["enum"] = [str(v) for v in enum]
            ml = raw.get("maxLength")
            if ml is not None:
                if not isinstance(ml, int) or ml < 1 or ml > MAX_STR:
                    return [], f"字段 {name} 的 maxLength 超出 1~{MAX_STR}"
                field["max_len"] = ml
        if ftype in ("number", "integer"):
            for bound in ("minimum", "maximum"):
                v = raw.get(bound)
                if v is not None and not isinstance(v, (int, float)):
                    return [], f"字段 {name} 的 {bound} 不是数字"
                field[bound] = v
        dv = raw.get("default")
        if dv is not None:
            if isinstance(dv, (str, int, float, bool)):
                field["default"] = dv
        fields.append(field)
    return fields, ""


def _num(field: dict, raw: str) -> "tuple[object, str]":
    try:
        if field["type"] == "integer":
            if raw.strip() in ("", "-"):
                raise ValueError(raw)
            return int(raw), ""
        return float(raw), ""
    except ValueError:
        return None, f"「{field['label']}」要填{'整数' if field['type'] == 'integer' else '数字'}"


def validate(fields: list[dict], values) -> tuple[dict, list[str]]:
    """前端交回来的 {字段名: 字符串} → (给服务端的 content, 错误清单)。

    界面交上来的**全是字符串**(输入框/下拉的原样值),类型在这里收口:
    number/integer 在此转、boolean 只认 yes/no、枚举必须精确命中。
    必填缺了要点名;多出来的键**忽略**(前端只回表单上有的格子)。
    """
    values = values if isinstance(values, dict) else {}
    content: dict = {}
    errors: list[str] = []
    for f in fields:
        raw = values.get(f["name"])
        raw = "" if raw is None else str(raw).strip()
        if raw == "":
            if f["required"]:
                errors.append(f"「{f['label']}」必填")
            elif f["default"] is not None:
                content[f["name"]] = f["default"]
            continue
        if f["type"] == "boolean":
            low = raw.lower()
            if low in ("yes", "true", "1", "是", "on"):
                content[f["name"]] = True
            elif low in ("no", "false", "0", "否", "off"):
                content[f["name"]] = False
            else:
                errors.append(f"「{f['label']}」只认 是/否")
            continue
        if f["enum"] is not None:
            if raw not in f["enum"]:
                errors.append(f"「{f['label']}」要从下拉里选一个")
            else:
                content[f["name"]] = raw
            continue
        if f["type"] in ("number", "integer"):
            v, err = _num(f, raw)
            if err:
                errors.append(err)
                continue
            if f["minimum"] is not None and v < f["minimum"]:
                errors.append(f"「{f['label']}」不能小于 {f['minimum']}")
                continue
            if f["maximum"] is not None and v > f["maximum"]:
                errors.append(f"「{f['label']}」不能大于 {f['maximum']}")
                continue
            content[f["name"]] = v
            continue
        # string
        if f["max_len"] is not None and len(raw) > f["max_len"]:
            errors.append(f"「{f['label']}」最多 {f['max_len']} 个字符")
            continue
        content[f["name"]] = raw
    return content, errors
