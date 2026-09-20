"""绩点计算器测试:加权平均 / 两种绩点算法 / 等级折算 / 容错。"""
from minicode.gpa import gpa_calc


def test_weighted_average_and_both_scales():
    out = gpa_calc(None, courses=[
        {"name": "高等数学", "score": 88, "credit": 4},
        {"name": "大学英语", "score": 92, "credit": 3},
    ])
    # 加权平均 = (88*4 + 92*3) / 7 = 89.71
    assert "加权平均分: 89.71" in out
    # 五分制: (3.8*4 + 4.2*3) / 7 = 3.97
    assert "五分制绩点: 3.97" in out
    # 4.0 制: (3.8*4 + 4.0*3) / 7 = 3.89(92 分封顶 4.0)
    assert "4.0 制绩点: 3.89" in out
    assert "以学校教务处" in out
    assert "高等数学 88 分 × 4 学分" in out


def test_grade_letters_are_folded_with_note():
    out = gpa_calc(None, courses=[
        {"name": "体育", "score": "优", "credit": 1},
        {"name": "思政", "score": "良", "credit": 2},
    ])
    assert "优=95" in out and "常见折算" in out
    assert "加权平均分: 88.33" in out          # (95*1 + 85*2) / 3


def test_failed_course_counts_zero_gpa():
    out = gpa_calc(None, courses=[
        {"name": "线代", "score": 50, "credit": 3},
        {"name": "物理", "score": 80, "credit": 3},
    ])
    assert "五分制绩点: 1.50" in out            # (0*3 + 3.0*3)/6
    assert "4.0 制绩点: 1.50" in out


def test_invalid_rows_are_skipped_and_reported():
    out = gpa_calc(None, courses=[
        {"name": "好的课", "score": 90, "credit": 2},
        {"name": "坏分数", "score": "挂科", "credit": 2},
        {"name": "坏学分", "score": 80, "credit": 0},
        "不是字典",
    ])
    assert "加权平均分: 90.00" in out
    assert "坏分数" in out and "坏学分" in out and "被跳过" in out


def test_empty_and_all_invalid():
    assert gpa_calc(None, courses=[]).startswith("错误:")
    assert gpa_calc(None).startswith("错误:")
    out = gpa_calc(None, courses=[{"name": "x", "score": 999, "credit": 1}])
    assert out.startswith("错误:") and "被跳过" in out
