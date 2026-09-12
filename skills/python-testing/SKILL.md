---
name: python-testing
description: 为 Python 代码编写与运行 pytest 测试的规范
---
# Python 测试规范

为 Python 项目编写或运行测试时遵循:

## 编写
1. 测试文件放 `tests/`,命名 `test_<模块>.py`;一个行为一个测试函数。
2. 用 fixture 提供前置数据,tmp_path 管理临时文件;测试之间绝不共享可变状态。
3. 断言要具体(断言值/内容,而不是只断言"不抛异常")。
4. 不写依赖网络、真实 API Key 或固定休眠的测试;外部服务用假实现注入。

## 运行
1. 用 `python -m pytest -q` 跑全量;先跑全量再收窄到单个用例定位:`python -m pytest tests/test_x.py::test_y -q`。
2. 测试失败时先读完整报错与断言差异,再改代码;禁止为了让测试通过而删除断言。
3. 修复后必须重跑全量,确认没有引入回归。
