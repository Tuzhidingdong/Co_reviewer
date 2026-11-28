# Batch 退役第一轮：本地回归验证与兼容性审查报告

仓库：`Tuzhidingdong/co_reviewer`
待测分支：`refactor/retire-batches-20261128`
待测 HEAD：`abf5a7fb229dcb6d14f8a3435f478b3aca5aba3f`（实测一致）
原始回滚分支：`archive/pre-batch-retirement-20261128`
回滚基线：`32e08dd42613c38a101b720cbf420e33fcff4813`（实测一致）
实施分支相对基线：5 个提交（`a4153d4`、`0a18ed7`、`1eb1994`、`2ef617c`、`abf5a7f`）

## 判定

**`PASS_FOR_RETIREMENT_REVIEW`**

理由：定向回归 144/144 通过；全量测试的实施分支 3 个失败在基线上以**相同测试名、相同断言信息**复现，属原有失败，无新增回归、无消失的旧失败；真实 DOCX 只读 Verify 45/45、字符级格式完整性 PASS，旧授权回执与 Plan 完全兼容；未发现现有 BAT/PowerShell/Python 脚本依赖已退役接口。存在 3 个**原有失败**与 1 项**外部依赖未知**，均不构成退役引入的阻断，但需在退役验收中记录。

---

## 0. 测试环境与隔离

| 项 | 值 |
|---|---|
| 实施 worktree | `<ROOT>/test_worktrees/impl_abf5a7f`（detached `abf5a7f`），测试前 `git status --porcelain` 为空 |
| 基线 worktree | `<ROOT>/test_worktrees/baseline_32e08dd`（detached `32e08dd`），测试前状态为空 |
| 原工作目录 | 未切换；始终保持在 `audit/batch-dependency-inventory-20261128` |
| Python | 3.13.9 |
| pytest / lxml / python-docx | 8.4.2 / 6.1.1 / 1.2.0 |
| jsonschema / pywin32 | 4.25.0 / 可用 |
| Word COM | 16.0 可用（本任务未运行导出 PDF） |
| 临时输出 | `C:\Users\Administrator\AppData\Local\Temp\opencode\retire_*`（未写入用户文档目录） |
| 测试副作用 | `PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider`；两个 worktree 测试后状态仍为空 |

---

## 1. 外部兼容性扫描

搜索记号：`analyze-run-formats`、`--run-format-analysis`、`--format-run-analysis`、`--format-analysis`、`--format-preflight-out`、`--format-execution-record-out`、`--format-execution-record`、`--format-verification-out`、`co_reviewer.run_format_analysis`、`co_reviewer.format_compiler`、`co_reviewer.format_execution`、`co_reviewer.format_requirements`。

- 扫描范围：`E:\Writting\Capillary_bridge\08` 下 3,728 个 `.py/.ps1/.bat/.cmd/.sh/.txt/.json/.md` 文件；以及 `E:\Writting` 下排除该目录的 731 个同类文件。
- 结果：前者 88 处命中，后者 **0 处**。
- 分类：
  - **A 现有运行脚本/工具调用**：**0**。没有任何 BAT、PowerShell 或 Python 工具实际调用已退役命令。
  - **B 历史日志/审计报告/历史代码**：88 处全部属于此类——实施分支的 `docs/reports/*batch*.md`（历史报告文字）、旧 `isolated_repo` 仓库代码与测试、基线 worktree 代码与测试、以及此前审计 Markdown 中的描述。历史记录不构成活动依赖。
  - **C 仅文档/示例**：无独立示例文件调用；历史报告中的 CLI 示例归入 B。
- **`COMPATIBILITY_BLOCKER`：未发现。**

### 仍保留的兼容入口

| 入口 | 实施分支证据 | 结论 |
|---|---|---|
| `--instruction-analysis`（compile-plan） | `co_reviewer_cli.py:12035`；帮助输出第 4、16 行 | 保留 |
| `--instruction-analysis`（apply / verify） | `co_reviewer_cli.py:12172,12180`；帮助第 6、21 / 5、19 行 | 保留，说明为“校验既有授权回执的 FR SHA 锚” |
| `--format-gate-report` | `co_reviewer_cli.py:12100`；`final-gate --help` 第 15、57 行 | 外部报告输入接口保留 |
| `format_requirements_present` | `instruction_coverage.py:269,339,350,396` | 旧回执字段保留；`true` 时仍要求 FR 文件锚 |
| 旧 Coverage/Plan Schema 字段 | `schemas/instruction-coverage.schema.json:35-55`（`format_requirement_ids`/`format_coverage`）；`schemas/revision-plan.schema.json:18,590`（`format_compilation`/target `format_requirement_ids`） | 字段保留，旧产物结构可读 |
| 新授权拒绝 FR 生产 | `instruction_analysis.py:86`、`co_reviewer_cli.py:11635-11638` | 新指令中携带 FR 会被明确拒绝，要求改用普通指令行 |

---

## 2. 静态检查

| 检查 | 结果 |
|---|---|
| `git diff --check 32e08dd...HEAD` | 退出码 0，无输出 |
| Python 语法编译（`py_compile.compile`，`cfile` 指向临时目录） | **93/93 文件通过**；worktree 未产生文件 |
| 全仓搜索已删模块的活动 import / 函数调用 | `git grep` 在实施分支 `scripts/` 中对四个模块名与 `compile_format_requirements`/`apply_compiled_formats`/`format_apply_preflight`/`verify_format_execution`/`format_final_gate_guard`/`analyze_docx(`/`validate_run_format_analysis(` 均 **0 命中** |
| CLI 中残留悬空调用 | 无；退役 flag 在 `--help` 中 0 命中 |
| Final Gate / Apply / Verify 未定义参数 | 语法编译 + 帮助输出 + 定向测试（含 `test_final_gate.py`、`test_full_workflow_integration.py`）均通过 |
| Batch 5 测试删除后的通用回归 | `_applicable_format_flags` 回归仍存在：`tests/test_character_format_integrity.py:19,43` 与 `tests/test_format_mapping_two_defects.py:18,90-129` |
| 基础模块与基线差异 | `document_engine.py`、`plan_compiler.py`、`insert_authorization.py` 在 `32e08dd...HEAD` 的 `git diff --stat` 均为空 |

**已确认不存在的依赖**：四个退役模块在 `scripts/` 中无任何活动 import/调用；退役 CLI 参数在帮助文本中不存在。
**仍保留的兼容入口**：见 §1 表格。
**需进一步确认**：仓库外可能存在未纳入扫描的脚本（[UNKNOWN]）；`jsonschema` 是否安装会影响 schema 分支（本环境已安装 4.25.0）。

---

## 3. CLI 冒烟验证

| 命令 | 退出码 | 行数 | 结果 |
|---|---:|---:|---|
| `--help` | 0 | 169 | 正常 |
| `compile-plan --help` | 0 | 19 | 正常；含 `--instruction-analysis` |
| `apply --help` | 0 | 23 | 正常；含 `--instruction-analysis` |
| `verify --help` | 0 | 21 | 正常；含 `--instruction-analysis` |
| `final-gate --help` | 0 | 59 | 正常；含 `--format-gate-report` |

退役命令/参数在五份帮助输出中命中数：**0**。未使用无效命令推断系统行为；所有结论来自实际返回码与帮助文本。

---

## 4. 定向回归测试

命令：`python -m pytest -q -p no:cacheprovider <14 个测试文件>`（实施 worktree）

**结果：`144 passed, 36 warnings in 165.68s`，退出码 0。**

执行文件（全部通过，无跳过、无失败）：
1. `tests/test_character_format_integrity.py`
2. `tests/test_instruction_analysis_producer.py`
3. `tests/test_instruction_based_authorization.py`
4. `tests/test_instruction_lifecycle.py`
5. `tests/test_final_gate.py`
6. `tests/test_format_mapping_two_defects.py`
7. `tests/test_format_operation_integrity.py`
8. `tests/test_format_preservation.py`
9. `tests/test_plan_compiler.py`
10. `tests/test_script_format_integrity.py`
11. `tests/test_target_scoped_format.py`
12. `tests/test_revision_scoped_format.py`
13. `tests/test_full_workflow_integration.py`
14. `tests/test_schema_contracts.py`

覆盖点核对：Instruction Analysis 原文 SHA/逐字对齐、Coverage 完整性与目标绑定、人工/机器授权区分、普通 FormatChange 映射、混合 Run 与上下标、字符级格式完整性、Final Gate 对缺失/失败格式证据的阻断、Marked/Clean 与历史 Schema 兼容性——**均通过**。

警告：仅 36 条 `FutureWarning`（`co_reviewer_cli.py:4214,4218` 的 `bool(run)` 用法），不影响结果。

---

## 5. 全量测试与基线对照

| 版本 | 结果 | 退出码 | 耗时 |
|---|---|---:|---:|
| 实施 `abf5a7f` | **3 failed, 776 passed, 42 warnings** | 1 | 938.16s |
| 基线 `32e08dd` | **3 failed, 854 passed, 44 warnings** | 1 | 883.18s |

基线全量首次运行被 900 秒上限终止（日志到 75%），已用 1,800 秒上限重跑并完成；结果如上。

### 三个失败（实施分支与基线完全一致）

1. `tests/test_numbering_transaction.py::NumberingTransactionTests::test_author_decision_can_be_patch_provenance_without_fabricating_comment_ids`
   - 断言：`"source_comment_ids or a hash-bound author_decision_ref" does not match "PatchOperation requires exact source_comment_ids, instruction_requirement_ids, or a hash-bound author_decision_ref."`
2. `tests/test_revision_workflow.py::RevisionWorkflowTests::test_repreflight_of_changed_approved_source_requires_renewed_approval`
   - 错误：`ValueError: Human author approval validation failed: - author_approval scope changed for R1-C1.`
3. `tests/test_two_pass_overlap.py::TwoPassOverlapTests::test_two_target_compiled_plan_text_then_format_change_passes_verify`
   - 断言：`False is not true : {... 'targets_failed': 1 ...}`；细节：`replacement_format_maps ... 'format_map_supplied': False, 'passed': False`，`unexpected_script_tokens` 中 `subscript '2'` at offset 11；`expected e3b0c44298fc, actual e3b5d4a23c10`。

基线上单独重跑这三个测试：`3 failed, 2 warnings in 2.66s`，**测试名与断言信息逐字一致**。

### 差异解释

- 通过数差 854 − 776 = **78**：删除 5 个 Batch 测试文件共 79 个测试（`test_format_requirements_batch1.py` 15、`test_run_format_analysis_batch2.py` 12、`test_format_compiler_batch3.py` 19、`test_format_execution_batch4.py` 30、`test_batch5_matrix_gaps.py` 3），同时 `test_character_format_integrity.py` 由 12 个增至 13 个测试，净差 78。
- **新增失败：0**；**消失的旧失败：0**；**无法复现：0**。
- 未删减任何测试以改善实施分支结果；删除的是随 Batch 模块一起退役的测试文件。

---

## 6. 真实 DOCX 只读 Verify 回归

路径依据：本地 Round `RUN_LOG.md:50-53`（未猜测文件名）。

- Plan：`<ROUND>/plan/revision_plan.instruction_authorized.json`（SHA-256 `148C6D4C…3F14`）
- Coverage：`<ROUND>/plan/instruction_coverage.json`（SHA-256 `8C8CF7A9…5478`）
- 授权回执：`<ROUND>/plan/instruction_based_authorization.json`（SHA-256 `7FEC7362…B172`）
- Apply Report：`<ROUND>/reports/apply_report.json`（SHA-256 `167528E6…7C31`）
- Analysis：`<ROUND>/plan/instruction_analysis.json`
- 输出：新报告写入 `C:\Users\Administrator\AppData\Local\Temp\opencode\retire_verify\verify_report.json`，**未覆盖**原 `reports/verify_report.json`

命令（实施 worktree CLI）：`verify --plan … --apply-report … --out <temp> --instruction-coverage … --instruction-authorization … --instruction-analysis …`

### 逐项对照

| 基准 | 原记录 | 本次回归 | 结论 |
|---|---|---|---|
| 目标总数 | 45 | 45 | 一致 |
| Verify 通过 | 45/45 | 45/45 | 一致 |
| 失败目标 | 0 | 0 | 一致 |
| `passed` | true | true | 一致 |
| `authorized_plan_sha256` | `148c6d4c…f14` | `148c6d4c…f14` | 一致 |
| `authorization_sha256` | `10fac4f2…c2b` | `10fac4f2…c2b` | 一致 |
| 字符级格式完整性 | PASS，19,084 字符 | PASS，19,084 字符 | 一致 |
| `format_operation_contract_integrity` | PASS | PASS | 一致 |
| 未跟踪文本违规 | 0 | 0 | 一致 |
| 独立文本检查 | 58 项（原报告） | 未重新运行（只读边界） | 原记录未改动 |
| 独立格式检查 | 25 项（原报告） | 未重新运行 | 原记录未改动 |
| Final Gate | READY / PATCH_READY（原报告） | 未重新运行；不触发 Word COM/PDF | 原报告 SHA `EF883151…187A174` 未改动 |

旧授权回执（含 `format_requirements_present:false`）与新分支**完全兼容**，verify 退出码 0；无需人工修改回执或 Plan。退出码 0 仅作为命令执行记录，结论以报告字段逐项对照为准。

原始 Plan/Coverage/授权/Apply/Verify/Final Gate 报告 SHA-256 在回归前后未变（见上表）。

---

## 7. 基础格式保护与 Final Gate 验证

- 定向测试中 `test_character_format_integrity.py`、`test_format_operation_integrity.py`、`test_format_preservation.py`、`test_script_format_integrity.py`、`test_target_scoped_format.py`、`test_revision_scoped_format.py`、`test_final_gate.py`、`test_full_workflow_integration.py` 全部通过。
- 基础模块 `document_engine.py`、`plan_compiler.py`、`insert_authorization.py` 与基线零差异；退役未触及普通 FormatChange、混合 Run、上下标、段落插入、目标绑定与零静默回退机制。
- Final Gate 的 `--format-gate-report` 外部兼容接口保留；`format_requirements_present:true` 的旧回执仍会要求 FR 文件锚，属于**预期兼容行为**，本轮旧回执为 `false`，未触发。
- 未运行 Final Gate 与 Word COM PDF 导出，避免覆盖原 PDF。

---

## 8. 兼容性阻断与回归问题

| 项 | 状态 | 说明 |
|---|---|---|
| `COMPATIBILITY_BLOCKER` | 无 | 无活动脚本依赖退役接口 |
| 新增回归 | 无 | 3 个失败在基线完全复现 |
| 原有失败 | 3 | 见 §5；不自行修复 |
| 消失的旧失败 | 无 | 基线 3 = 实施 3，名称一致 |
| 外部依赖未知 | 1 | 仓库外脚本/BAT 未纳入扫描范围（[UNKNOWN]），需发布前确认 |
| 真实 DOCX 结果 | 通过 | 45/45、CFI PASS、0 未跟踪文本 |

---

## 9. 结论与下一阶段建议

**判定：`PASS_FOR_RETIREMENT_REVIEW`。** 可以进入退役验收，但必须同时保留以下事项：

1. 三个原有失败应在退役验收中作为已知基线问题登记，不得归因于本次退役，也不得通过修改断言消除。
2. 发布前确认仓库外是否存在调用已退役 CLI 参数/模块的脚本（[UNKNOWN]，本地扫描为 0）。
3. 保留 `--format-gate-report`、`--instruction-analysis` 与旧 Schema 字段的兼容入口，确保历史产物仍可读。
4. 本报告不授权删除任何代码；退役执行须另行评审与实施。

报告首次提交 SHA：`PENDING_FIRST_COMMIT`
报告链接：`https://github.com/Tuzhidingdong/co_reviewer/blob/audit/retirement-regression-20261128/RETIRE_BATCHES_REGRESSION_REPORT.md`
