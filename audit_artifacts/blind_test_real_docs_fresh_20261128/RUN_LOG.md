# Co_Reviewer 真实文档盲测 — 完整运行日志（公开仓库脱敏版）

> 脱敏说明：保留全部命令、阶段、计数、哈希、错误代码与 8 次 fail-closed 修正过程；
> 删除逐字 find/replace 文本、论文正文片段、作者姓名，DOCX/PDF 未上传。原始 Round 未被修改。

## 00 基线与隔离

1. `git rev-parse HEAD` → `32e08dd42613c38a101b720cbf420e33fcff4813`；`git status --porcelain` 为空；分支 `feat/format-real-docx-acceptance-batch5-20261128`。
2. 四个原始文件存在性、大小、SHA-256 记录见 `reports/hashes.json`（文件名作者字段已脱敏）。
3. 新建独立 Round `<ROUND_ROOT>`（input/logs/plan/reports/output/runs）；仅复制四个原文件到 `input/`，复制后哈希逐一相同；未读取历史 round 的 FR/Plan/Receipt/Apply 产物；未修改原始备份目录。
4. 环境：Python 3.13.9；Microsoft Word COM 16.0。

## 01 只读文档勘察（stdlib 脚本）

- `logs/inspect_docx.py` → `logs/{CL,Main,SI}_paragraphs.json`（17 / 81 / 81 段）。
- `logs/anchor_report.py` → 所有查找锚点唯一命中；条件项前提经源文档核实为假。
- `logs/dump_targets_direct.py` → 目标段落原文与 Run 格式，用于构造精确 `find` 与格式标记（正文已脱敏，不上传）。

## 02 Agent 指令理解（无人工填值）

- `plan/build_artifacts.py` 由 Agent 通读指令后产出：`instruction_analysis.json`（48 条原子需求）、`instruction_coverage.raw.json`（schema 1.1）、`intents.json`（30 intents）。
- `validate-instruction-analysis --analysis … --out reports/instruction_analysis_validation.json` → `passed: true`。
- action 覆盖 `replace` / `delete` / `insert` / `format`；polarity 含 `affirmative` 与 1 条 `conditional`。

## 03 Coverage / Intent / Plan / Preflight

- `sync-analysis-to-coverage --analysis … --coverage plan/instruction_coverage.raw.json --out plan/instruction_coverage.synced6.json`；`plan/finalize_coverage.py` 绑定 `coverage_sha256`。**未人工填写 action/polarity**。
- `plan/add_format_maps.py` 从源 DOCX Run 清单确定性生成 `replacement_format_map` 与 `inserted_format_map`。
- `compile-plan --intents plan/intents.mapped.json --instruction-coverage … --instruction-analysis … --out plan/revision_plan.json` → 30 items / 45 targets（15 FormatChange + 1 insert_paragraph）。
- `preflight-plan … --out reports/preflight_report.json` → `45/45 target(s) are safe to apply.`

### 8 次 fail-closed 修正（仅保留机器错误代码与处置，正文已脱敏）

1. `Unsupported doc_role` → 改用合法角色 `Other`。
2. `VERTICAL_ALIGNMENT_TARGET_TOO_BROAD` → 字面 `~` 误作下标标记，改用转义。
3. `MIXED_RUN_FORMAT_REPLACEMENT_REQUIRES_EXPLICIT_SEGMENT_PLAN` → 增加完整 `replacement_format_map`。
4. `NAIVE_SINGLE_SEGMENT_FORMAT_FLATTEN_BLOCKED` → 改用非零 plain 源偏移。
5. `Tabs/newlines are not supported in safe find/replace text` → 两条参考文献拆为两个 `insert_paragraph`。
6. 插入段落的格式目标在源文档中不存在 → 改由 `inserted_format_map` 继承源 Run 斜体/加粗。
7. `PARAGRAPH_INSERTION_AMBIGUOUS_CHARACTER_FORMAT` → 提供分段 `inserted_format_map`。
8. `character_format_integrity` 1 项 FAIL → 增加显式 `i:false` FormatChange 保持正体；重建后 45/45 通过。

所有失败均按 fail-closed 处理，未绕过任何 gate。

## 04 授权 / Apply / Clean / Verify / Final Gate

- `plan/build_assessment.py` → 48 需求 ID + 45 目标引用，四个阻塞数组为空，绑定 preflight plan SHA。
- `authorize-instruction-plan …` → `[INSTRUCTION-BASED AUTHORIZED PLAN]`；回执为机器授权，明确 *not represented as human approval*；未伪造人工批准。
- `apply … --report reports/apply_report.json` → 7 / 32 / 6 编辑全部写入原生 Track Changes。
- `compile-clean-from-marked`（3 次）→ 三份 Clean。
- `verify … --out reports/verify_report.json` → `45/45 targets verified; 0 failed`；`0 untracked-text violation(s)`。
- `final-gate … --docs <6 files> --document-role <6 bindings>` → `verdict=READY status=PATCH_READY`；8 项 gate 全 PASS；0 fallback / 0 blocked / 0 skipped format ops；六份 DOCX 质量与 Word COM PDF 渲染全 PASS。

## 05 独立真实文档验收（不依赖 CLI 退出码）

- `logs/acceptance_check.py` → `reports/independent_acceptance.json`：58 项文本检查、25 项格式检查全通过；Marked 中原生 `w:ins/w:del`（23/21、4/4、3/3），Clean 均 0/0 且无 comment part。
- `logs/collect_hashes.py` → `reports/hashes.json`：四份原始文件 SHA-256 运行前后不变；12 个本地交付文件哈希记录（公开副本已脱敏文件名并移除 DOCX/PDF 正文）。
