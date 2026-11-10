---
name: co-reviewer
description: 多文档学术论文审稿返修协同工具（Reviewers / Manuscript / SI 三端同步与 Word 原生修订模式注入）。采用 Supervisor-Worker 架构极致节省主进程 Token。
---

# Co_Reviewer: 学术审稿三端同步与主从自动化技能

## 核心省 Token 铁律 (Supervisor Guardrails)
1. **严禁主进程写临时探索脚本**：禁止在主进程中创建类似 `dump_*.py`、`check_*.py` 的一次性脚本。所有底层解析、段落检索、图片移植、修订注入必须调用 `scripts/co_reviewer_cli.py` 或委派给 **OpenCode Worker** 执行。
2. **主进程上下文隔离**：主进程（Antigravity Supervisor）**只允许读取**精简的 Markdown 摘要报告（`< 300 行`），绝不直接将整篇 `.docx` 的全部段落灌入主上下文。
3. **两步闭环法**：全流程严格限制在 **2 次 Worker 委派 + 1 次人工确认** 内完成。

---

## 标准执行工作流 (Two-Stage Pipeline)

### 阶段 1：委派 OpenCode Worker 执行全量体检与上下文切片
当用户提供 `Reviewers.docx`、`Manuscript.docx`、`SI.docx` 时，主进程立即委派 **OpenCode Worker** 执行以下任务：
- **Worker 指令**：
  1. 运行 `python scripts/co_reviewer_cli.py inspect --rev <Reviewers.docx> --man <Manuscript.docx> --si <SI.docx> --author "春雳" --out workspace_digest.md`
  2. 输出高密度、低 Token 占用的 `workspace_digest.md`。

### 阶段 2：委派 OpenCode Worker 组装 edits_plan.json 并注入修订
收到用户确认后，主进程将确认后的目标修改文本下发给 **OpenCode Worker**：
- **Worker 指令**：
  1. 组装 `edits_plan.json`。
  2. 调用 `python scripts/co_reviewer_cli.py apply --plan edits_plan.json`。
