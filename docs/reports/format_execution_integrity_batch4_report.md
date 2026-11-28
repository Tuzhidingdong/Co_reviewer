# Phase 3 / Batch 4 — Format Execution Integrity 报告

日期：2026-11-28

## 1. 基线、分支与提交

- Batch 3 基线 HEAD：`c55d102b5f07f3df28315169a68691f8d438eb01`（检定时工作区 clean）
- Batch 1 `1171590…`、Batch 2 `0a6ae17…`、Batch 3 `89b95bb…` 实现提交均确认为祖先链。
- Batch 4 分支：`feat/format-execution-integrity-batch4-20261128`
- Batch 4 实现 commit：`BATCH4_COMMIT_PLACEHOLDER`
- 未 push、未创建 PR、未改 remote、未修改 Batch 1–3 历史提交。

## 2. 修改文件及目的

| 文件 | 状态 | 目的 |
|---|---|---|
| `scripts/co_reviewer/format_execution.py` | 新增 | RECOMPUTE 确定性计算、target occurrence 身份、Apply Preflight、执行记录、独立 Verify、Final Gate 保护 |
| `scripts/co_reviewer_cli.py` | 修改 | `compile-plan` 在编译后完成 recompute/occurrence；`apply` 新增 Preflight 硬门槛与执行记录；`verify` 新增独立格式 Verify；`final-gate` 新增格式 guard 输入 |
| `scripts/co_reviewer/final_gate.py` | 修改 | `evaluate_final_gate` 接受可选 `format_gate`，未全部 VERIFIED 时阻断 |
| `scripts/co_reviewer/format_compiler.py` | 修改（Batch 3 接口补齐） | 生成的格式 target 增加 `applies_to_target_index`，使现有 Apply/Verify 作用域机制授权插入字符上的 FormatChange |
| `tests/test_format_execution_batch4.py` | 新增 | 30 项合成 DOCX 的真实执行/验证/Gate 测试 |
| `docs/reports/format_execution_integrity_batch4_report.md` | 新增 | 本报告 |

未重写 `document_engine.py`、`plan_compiler.py`、`v232_contract.py`；未修改 FR 语义、Batch 1 外部锚点或 Batch 2 解析规则。

## 3. 真实执行链（实际代码路径）

```
FR (instruction_analysis.json + format_compiler.py)
  -> Coverage (format_requirement_ids only)
  -> Plan (compile_revision_plan -> apply_compiled_formats)
  -> Run Analysis (analyze_docx)
  -> Format Compiler (compile_format_requirements)
  -> Batch 4 recompute/occurrence (compile-plan 内)
  -> Apply Preflight (cmd_apply, apply_plan 之前)
  -> Apply (apply_plan -> prepare_document -> replace_in_paragraph / apply_format_operation)
  -> Verify (verify_apply_report + verify_format_execution 重新打开输出 DOCX)
  -> Final Gate (evaluate_final_gate + format_gate)
```

真实格式写入由 `document_engine.apply_format_operation` 完成，Track Changes 快照由 `v232_contract.attach_format_change` 写入。Batch 4 不另建执行链。

## 4. `RECOMPUTE` 规则

- 规则版本：`recompute/1.0`。
- 输入：目标段落上下文（`pStyle`、`pPr/rPr`、`in_table`/`in_numbering`）、`styles.xml`、`theme1.xml`、`docDefaults`；**排除**基础 Run 的直接 `rPr` 与字符样式。
- 计算：调用现有 `effective_run_properties` 解析目标上下文，逐属性取确定值；支持 `rFonts`/`sz`/`szCs`/`color`。
- 输出：写入 `trace.results[i].recompute = {rule_version, status, context, properties{value, source, operation_id}}`，并为每个 recompute 属性生成带 `recomputed=true` 的显式 Pass-2 操作（同一 FR scope）。
- 阻断：目标段落缺失、上下文解析失败、无确定性规则、值为空 → `status=BLOCKED`，写入 `blockers`，Trace 状态变 `BLOCKED`。
- 确定性：相同 DOCX + 相同规则版本 → 相同 `compile_fingerprint`（测试 `test_recompute_is_deterministic`）。
- 示例（段落样式 P2）：`rFonts = {"ascii":"Times New Roman","hAnsi":"Times New Roman"}`，来源 `PARAGRAPH_STYLE`。

Preflight 拒绝 `recompute.status` 不是 `NOT_REQUIRED`/`RESOLVED` 的编译结果。

## 5. `target_occurrence_id`

- 规则版本：`target-occurrence/1.0`；格式 `to-<sha256[:32]>`。
- 稳定输入：DOCX SHA-256、part、`p_idx`、source 起止偏移、覆盖 Run 起止索引、`find` 文本、结构上下文；canonical JSON 序列化，不依赖对象 ID、字典顺序或随机遍历。
- 校验：Preflight 对当前 DOCX 重算并逐 FR 比对；同一 occurrence 的多 FR 逐项比较 `replace`、`scope`、格式属性值；不同替换文本或同属性不同值 → BLOCKED。

## 6. 多 FR 关联与冲突

- 允许同一 occurrence 多个 FR，但必须保持独立 FR ID、版本 ID、Trace 条目和 Pass-2 操作。
- 冲突类型：替换文本不同、scope 不同、同一属性不同值 → 阻断，不使用“最后一个操作覆盖”。
- 测试覆盖：兼容多 FR、冲突值、不同替换文本、相同文本不同段落、旧 DOCX 身份失效。

## 7. Apply Preflight 调用入口与顺序

入口：`co_reviewer_cli.cmd_apply`，当传入 `--format-run-analysis`（及 `--format-analysis`）时，在 `apply_plan` **之前**执行；失败即抛错，不写任何 DOCX。

检查顺序：
1. 文件/DOCX 存在；Plan `source_sha256`、Run Analysis SHA、Compile Trace SHA 与当前 DOCX 三方一致。
2. Run Analysis schema/规则版本；FR 完整性与当前版本；Coverage 引用。
3. Compile Trace Schema 与 `compile_fingerprint` 自洽；状态必须 `COMPILED`。
4. 逐 FR 状态（`COMPILED`/`UNCOVERED`/`UNCOMPILED`/`MISSING_OR_STALE_FR`/`COMPILE_BLOCKED`）。
5. 基础 Run 安全类别、RECOMPUTE 状态、`BLOCKED_IF_UNKNOWN`、脚本污染。
6. Map 连续性与 Pass-2 操作 FR 身份。
7. target occurrence 重算与多 FR 冲突。
8. Authorization/Receipt：复用 `validate_instruction_based_authorization`（含 Batch 1 FR 外部锚点）或 `_validate_author_approval_record`。

阻断信息带具体 FR、target、操作与原因，不是泛化 `PRECHECK_FAILED`。

## 8. Authorization / Receipt 路径

Preflight 不建立第二套授权：`instruction_based` 计划必须提供 receipt，并调用现有 `validate_instruction_based_authorization`（校验 plan/Coverage 文件 SHA、FR 锚点、目标范围、输入 DOCX SHA）；`author_approval` 调用现有 `_validate_author_approval_record`。测试 `test_instruction_based_receipt_and_fr_anchor_are_checked` 生成合法 receipt 通过 Preflight，篡改 FR 分析文件后因锚点失配被阻断。

## 9. Map 与 Pass-2 接入 Apply 的真实路径

`compile-plan --run-format-analysis` 将 Batch 3 的 `replacement_format_map` 与 Pass-2 操作写入 Plan target；`apply_plan` 原有代码把它们放入 edit，由 `prepare_document` 在 Pass 1 执行映射替换、Pass 2 执行 `apply_format_operation`。Batch 4 只为格式 target 补充 `applies_to_target_index`，使现有 `authorized_ranges` 机制正确授权插入字符上的格式覆盖（此前会出现 Verify `format_integrity` 失败）。

## 10. 合成 DOCX 的真实执行证据

- 原始 DOCX：临时目录中的 `source.docx`（`w:p` 含 `Use ` / `token-A` / ` safely.` 三个 Run）。
- 输出 DOCX：独立路径 `marked.docx`，原始文件保留。
- 输入 SHA 示例：`0903bf7b81dbe2e33be80f92e2cfef0227d41f8e1182d72577372e1cdfbd03ef`
- 输出 SHA 示例：`b87d737140e6158a0df7636a6ce3c0162d2c86c02d8e9115f7da199c0d98aee1`
- 实际输出 XML 证据：`token-A` 经 Track Changes 删除/插入后，插入文本被拆成逐字符 Run，覆盖 `token-A` 的 Run 均含 `<w:vertAlign w:val="superscript"/>`；`" safely."` 未受影响。
- 执行记录示例：

```json
{
  "preflight_status": "PASSED",
  "compile_fingerprint": "386d8f4501b8d3ceca135787c772b72b5adc2f763eb14c1244bc4cbb301c31b2",
  "requirements": [{
    "format_requirement_id": "FR-REQ-T-01-01",
    "version_id": "FR-REQ-T-01-01-v1",
    "target_occurrence_id": "to-d9b32752a83902c5a240fce4f438edbf",
    "format_operations": [{"operation_id": "7c1592ac0ab47a5fe0995d14",
                           "properties": {"vertAlign": "superscript"},
                           "recomputed": false}],
    "execution_status": "APPLIED"
  }]
}
```

## 11. Verify：逐 FR、逐属性结果与证据

`verify_format_execution` 重新打开输出 DOCX，独立读取 `w:rPr` 与 `effective_run_properties`，逐字符核对 FR 属性；同时比对原文/输出非目标文本。

实际结果示例：

```json
{
  "verified_docx_sha256": "b87d737140e6158a0df7636a6ce3c0162d2c86c02d8e9115f7da199c0d98aee1",
  "passed": true,
  "results": [{
    "format_requirement_id": "FR-REQ-T-01-01",
    "status": "VERIFIED",
    "expected": {"vertAlign": "superscript"},
    "errors": [],
    "non_target_text_preserved": true,
    "first_check": {
      "position": 4, "run_index": 2, "property": "vertAlign",
      "expected": "superscript", "actual": "superscript",
      "direct": "superscript", "provenance": "DIRECT_RUN", "passed": true
    }
  }]
}
```

负向证据：删除输出 DOCX 的 `vertAlign` → `FAILED`；改成 `subscript` → `FAILED`；修改非目标文本 → `non_target_text_preserved=false`。CLI `cmd_verify` 在格式 Verify 失败时抛错，不返回成功。

## 12. Final Gate 判定与修复保护

`format_final_gate_guard` 以 FR 为起点：逐条当前 FR 检查编译存在、版本一致、fingerprint 一致、Pass-2 未删除、VERIFIED 证据存在、compile fingerprint 一致。示例：

```json
{"rule_version": "format-gate/1.0", "passed": true,
 "blockers": [], "fr_status": {"FR-REQ-T-01-01": "VERIFIED"}}
```

`evaluate_final_gate(..., format_gate=...)` 把未通过或存在非 VERIFIED FR 转为 gate blocker。修复保护测试证明：删除 FR、改变 token 语义但未创建新版本、删除 Pass-2、FR FAILED/MISSING 均阻断；合法实现层修复不改变 FR 语义时可通过。表格内的 FormatChange 由既有引擎 `FORMAT_CHANGE_TABLE_STYLE_RESOLUTION_REQUIRED` 阻断，Batch 4 未绕过。

## 13. 测试命令、退出码与结果

- `test_format_execution_batch4.py`：30/30 PASS，退出码 `0`。
- `test_format_requirements_batch1.py`：15/15 PASS，退出码 `0`。
- `test_run_format_analysis_batch2.py`：12/12 PASS，退出码 `0`。
- `test_format_compiler_batch3.py`：19/19 PASS，退出码 `0`。
- 相关回归：`test_final_gate.py`、`test_format_operation_integrity.py`、`test_format_preservation.py`、`test_script_format_integrity.py`、`test_instruction_lifecycle.py`、`test_schema_contracts.py`、`test_final_round_verification.py`、`test_plan_compiler.py`、`test_document_engine_module.py`、`test_instruction_based_authorization.py` 全部 PASS，退出码 `0`。
- 完整套件：`PYTHONPATH=scripts python -m unittest discover -s tests -v`；结果：Ran 851 tests，848 PASS、2 FAIL、1 ERROR，退出码 `1`。较 Batch 3（821 tests）增加 30 项（本批新增），未出现新的失败项。
- 三个基线既有失败复核：仍为完全相同的三项、相同失败类型与位置（numbering transaction 错误消息正则不匹配、two-pass overlap 既有 format-map/script 问题、revision workflow 的 `author_approval scope changed for R1-C1`），均已在未修改的 `a7e32f1` 基线树中复现，与 Batch 4 无关。

## 14. V3 非格式工作流兼容性

文本替换、删除、插入、段落结构、表格、编号、参考文献、Reviewer、Intent、Coverage、Authorization、Receipt、版本控制与生命周期均未改动；CLI 新增参数全部可选，不传时行为与 Batch 3 一致。真实 CLI round-trip 测试中 Apply 2/2、Verify 2/2 通过。

## 15. 未解决问题与 BLOCKED

- 表格/编号内的直接格式写入仍被既有引擎 fail-closed 阻断（需后继批次按已有接口提供表格样式解析证据），Batch 4 未绕过。
- 复杂多 FR 指向同一 target 且替换文本不同的场景目前阻断，需回到 Intent/Reviewer 流程产生新 FR 版本。
- 完整套件三项遗留失败均为基线既有问题，不在本批次范围。
- 实现 commit 提交后工作区：clean。
- 回填本报告 SHA 字段的 docs-only commit 提交后工作区：clean。
- Batch 1–3 分支与提交保持不变；`master` 仍指向 `a7e32f1b79994173439cb2755ef52a9c99ce872b`。

## 16. 完成后回答

**问题 1：所有 RECOMPUTE 是否在 Apply 前解析为确定值？** 是。规则版本 `recompute/1.0`，在 `compile-plan` 内完成；缺上下文即 BLOCKED，Preflight 拒绝未决项。

**问题 2：能否精确区分多个 FR 的不同 target occurrence？** 能。`to-<sha>` 由 DOCX SHA、结构位置、字符/Run 范围与文本证据确定性生成，旧 DOCX 身份即时失效。

**问题 3：范围或格式值冲突是否可靠阻断？** 是。同一 occurrence 的替换文本、scope、属性值逐项比较，冲突 BLOCKED，不采用后者覆盖。

**问题 4：Preflight 能否发现身份/完整性不一致？** 能。覆盖 FR、DOCX、Run Analysis、Map、Pass-2、Trace、target identity 与 Authorization/Receipt，并给出具体阻断原因。

**问题 5：现有 Apply 是否真的读取并执行 Batch 3 结果？** 是。真实合成 DOCX 上执行了 map 替换与 Pass-2 `vertAlign=superscript`，输出 DOCX 的 Run 含对应 `w:rPr` 与 Track Changes 记录。

**问题 6：Verify 是否读取实际输出 DOCX 并独立核验？** 是。重新打开输出 DOCX，逐字符读取 Direct/Effective 格式并对照 FR；能发现漏写、错写与非目标污染。

**问题 7：Final Gate 是否确保全部 FR VERIFIED 且修复不能删除语义？** 是。以 FR 为起点核对 VERIFIED、版本、fingerprint、Pass-2 与 compile fingerprint；删除/改语义均阻断。

**问题 8：是否形成可进入 Batch 5 盲测的完整闭环？还有哪些风险？** 合成 DOCX 上形成完整闭环。风险：真实论文 DOCX 的复杂表格/编号样式、并发多 FR 分叉、人工授权接收证据在真实批次中的一致性尚未由合成测试覆盖，需 Batch 5 盲测验证。
