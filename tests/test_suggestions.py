"""动态首页建议:主题打分、历史采集、默认回退。"""
import json
import time

from minicode.suggestions import (
    MAX_SUGGESTIONS, TOPICS, build_suggestions, collect_user_texts,
)


def test_no_history_returns_defaults():
    out = build_suggestions([])
    assert len(out) == MAX_SUGGESTIONS
    assert out[0] == "最近学校有什么活动"   # 默认热度顺序


def test_asked_topic_rises_with_followup():
    out = build_suggestions(["校园卡丢了怎么办"])
    # 聊过校园卡 → 该主题换成追问话术并排第一
    assert out[0] == "校园卡补办费用再确认一下"
    # 未聊过的默认主题仍会补位,且总数不变
    assert "考研择校与复习规划" in out
    assert len(out) == MAX_SUGGESTIONS


def test_recent_messages_weighted_higher():
    texts = ["考研", "考研", "宿舍空调坏了"]   # 考研更新且更密集
    out = build_suggestions(texts)
    assert out[0].startswith("考研")


def test_every_topic_has_ask_and_unique():
    asks = [t.ask for t in TOPICS]
    assert len(asks) == len(set(asks))
    assert all(t.keywords and t.followup for t in TOPICS)


def _write_session(dir_path, texts, mtime_offset=0):
    p = dir_path / f"session_{int(time.time())}_{mtime_offset}.jsonl"
    lines = [json.dumps({"role": "user", "content": t}, ensure_ascii=False)
             for t in texts]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_collect_from_current_and_files(tmp_path):
    _write_session(tmp_path, ["四级难吗"], mtime_offset=1)
    time.sleep(0.01)
    _write_session(tmp_path, ["奖学金怎么评定"], mtime_offset=2)
    current = [{"role": "user", "content": "考研什么时候出分"},
               {"role": "assistant", "content": "别急"},
               {"role": "user", "content": "好的"}]
    texts = collect_user_texts(tmp_path, current)
    assert texts[0] == "好的"                    # 当前会话倒序最优先
    assert "考研什么时候出分" in texts
    assert "奖学金怎么评定" in texts             # 新文件先于旧文件
    assert "四级难吗" in texts
    assert all("assistant" not in t for t in texts)


def test_collect_handles_missing_dir(tmp_path):
    assert collect_user_texts(tmp_path / "nope", None) == []
