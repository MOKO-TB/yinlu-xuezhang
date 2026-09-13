"""测试用假 MCP 服务器:stdio 上一行一个 JSON-RPC 报文。

支持的方法:
  initialize      → serverInfo=fake-mcp
  tools/list      → echo(回显 message)/ fail(返回 isError)/ slow(睡 2 秒)
  tools/call      → 按工具名返回对应结果
"""
import json
import sys
import time

TOOLS = [
    {
        "name": "echo",
        "description": "回显传入的 message(测试用)",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "要回显的文本"}},
            "required": ["message"],
        },
    },
    {
        "name": "fail",
        "description": "总是返回错误(测试用)",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def reply(rid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result},
                                ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        rid = msg.get("id")
        if rid is None:          # 通知(notifications/initialized)无需响应
            continue
        if method == "initialize":
            reply(rid, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake-mcp", "version": "0.0.1"}})
        elif method == "tools/list":
            reply(rid, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "echo":
                reply(rid, {"content": [{"type": "text", "text": f"echo: {args.get('message', '')}"}]})
            elif name == "fail":
                reply(rid, {"isError": True,
                            "content": [{"type": "text", "text": "故意失败"}]})
            elif name == "slow":
                time.sleep(2)
                reply(rid, {"content": [{"type": "text", "text": "slow done"}]})
            else:
                sys.stdout.write(json.dumps(
                    {"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32601, "message": f"未知工具 {name}"}},
                    ensure_ascii=False) + "\n")
                sys.stdout.flush()
        else:
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": rid,
                 "error": {"code": -32601, "message": f"未知方法 {method}"}},
                ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
