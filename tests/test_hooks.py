"""工具钩子(F3,v0.9.100)测试:模板替换、pre 拦截、post 如实附行、安全口径。

安全三条是底线:①钩子只从 config.json 来(没有工作区入口);②参数/结果
经临时文件交付不进命令行;③pre 拦截发生在 tool_start 之前(不出卡片)。
"""
import json


from minicode import hooks


def test_run_hook_no_config_means_none():
    assert hooks.run_hook("pre_tool", None, "bash") is None
    assert hooks.run_hook("pre_tool", {}, "bash") is None
    assert hooks.run_hook("pre_tool", {"post_tool": "x"}, "bash") is None


def test_render_substitutes_tool_inline_and_args_via_file(tmp_path):
    args = {"path": "包含 空格 与\"引号\".txt"}
    cmd, tmp = hooks._render("检查 {tool} 的参数文件 {args_file}", "write_file", args, "args_file")
    assert "{tool}" not in cmd and "write_file" in cmd
    assert tmp is not None and json.loads(tmp.read_text(encoding="utf-8")) == args
    # 没有 {args_file} 占位就不落临时文件
    cmd2, tmp2 = hooks._render("echo {tool}", "bash", args, "args_file")
    assert tmp2 is None


def test_pre_hook_blocks_on_nonzero_exit():
    cfg = {"pre_tool": "exit 1"}
    r = hooks.run_hook("pre_tool", cfg, "bash", args={"path": "x"})
    assert r["blocked"] is True and r["ok"] is False
    # 成功不拦截
    r2 = hooks.run_hook("pre_tool", {"pre_tool": "echo ok"}, "bash", args={"path": "x"})
    assert r2["blocked"] is False and r2["ok"] is True
    # 超时也算拦截
    r3 = hooks.run_hook("pre_tool", {"pre_tool": "ping -n 30 127.0.0.1"}, "bash",
                        args={"path": "x"})
    assert r3["blocked"] is True


def test_post_hook_failure_does_not_block():
    r = hooks.run_hook("post_tool", {"post_tool": "exit 3"}, "bash", result="已完成")
    assert r["blocked"] is False and r["ok"] is False
    r2 = hooks.run_hook("post_tool", {"post_tool": "echo done"}, "bash", result="已完成")
    assert r2["ok"] is True


def test_pre_blocks_before_tool_start_and_reports(tmp_path):
    """端到端:pre 拦截发生在 tool_start 之前 —— 界面没有"正在执行"的卡片,
    回话是带钩子输出的错误。"""
    import json as _json
    from minicode.agent import Agent
    from minicode.config import Config, Provider
    from minicode.gui_ui import GuiUI
    from minicode.session import Session
    from minicode.tools.base import Tool, ToolContext
    from minicode.llm import ToolCall

    class FakeWindow:
        def __init__(self): self.events = []
        def evaluate_js(self, js): self.events.append(js)

    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t",
                 permission_mode="accept_all",
                 hooks={"pre_tool": "exit 7"})
    ui = GuiUI()
    ui.window = FakeWindow()
    started = []
    tool = Tool(name="write_file", description="", parameters={},
                handler=lambda ctx, **k: started.append(k) or "已写入")
    from minicode.permission import PermissionGate
    agent = Agent(llm=None, registry=[tool],
                  session=Session(system_prompt="s", log_dir=None),
                  gate=PermissionGate(cfg),
                  ui=ui,
                  ctx=ToolContext(workspace=tmp_path, config=cfg),
                  max_steps=1)
    out = agent._execute(ToolCall(id="c1", name="write_file",
                                  arguments=_json.dumps({"path": "a.txt", "content": "x"})))
    assert "工具钩子拦下" in out and started == [], "被拦的那步绝不能真执行"
