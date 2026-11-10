---
description: 运行 Co_Reviewer 审稿三端同步返修工作流
---
1. 运行 `python scripts/co_reviewer_cli.py inspect ...` 扫描底稿。
2. 生成冲突消解与同步方案供用户确认。
3. 执行 `python scripts/co_reviewer_cli.py apply ...` 注入原生修订。
