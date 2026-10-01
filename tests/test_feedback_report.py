"""反馈包(R2,v0.9.99)测试:形状、隐私口径、轨迹可选、处理器落盘。

隐私三条是这份文件能不能发出去的底线,逐条有针:
①密钥/凭据/配置内容一个字节不进包;②心情/课表/成绩不碰;
③轨迹默认不带,带了要在 privacy note 写明。
"""
import json

import pytest

from minicode import feedback_report as fr
from minicode import growth
from minicode.config import Config, Provider
from minicode.tools.base import ToolContext


@pytest.fixture
def cfg():
    return Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                      api_key="sk-secret", model="m")],
                  active_provider_id="t")


def test_build_report_shape(cfg, tmp_path):
    rep = fr.build_report(workspace=tmp_path, config=cfg, note="导出总是失败")
    assert rep["format"] == fr.REPORT_FORMAT
    assert rep["app"]["app_version"] and rep["app"]["platform"]
    assert rep["note"] == "导出总是失败"
    assert "轨迹" not in rep["privacy"], "默认不带轨迹,privacy 里不该提"
    assert isinstance(rep["health"].get("rows"), list) and rep["health"]["rows"], \
        "浅检至少有本机那几行"
    assert all({"key", "label", "status"} <= set(r) for r in rep["health"]["rows"])


def test_report_never_carries_secrets(cfg, tmp_path):
    """密钥三条路:Provider 的 key 在 config 里传了进来,但报告绝不能带出去。"""
    text = json.dumps(fr.build_report(workspace=tmp_path, config=cfg),
                      ensure_ascii=False)
    assert "sk-secret" not in text
    assert "api_key" not in text
    assert "credentials" not in text and "config.local" not in text


def test_report_carries_gaps_and_skill_usage(tmp_path, cfg):
    growth.log_kb_gap("蓝桥杯报名入口在哪")
    from minicode import paths
    p = paths.APP_DIR / "skill-usage.jsonl"
    p.write_text(json.dumps({"ts": "2026-10-01 10:00:00", "skill": "git-commit"}) + "\n",
                 encoding="utf-8")
    rep = fr.build_report(workspace=tmp_path, config=cfg)
    assert any(g["query"] == "蓝桥杯报名入口在哪" for g in rep["kb_gaps"])
    assert rep["skill_usage"] and rep["skill_usage"][0]["skill"] == "git-commit"


def test_trace_opt_in_and_flagged(cfg, tmp_path):
    msgs = [{"role": "user", "content": "帮我导出课表", "ts": "2026-10-01 10:00:00"}]
    off = fr.build_report(workspace=tmp_path, config=cfg)
    assert "trace" not in off
    on = fr.build_report(workspace=tmp_path, config=cfg, include_trace=True,
                         trace_messages=msgs)
    assert on["trace"]["count"] == 1
    assert "会话轨迹(含对话原文)" in on["privacy"], "带了就必须写明"


def test_export_tool_writes_and_explains(cfg, tmp_path):
    ctx = ToolContext(workspace=tmp_path, config=cfg)
    out = fr.export_feedback_tool(ctx, note="界面打不开")
    assert "反馈-" in out and "过一眼" in out and "界面打不开" in out
    files = list(tmp_path.glob("反馈-*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["format"] == fr.REPORT_FORMAT and data["note"] == "界面打不开"


def test_feedback_tool_in_registry_and_web_excluded():
    from minicode.tools.registry import build_default_registry
    names = [t.name for t in build_default_registry()]
    assert "export_feedback" in names
    assert names.count("export_feedback") == 1
    from minicode.web_profile import EXCLUDED_TOOLS
    assert "export_feedback" in EXCLUDED_TOOLS
