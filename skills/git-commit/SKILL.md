---
name: git-commit
description: 生成符合 Conventional Commits 规范的 git 提交信息
---
# Git 提交信息规范

为用户提交代码时,commit message 遵循以下规则:

## 格式
```
<type>(<scope>?): <subject>

<body?>
```

## type 取值
- feat: 新功能
- fix: 修复缺陷
- refactor: 重构(不改行为)
- docs: 文档
- test: 测试
- chore: 构建/依赖/杂项

## 要求
1. subject 用中文、不超过 50 字,结尾不加句号,用动词开头(如"修复""新增")。
2. body 说明"为什么改",不是罗列改了哪些文件;省略不影响理解时可省略。
3. 一次提交只做一件事;发现混入无关改动时,提醒用户拆分。
4. 提交前用 `git diff --staged` 核对暂存内容,不要凭记忆写。
5. 绝不使用 `--no-verify` 跳过钩子;钩子失败要向用户说明原因。
