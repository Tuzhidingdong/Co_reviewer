# Phase 3 / Batch 3 — Deterministic Format Compiler 报告

日期：2026-11-28

## 1. 基线与分支

- Batch 2 基线 HEAD：`6474ed2c51b04df7d06c0c6d16cc6a0997ceac24`（检定时工作区 clean）
- Batch 1 实现 `1171590f33ca12340ffcd15ab1187bf2b7d8a112`、Batch 2 实现 `0a6ae17055433f91ddef3377532b09875abce6f8` 均已确认为 HEAD 祖先。
- Batch 3 本地分支：`feat/deterministic-format-compiler-batch3-20261128`
- Batch 3 实现 commit：`89b95bb288c3b649bdde45c4c56447ac73750fb1`（`feat: Batch 3 deterministic format compiler and Pass-2 trace`）。
- 本报告中的 commit SHA 由紧随其后的 docs-only commit 回填；实现代码、Schema、测试均在上述实现 commit 中，之后未再改动。
- 未 push、未创建 PR、未添加/修改 remote、未重建基线、未覆盖 Batch 1/2 提交。

## 2. 修改文件及目的

| 文件 | 状态 | 目的 |
|---|---|---|
| `scripts/co_reviewer/format_compiler.py` | 新增 | FR 完整性/身份校验、Run 边界定位、安全候选选择、`replacement_format_map`、Pass-2 操作、`format_compile_trace`、Plan 合并 |
| `schemas/format-compile-trace.schema.json` | 新增 | Compile Trace 的 Draft 2020-12 Schema |
| `scripts/co_reviewer_cli.py` | 修改 | `compile-plan` 增加 `--run-format-analysis`；有 FR 时在正常编译路径内调用编译器；无 FR 时保持原 Plan 不变 |
| `schemas/revision-plan.schema.json` | 修改 | Plan 顶层 `format_compilation`、target 的 `format_requirement_ids`、format operation 的 FR 关联字段 |
| `scripts/co_reviewer/run_format_analysis.py` | 修改（接口扩展） | 分析结果增加 `structure_context` 与 `source_docx_sha256`，供编译器做结构一致性/身份校验；未改变解析与分类规则 |
| `tests/test_format_compiler_batch3.py` | 新增 | 19 项合成 DOCX / 合成 FR / 合成 Plan 测试 |
| `docs/reports/deterministic_format_compiler_batch3_report.md` | 新增 | 本报告 |

未修改 `plan_compiler.py` 的文本编辑/映射逻辑、`document_engine.py` 的写入逻辑、`v232_contract.py` 的样式解析、`format_requirements.py` 的 FR 语义与指纹、Apply/Verify/Final Gate 流程。

## 3. V3 Plan Compiler 真实接入位置与调用路径

`cmd_compile_plan`：

```
intents -> _resolve_instruction_analysis (Batch 1 校验)
        -> crosscheck_coverage (Batch 1 FR 引用校验)
        -> compile_revision_plan (原有编译)
        -> has_format_requirements ? 否: 原 Plan 不变
                                    是: compile_format_requirements(plan, analysis, coverage, run_analysis)
                                        -> validate_compile_trace
                                        -> BLOCKED => 抛错阻断
                                        -> COMPILED => apply_compiled_formats(plan, trace)
        -> crosscheck_plan -> validate_plan -> validate_plan_insert_authorizations -> write
```

编译器不是孤立 CLI：只有 `compile-plan` 正常路径调用；编译失败直接阻断，不产出 Plan。

## 4. FR → 目标文本范围的解析

1. `validate_format_requirements()` 验证 FR 版本链与指纹；按 `format_requirement_id` 只取 `current_version_id` 指向的版本，并再次核对 `semantic_fingerprint`。
2. Coverage 只提供 `format_requirement_ids` 引用；缺失/非当前引用 → BLOCKED。
3. FR `target_ref` 给出 `part`、`p_idx`、`revision_item_id`、`target_index`，定位 Plan 中的精确 target。
4. 取 target 的 `find`/`replace`，用 `visible_text(paragraph)` 确认 `find` **恰好出现一次**；重复 `source_excerpt` 且结构证据不能唯一确定时 BLOCKED。
5. 用 `token` 与 `scope.start` 在 `replace` 中核对 token；token 不在 replace 中或 scope 与实际不一致 → BLOCKED。
6. 用 `run_plain_text` 建立段落 Run 字符跨度，将 `find` 内偏移映射到 Run 边界，支持一 Run、跨两/多 Run、部分覆盖、Unicode、空格与标点。

## 5. 安全基础 Run 筛选与确定性选择

- 仅 `SAFE_NEUTRAL` / `SAFE_WITH_COMPATIBLE_CONTEXT` 且 `context_ready=true` 可作候选；`UNSAFE_SCRIPT_FORMAT`、`UNSAFE_STYLE_INHERITANCE`、`UNRESOLVED_EFFECTIVE_FORMAT` 直接排除。
- 目标范围内若出现 `STRIP`（有效污染属性）或 `BLOCKED_IF_UNKNOWN`，该 Run 不作映射来源。
- 先选 `SAFE_NEUTRAL`；无中性候选才用兼容上下文候选。
- 同一池内按 `(run_index, run_id)` 升序取第一个；左侧优先、结果与字典/文件遍历顺序无关。
- 没有任何安全候选 → BLOCKED，不退回原始不安全 Run。

## 6. `replacement_format_map` 结构（合成样例）

```json
[
  {"replacement_start": 0, "replacement_end": 1, "source_offset": 0},
  {"replacement_start": 1, "replacement_end": 2, "source_offset": 1},
  {"replacement_start": 2, "replacement_end": 3, "source_offset": 2},
  {"replacement_start": 3, "replacement_end": 4, "source_offset": 3},
  {"replacement_start": 4, "replacement_end": 5, "source_offset": 4},
  {"replacement_start": 5, "replacement_end": 6, "source_offset": 5},
  {"replacement_start": 6, "replacement_end": 13, "source_offset": 6}
]
```

语义：对 `"token-A" -> "token-A value"`，前 6 个字符逐字符对应源 Run 同位置；第 7 个字符与新增 `" value"` 一起继承目标 token 最后一个安全字符（`source_offset=6`）。映射是逐字符/逐片段构造的，**不复制整段 `rPr`**。输出前按 V3 现有 `validate_replacement_format_map` 契约检查连续性、范围与脚本污染。

逐属性决策的使用：`PRESERVE` 仅来自安全基础 Run 且可解析的属性；`STRIP`/`BLOCKED_IF_UNKNOWN` 使该 Run 不可作为来源；`EXPLICIT_OPERATION_REQUIRED` 不假设基础已设置，只由 Pass-2 承载；`RECOMPUTE` 记录在 Trace 中，映射只提供安全锚点，不猜测新值。

## 7. Pass-2 `format_operations` 结构（合成样例）

Trace 中的完整记录：

```json
{
  "operation_id": "7c1592ac0ab47a5fe0995d14",
  "find": "token-A",
  "properties": {"vertAlign": "superscript"},
  "scope_find": "token-A value",
  "scope_offset": 0,
  "format_requirement_id": "FR-REQ-T-01-01",
  "format_requirement_version_id": "FR-REQ-T-01-01-v1"
}
```

写入 Plan 时按 V3 现有 operation 契约裁剪字段，并用 `source_occurrence_id = "<FR id>:<version id>"` 保留追溯关联；完整 FR 关联保留在 `format_compilation`。同一 FR 多个属性 → 多个独立操作，operation_id 由 FR/版本/属性/值/范围确定性哈希生成。冲突的既有操作（同 find/scope 但属性不同或无 FR 引用）→ BLOCKED，不覆盖也不静默保留。

## 8. FR → Run → Map → Pass-2 Trace 样例

```json
{
  "schema_version": "1.0",
  "compiler_version": "format-compiler/1.0",
  "analysis_version": "run-safety/1.0",
  "status": "COMPILED",
  "source_docx_sha256": "<sha256>",
  "results": [{
    "format_requirement_id": "FR-REQ-T-01-01",
    "version_id": "FR-REQ-T-01-01-v1",
    "fingerprint": "<sha256>",
    "source_excerpt": "T-01: superscript token-A.",
    "part": "word/document.xml", "p_idx": 0,
    "find": "token-A", "replace": "token-A value", "token": "token-A",
    "source_offset": 4,
    "run_ids": ["word/document.xml:P0:R1"],
    "base_run_id": "word/document.xml:P0:R1",
    "base_selection_reason": "compatible context; deterministic by run_index",
    "safety_classifications": ["SAFE_WITH_COMPATIBLE_CONTEXT"],
    "property_decisions": {"vertAlign": "PRESERVE", "i": "PRESERVE", "rFonts": "PRESERVE", "sz": "PRESERVE"},
    "replacement_format_map": ["<见第 6 节，共 7 段>"],
    "format_operations": ["<见第 7 节>"],
    "status": "COMPILED"
  }],
  "blockers": [],
  "unresolved_requirement_ids": [],
  "compile_fingerprint": "<sha256>"
}
```

`compile_fingerprint` 覆盖整个 Trace 并排除自身；篡改任一字段可被 `validate_compile_trace()` 检出。

## 9. 表格与编号上下文传递

Batch 2 分析结果新增 `structure_context = {in_table, in_numbering}`；编译器从目标段落的 `w:pPr/w:numPr` 与祖先 `w:tbl` 重建同一结构，逐 Run 比对；不一致 → BLOCKED。表格内多段落、段落样式 + 字符样式 + 直接格式叠加仍复用 `effective_run_properties`，未新建样式引擎。

## 10. 确定性与幂等性

- 相同输入两次编译：`compile_fingerprint` 完全一致；Trace、map、ops 完全一致。
- 同一 Trace 重复 `apply_compiled_formats`：按 `find + properties` 去重，不新增重复操作，不丢失既有字段。
- 候选按 `(run_index, run_id)` 排序；FR 按 ID 排序；属性按名称排序；操作按 `scope_offset + operation_id` 排序。

## 11. 测试与真实退出码

- `test_format_compiler_batch3.py`：19/19 PASS，退出码 `0`。
- `test_run_format_analysis_batch2.py`：12/12 PASS，退出码 `0`。
- `test_format_requirements_batch1.py`：15/15 PASS，退出码 `0`。
- 相关回归：`test_plan_compiler.py`、`test_format_operation_integrity.py`、`test_format_mapping_two_defects.py`、`test_target_scoped_format.py`、`test_script_format_integrity.py`、`test_format_preservation.py`、`test_instruction_analysis_producer.py`、`test_instruction_based_authorization.py`、`test_schema_contracts.py`、`test_minfix_baseline_regression.py`、`test_v232_contract.py`、`test_document_engine_module.py` 全部 PASS，退出码均为 `0`。
- 完整套件：命令 `PYTHONPATH=scripts python -m unittest discover -s tests -v`；结果：Ran 821 tests，818 PASS、2 FAIL、1 ERROR，退出码 `1`。较 Batch 2（802 tests）增加 19 项（本批新增），未出现新的失败项。
- 三个基线既有失败复核：仍为完全相同的三项、相同失败类型与位置（numbering transaction 错误消息正则不匹配、two-pass overlap 既有 format-map/script 问题、revision workflow 的 `author_approval scope changed for R1-C1`），均已在未修改的 `a7e32f1` 基线树中复现，与 Batch 3 无关。

测试使用程序生成的合成 DOCX、合成 FR 与合成 Plan；未使用真实论文内容，未执行真实 DOCX Apply。

## 12. 兼容性检查

- 文本替换、删除、插入、段落调整等非格式操作仍由 `compile_revision_plan` 生成，本批次未改动。
- Reviewer、Intent、Coverage、Authorization、Receipt、版本控制与生命周期均未修改；编译器不建立第二套授权。
- 无 FR 时 Plan 完全不变（不生成 `format_compilation`、不新增格式操作）。
- 有 FR 时旧格式操作逐项校验：语义一致则保留全部字段，冲突或无 FR 引用则 BLOCKED。
- 未生成/修改任何 DOCX 文件；未调用 Word 写入。

## 13. 未解决问题 / BLOCKED

- `RECOMPUTE` 属性只输出安全锚点与决策，具体新值仍需目标上下文由 Batch 4 Apply 阶段按规则落实；Batch 3 不猜测值。
- 同一 target 被多个 FR 指向且 `replace` 不同时，目前按首个 Fr 的格式 target 合并，需在后续阶段细化结构定位；该场景未被合成测试覆盖。
- 完整套件遗留三项失败均为基线既有问题，不在本批次修复范围。
- 实现 commit 提交后工作区：clean。
- 回填本报告 SHA 字段的 docs-only commit 提交后工作区：clean。
- Batch 1/2 分支与提交保持不变；`master` 仍指向 `a7e32f1b79994173439cb2755ef52a9c99ce872b`。

## 14. 完成后回答

**问题 1：FR 能否准确定位到原始 DOCX 的具体字符范围和 Run？**
能。`target_ref` + `find` 唯一性 + token/scope 校验 + Run 字符跨度映射；重复文本无法唯一确定时 BLOCKED。

**问题 2：是否排除上标/下标和不安全继承 Run，并生成安全 map？**
是。三类不安全分类均被排除；污染属性或有未知属性的 Run 不作映射来源；没有安全候选即 BLOCKED。

**问题 3：`replacement_format_map` 是否逐属性安全构造，而非整段复制 `rPr`？**
是。映射按字符/片段生成，逐属性参考 Batch 2 的 STRIP/BLOCKED_IF_UNKNOWN 决策过滤来源；不整体复制邻居格式。

**问题 4：Pass-2 操作是否真正进入最终 Plan，属性和值是否保留？**
是。编译成功后写入格式 target 的 `format_operations`，`find/properties/scope_find/scope_offset` 完整保留；FR/版本关联在 Trace 与 `source_occurrence_id` 中保留；经现有 Plan 校验。

**问题 5：表格、编号、重复文本、跨 Run 和冲突操作如何处理？**
表格/编号结构上下文双向比对，不一致 BLOCKED；重复文本要求唯一命中，否则 BLOCKED；跨 Run 按字符片段映射；冲突既有操作 BLOCKED，不覆盖。

**问题 6：Batch 3 输出是否具备进入 Batch 4 的全部编译信息？**
具备 FR→Run→Map→Pass-2→Trace 的完整链条、指纹与阻断记录。接口缺口：`RECOMPUTE` 的最终属性值需 Batch 4 按目标上下文计算；多 FR 指向同一 target 且替换文本不同的场景需更细的 target 级结构定位。编译结果可被后续 Preflight/Verify/Final Gate 通过 `format_compilation` 读取。
