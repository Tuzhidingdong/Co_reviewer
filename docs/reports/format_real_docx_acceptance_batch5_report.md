# Phase 3 / Batch 5 — Full Regression & Controlled Real DOCX Acceptance 报告

日期：2026-11-28

## 最终判定

`REAL_DOCX_ACCEPTANCE_BLOCKED` — 阻断发生在**第二阶段：真实 DOCX 来源识别**，**未执行真实 Apply**，原始 DOCX 未被修改。

原因：现有 V3 盲测流程指定的是**三份 DOCX + 一份指令 TXT 的角色绑定集合**，并且每份 DOCX 存在多个版本；没有任何报告明确指定单一 DOCX 作为本轮验收对象。按指令不得自行挑选，因此停止在真实 Apply 之前。

## 1. 基线、分支与提交

- Batch 4 基线 HEAD：`ceef221dc26bcbe15220e5187d3abb6aa76b88b9`（检定时 clean）
- Batch 1–4 实现/报告提交均在祖先链上（已逐一验证）。
- Batch 5 分支：`feat/format-real-docx-acceptance-batch5-20261128`
- Batch 5 唯一代码提交（最小定向修复）：`179c2c571cc690002762d677f4651fc84e8172fc`（`fix: block concurrent FR version forks (T11-15) with synthetic regression`）。本轮除该修复外无其他代码改动，报告与验收证据索引在最后的 docs commit 中提交。
- 本报告中的 commit SHA 由紧随其后的 docs commit 回填。
- 未 push、未创建 PR、未改 remote、未修改 Batch 1–4 历史提交。

## 2. 第一阶段：合成回归

### 2.1 Batch 1–4 定向测试（本次实际重跑）

| 批次 | 命令 | 结果 | 退出码 |
|---|---|---|---|
| Batch 1 | `PYTHONPATH=scripts python -m unittest discover -s tests -p test_format_requirements_batch1.py` | 15/15 PASS | 0 |
| Batch 2 | `... -p test_run_format_analysis_batch2.py` | 12/12 PASS | 0 |
| Batch 3 | `... -p test_format_compiler_batch3.py` | 19/19 PASS | 0 |
| Batch 4 | `... -p test_format_execution_batch4.py` | 30/30 PASS | 0 |
| Batch 5 补齐 | `... -p test_batch5_matrix_gaps.py` | 3/3 PASS | 0 |

### 2.2 T01–T11 矩阵逐项核对

| ID | 风险 | 实际覆盖测试 | 结论 |
|---|---|---|---|
| T01 | FR 权威唯一、id/fingerprint 贯穿 | Batch1 `test_valid_format_requirement_analysis`、`test_fingerprint_semantic_boundary`；Batch3 `test_cli_compile_plan_integration`；Batch4 `test_instruction_based_receipt_and_fr_anchor_are_checked` | PASS |
| T02 | 语义错误/歧义不静默编译 | Batch1 `test_invalid_token_and_properties`、`test_scope_missing`、`test_missing_required_semantic_field`；Batch3 `test_ambiguous_target_blocked` | PASS |
| T03 | Run 边界与可追溯偏移 | Batch3 `test_cross_run_target`、`test_unicode_target_compiles`；Batch4 `test_apply_cross_run_and_partial_run_targets` | PASS |
| T04 | 安全基础 Run，不继承脚本 | Batch2 `test_inherited_script_is_detected`、`test_safe_base_classes`、`test_unresolved_context_never_becomes_safe`；Batch3 `test_unsafe_direct_script_blocked`、`test_styled_superscript_blocked`；Batch4 `test_preflight_blocks_unsafe_base_run` | PASS |
| T05 | Pass-2 逐字符覆盖、无冲突 | Batch3 `test_multiple_properties_become_distinct_operations`、`test_compile_single_safe_run`；Batch4 `test_end_to_end_real_apply_verify_and_gate`、`test_explicit_false_property_is_verified` | PASS |
| T06 | Gate 修复不得删语义 | Batch4 `test_gate_repair_cannot_change_semantics_or_delete_ops`、`test_gate_blocks_failed_and_missing_fr`、`test_final_gate_cli_honours_format_guard` | PASS |
| T07 | Apply→Verify 真实一致性 | Batch4 `test_end_to_end_real_apply_verify_and_gate`、`test_cli_apply_and_verify_with_preflight` | PASS |
| T08 | 生命周期/授权/Receipt 非回归 | Batch4 `test_instruction_based_receipt_and_fr_anchor_are_checked`；既有 `test_instruction_lifecycle.py`、`test_instruction_based_authorization.py`、`test_final_gate.py` | PASS |
| T09 | 文档完整性与非目标隔离 | Batch4 `test_verify_detects_non_target_text_change`、`test_apply_inside_table_and_numbering`（fail-closed）、`test_recompute_missing_context_blocked` | PASS（复杂表格写入仍 BLOCKED） |
| T10-01…12 | 样式继承/脚本污染 | Batch2 12 项全部覆盖；Batch3 `test_adjacent_script_run_does_not_contaminate_map`、`test_table_and_numbering_context_compiles`；Batch4 `test_recompute_from_paragraph_style_context`、`test_unresolved_recompute_blocks_preflight` | PASS；T10-07 由 Batch5 `test_t10_07_complex_script_flags_are_not_applied_without_context` 补齐 |
| T11-01…16 | 版本完整性 | Batch1 15 项（含 `test_cycle_and_broken_current`、`test_history_tamper_and_duplicate_version_content`、`test_parent_missing_and_fingerprint_mismatch`、`test_external_anchor_roundtrip_and_tamper`）；Batch4 `test_trace_tamper_detected` 及 Gate 保护测试 | T11-15 **原有缺口**：并发分叉此前可通过校验；已最小修复并由 Batch5 `test_t11_15_concurrent_version_fork_is_blocked` 覆盖，合法单链由 `test_t11_10_legal_single_parent_chain_still_valid` 保护 |

### 2.3 完整套件

命令：`PYTHONPATH=scripts python -m unittest discover -s tests -v`
结果：Ran 854 tests，851 PASS、2 FAIL、1 ERROR，退出码 `1`（含本轮新增 3 项矩阵补齐测试）。较 Batch 4（851 tests）增加 3 项，未出现新的失败项。

基线对照：创建临时 detached worktree `a7e32f1b79994173439cb2755ef52a9c99ce872b` 后逐项运行，三项失败名称、类型与位置完全一致：

- `test_author_decision_can_be_patch_provenance_without_fabricating_comment_ids`（numbering transaction）
- `test_two_target_compiled_plan_text_then_format_change_passes_verify`（two-pass overlap）
- `test_repreflight_of_changed_approved_source_requires_renewed_approval`（revision workflow）

临时 worktree 已清理，未写回正式工作区。基线失败明细（与当前轮次相同）：
- numbering transaction：`RevisionModelError: PatchOperation requires exact source_comment_ids…`，测试期望正则 `"source_comment_ids or a hash-bound author_decision_ref"` 与实际错误消息不匹配。
- two-pass overlap：`AssertionError: False is not true : {'scope': 'Mechanical DOCX Track Changes verification…'}`（既有 format-map/script 问题）。
- revision workflow：`ValueError: Human author approval validation failed:` / `author_approval scope changed for R1-C1`。

三份原始 DOCX 在本轮前后 SHA-256 保持不变（见第 3.1 节表格，第二次复核一致）。

### 2.4 非格式工作流回归

`test_plan_compiler.py`、`test_document_engine_module.py`、`test_final_gate.py`、`test_format_preservation.py`、`test_format_operation_integrity.py`、`test_instruction_lifecycle.py`、`test_schema_contracts.py` 等均 PASS；文本替换、删除、插入、段落、表格、编号、Reviewer、Intent、Coverage、Authorization、Receipt、Plan、Apply、Verify、Final Gate 与 DOCX 生命周期未见新增回归。Batch4 CLI 真实回合 Apply 2/2、Verify 2/2 通过。

## 3. 第二阶段：真实 DOCX 来源识别与只读检查

### 3.1 指定对象判定

证据目录：`outputs/format_real_docx_acceptance_batch5_blocked/candidate_index.json`

指定集合来自 `blind_test_real_docs_20261128/FINAL_REPORT.md` 第 19–26 行：三份 DOCX（Main / Cover Letter / SI）+ 一份 `text_revision_instructions.txt`，均以 SHA-256 绑定。只读复核的大小与 SHA 全部一致：

| 角色 | 文件 | 大小 | SHA-256 |
|---|---|---:|---|
| Manuscript | `art_file_water_WangChunli_clean.docx` | 4,478,596 | `2c7f55b85721f1f73acf300e25367bdcc202e25f9ad3feb3e5cf0205c0dfa051` |
| Cover Letter | `Cover_Letter_Nature_Water.docx` | 29,744 | `9fbf54eba50d7080405babc6630640077f7ad88d0654ffb64b54499156964cd5` |
| SI | `SI_water_WangChunli_clean.docx` | 5,001,910 | `bdd617faa4aa158e038f7ab32cae0aa6d2763088928ff555a4199b8f58f84c78` |
| 指令 | `text_revision_instructions.txt` | 22,692 | `d544523bf733add1bfeff5fc4d0112ba9caacaada592652268d3051c30fcdb5f` |

歧义：每个角色存在 immutable 原件、V3 input 副本、V3 output clean/marked、工作区根目录 delivered clean/marked 多个版本；两份 clean Cover Letter 同为 26,725 字节但 SHA 不同。没有报告单独指定某一 DOCX。因此**未选择任何文件**，`REAL_DOCX_APPLY` 未执行。

### 3.2 只读结构分析与 Run 分析

证据：`outputs/format_real_docx_acceptance_batch5_blocked/readonly_structure_analysis.json`

| 文档 | 段落/Run/表格 | 编号段落 | 直接脚本 Run | 重复段落文本 | Run 分类 |
|---|---|---|---|---|---|
| Manuscript | 81 / 1236 / 0 | 0 | 127 | 2 | SAFE_WITH_COMPATIBLE_CONTEXT 1089，UNSAFE_SCRIPT_FORMAT 140 |
| Cover Letter | 17 / 94 / 1 | 0 | 18 | 0 | SAFE_WITH_COMPATIBLE_CONTEXT 63，UNSAFE_SCRIPT_FORMAT 18 |
| SI | 81 / 935 / 0 | 0 | 136 | 11 | SAFE_WITH_COMPATIBLE_CONTEXT 767，UNSAFE_SCRIPT_FORMAT 147 |

三份均有 `styles.xml` 与 `theme1.xml`；Batch 2 分析规则 `run-safety/1.0` 正常；分析输出的 `source_docx_sha256` 与各自输入完全一致。没有出现 `UNRESOLVED_EFFECTIVE_FORMAT`，说明必要上下文可解析。

支持：普通正文段落、字符/段落样式、直接上下标、表格单元格段落（Cover Letter 4 个 Run 标记 in_table）、重复文本识别。
不支持/限制：表格内的 Pass-2 格式写入仍由现有引擎 `FORMAT_CHANGE_TABLE_STYLE_RESOLUTION_REQUIRED` fail-closed；编号段落在这三份输入中不存在，实际编号上下文未覆盖；重复段落文本需要结构证据消歧。

### 3.3 Authorization / Receipt 只读复核

证据：`outputs/format_real_docx_acceptance_batch5_blocked/authorization_recheck.json`

对 V3 盲测轮已有 `instruction_based_authorization.json` + `instruction_coverage.json` + `revision_plan.instruction_authorized.json` + `instruction_analysis.json` 调用现有 `validate_instruction_based_authorization()`，结果 `errors=[]`、`passed=true`。但该 receipt 绑定的是**上一轮旧 Plan**，不覆盖本轮任何新 FR/新编译结果，因此**不能**作为本轮 Apply 的授权依据。本轮没有伪造或扩展任何 Receipt。

### 3.4 阻断说明

按指令 4.1：“如果现有 V3 盲测目录中存在多个无法确定哪个是指定对象的真实 DOCX，不得自行挑选。应报告候选文件及证据，停止真实 Apply。” 因此：

- `REAL_DOCX_READ_ONLY_PASS`：三份指定 DOCX 的只读结构/Run 分析完成且哈希一致。
- `REAL_DOCX_APPLY_PASS`：未执行（N/A）。
- `REAL_DOCX_VERIFY_PASS`：未执行（N/A）。
- `REAL_DOCX_FINAL_GATE_PASS`：未执行（N/A）。

## 4. 最小定向修复

只读矩阵核对发现 T11-15 并发版本分叉此前未被 FR 校验拦截（合成探针 `FORK_VALIDATION_ERRORS []`）。已在 `format_requirements.py` 增加“同父多子即并发分叉 → BLOCKED”校验，并新增 `tests/test_batch5_matrix_gaps.py` 三项测试（分叉阻断、合法单链仍有效、complex script 标志适用范围）。未针对任何论文文字或作者硬编码，未删除既有失败证据。

## 5. 真实 DOCX 失败分类

本次属于指令第八节**情况 B/来源歧义**：没有明确指定的单一真实对象，也没有覆盖新编译结果的 Receipt。结果 `BLOCKED BEFORE APPLY`，未产生 Apply 输出、未修改任何原始 DOCX、未执行 Verify/Final Gate。

## 6. 最终判定与十个问题

1. **T01–T11 与 Batch 1–4 回归是否通过？** 通过（T11-15 原缺口已修复并补测；复杂表格写入仍按既有规则阻断）。
2. **三项历史基线失败是否一致？** 是，名称、类型、位置与 `a7e32f1` detached worktree 完全一致。
3. **实际测试的真实 DOCX 是什么？** 未选定。指定的是三份 DOCX + 一份指令 TXT 的集合，证据见 `candidate_index.json`。
4. **Authorization / Receipt 是否与具体 Plan 一致？** 旧轮 receipt 通过现有校验，但绑定旧 Plan，不授权本轮新编译；未伪造。
5. **真实文档表格/编号/样式继承/Run 是否都能分析？哪些 BLOCKED？** 只读分析全部完成；表格结构可识别，表格内格式写入 fail-closed；三份输入无编号段落；未放宽 UNRESOLVED 判定。
6. **真实 DOCX Apply 是否用正常流程执行？** 否，未执行。
7. **输出 DOCX 重新读取后逐条 FR Verify 是否通过？** 未执行（无输出）。
8. **插入字符格式覆盖是否正确？** 合成 DOCX 上已验证；真实 DOCX 未执行。
9. **Final Gate 是否核查所有 FR 并拒绝语义修改？** 合成测试已验证；真实 DOCX 未执行。
10. **V3 是否具备真实 DOCX 格式处理可验收能力？** 合成闭环完整；真实验收仍 BLOCKED，需用户明确指定单一 DOCX 与角色、并提供覆盖新 FR/Plan 的 Authorization/Receipt。适用范围限制：表格内格式写入、编号上下文、复杂多 FR 分叉需进一步证据。

## 7. 最终 git status

报告与验收证据提交后应为：`## feat/format-real-docx-acceptance-batch5-20261128`，工作区 clean。历史提交不变；本轮唯一代码提交为 `179c2c5`，其后为 docs/evidence 提交。
