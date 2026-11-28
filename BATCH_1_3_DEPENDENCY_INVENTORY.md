# Batch 1–3 逐函数依赖与主流程影响清点（严格只读）

仓库：`Tuzhidingdong/co_reviewer`
代码审查基线：`32e08dd42613c38a101b720cbf420e33fcff4813`
实际审查 HEAD：`ab2e4449db1e1c6dd9cdbf43e78a8a28d4353ce2`（架构审查分支，代码与基线一致）
真实文档验收证据分支：`audit/real-docx-blind-test-20261128`（`ad3c5fe` → `6593cf5`）
架构审查分支：`audit/architecture-feasibility-20261128`（`dba7b77` → `ab2e444`）
报告首次提交 SHA：`11c96bcc22bf9a03d6f144135d11c5f057a4cbf8`
报告链接：`https://github.com/Tuzhidingdong/co_reviewer/blob/audit/batch-dependency-inventory-20261128/BATCH_1_3_DEPENDENCY_INVENTORY.md`

证据等级：**CONFIRMED** = git show/blame/grep/直接读码可复核；**INFERRED** = 基于已引用代码路径的推论；**UNKNOWN** = 静态检查无法确定，不猜测。

本次只做清点，不做删除；未改代码/Schema/测试/Skill/Workflow/CLI，未运行 Apply/Verify/Final Gate，未创建 PR。

---

## 1. 执行摘要

1. **三个 Batch 没有为上次真实 DOCX 成功结果提供任何直接或间接的 Batch 函数调用** [CONFIRMED]。真实运行证据中不存在 `format_compilation`、`compile_fingerprint`、`FR-…`、`source_occurrence_id`、`format_verification`、`format_final_gate_guard`、`analyze-run-formats`、`--run-format-analysis` 等痕迹；唯一相关字段是 `format_requirements_present: false`。
2. **但 Batch 1 已经进入无标志默认主流程** [CONFIRMED]：`instruction_analysis.py:16-21` 在模块加载时导入 B1 函数，`:93-94`、`:118`、`:165` 无条件调用；`instruction_coverage.py:402-412` 的 FR 锚定分支在既有授权校验内、由回执字段门控。因此**不能直接删除 `format_requirements.py`**，否则默认 `validate-instruction-analysis` / `sync` / `compile-plan` 会在 import 阶段失败。
3. **Batch 2/3/4 没有进入无标志默认主流程** [CONFIRMED]：所有生产调用点都在 CLI 可选参数之后；`compile_revision_plan` 不导入 B1/B3；`preflight-plan` 与 `compile-clean-from-marked` 全程 NOT_CALLED；`verify` 对 B2/B3 只有模块级 import、没有调用。
4. **格式能力不是 Batch 产物** [CONFIRMED]：`document_engine.py` 与 `plan_compiler.py` 自基线 `c90f3e0` 起未被任何 Batch 修改；`replacement_format_map`、`inserted_format_map`、`format_operations`、`applies_to_target_index`、`character_format_integrity`、`format_operation_contract_integrity`、零静默回退均属基线 V2 通用能力（`git log -S` 与 blame 确认）。
5. **Batch 4 借用了 Batch 1/2/3 的函数** [CONFIRMED]：`format_execution.py:19-24` 导入 B1/B2/B3，`:239-240,312-314,432-433,429,431` 调用；但它本身也全部被 flag 门控，且 `format_final_gate_guard` 在 `scripts/` 内没有调用者。
6. **可拆除性结论** [INFERRED]：B2/B3/B4 可作为一个自洽簇移除而不影响当前真实成功流程；B1 必须与通用调用方**同步迁移或删除**，不能单文件删除。Schema 字段与 CLI 参数的历史兼容性 [UNKNOWN]，故标 `DO_NOT_TOUCH_YET`。

---

## 2. 三个 Batch 实际包含的代码

### 2.1 Batch 1 — Format Requirements（提交 `1171590`，8 文件 +880；`95c1806` 纯文档回填）[CONFIRMED]

| 项目 | 证据 |
|---|---|
| 独有文件 | `scripts/co_reviewer/format_requirements.py`（236 行）；`schemas/format-requirements.schema.json`（70 行）；`tests/test_format_requirements_batch1.py`（331 行，15 测试）；`docs/reports/format_requirements_batch1_report.md`（+155） |
| 新增函数 | `canonical_json` :39-41；`semantic_fingerprint` :44-47；`version_sha256` :50-53；`_fr_rows` :56-58；`_versions` :61-63；`validate_format_requirements` :66-172（分叉检测 :96-112 由 `179c2c5` 后补）；`validate_format_requirements_schema` :175-192；`current_versions` :195-205；`crosscheck_coverage_fr` :208-244；`fr_ids_for_requirement` :247-253 |
| 修改的旧函数 | `instruction_analysis.py` 导入块 :16-21，`validate_instruction_analysis` :93-94，`crosscheck_coverage` :118，`analysis_to_coverage` :150-171 中 :165-168；`instruction_coverage.py` `authorize_instruction_based_plan` 参数 :267-269 与锚定 :336-350、回执 :356，`validate_instruction_based_authorization` 参数 :387 与锚定 :402-412 |
| 新增数据字段 | 回执 `format_requirements_present` / `format_requirements_file` / `format_requirements_file_sha256`（生产者 `instruction_coverage.py:337-356`，消费者 `instruction_coverage.py:402-412`，CLI `co_reviewer_cli.py:11782-11784,11860`）；Coverage `format_requirement_ids` / `format_coverage`（`schemas/instruction-coverage.schema.json:35-55`，生产者 `analysis_to_coverage:165-168`，消费者 `crosscheck_coverage_fr:208-244`） |
| CLI | `--instruction-analysis` 加入 `authorize-instruction-plan`(:12216)、`apply`(:12321)、`verify`(:12333)、`compile-plan`(:12179-12182)；调用点 `apply_plan:3026,3040`、`cmd_apply:8669`、`cmd_verify:9262`、`cmd_authorize:11775-11784,11858-11868` |
| Schema 调用 | `format-requirements.schema.json` 仅由 `format_requirements.py:184,188-192` 在 `validate_format_requirements_schema` 内加载；条件：FR 非空（:177-179）且 `jsonschema` 可导入（:180-183） |
| Tests | `test_format_requirements_batch1.py:31-327`：15 项，全部手工构造 Python 对象 + 合成两行 TXT；无 CLI、无自然语言生成、无真实 DOCX。代表：`test_external_anchor_roundtrip_and_tamper:287`、`test_fr_coverage_reference_not_semantic_copy:321` |
| 文档 | `SKILL.md:574-607` 只要求 Agent 产出 `instruction_analysis`，未提 FR；`SKILL.md:592-594` 的 `script_format` 是另一条 Coverage 路径；`docs/WORKFLOW.md`/`README.md` 无 FR 引用 [CONFIRMED]；FR 要求只在 `docs/reports/format_requirements_batch1_report.md:73-96,136-158` |

后续提交：`179c2c5` 仅改 `format_requirements.py` +17（分叉检测 :96-112）与 `tests/test_batch5_matrix_gaps.py` +59，未改调用方 [CONFIRMED]。

### 2.2 Batch 2 — Run Analysis（提交 `0a6ae17`，5 文件 +931；`6474ed2` 纯文档回填）[CONFIRMED]

| 项目 | 证据 |
|---|---|
| 独有文件 | `scripts/co_reviewer/run_format_analysis.py`（438 行）；`schemas/run-format-analysis.schema.json`（84 行）；`tests/test_run_format_analysis_batch2.py`（224 行，12 测试）；`docs/reports/format_run_safety_batch2_report.md`（+156） |
| 新增函数 | `canonical_json` :48；`_source_of` :52；`_bool_value` :70；`_normalize_property` :79；`_direct_rpr_map` :98；`_fr_expected` :109；`_property_decisions` :118；`analyze_run` :186；`analyze_paragraph_runs` :332；`analyze_docx` :364；`validate_run_format_analysis` :407 |
| 导入的旧函数 | `document_engine.py`：`NS` :31、`_applicable_format_flags` :516、`_direct_format_flags` :450、`parse_xml` :38、`run_plain_text` :360、`visible_text` :45；`v232_contract.py`：`effective_run_properties` :493（调用点 `run_format_analysis.py:203-213`）、`qn` :31 |
| 新增数据字段 | `direct_format` / `effective_format` / `provenance` / `resolution` / `safety_classification` / `safety_reasons` / `property_decisions` / `analysis_fingerprint`（`run_format_analysis.py:186-329`），`structure_context` / `source_docx_sha256` 由 `89b95bb` 后补 :306,:348,:386 |
| CLI | 子命令 `analyze-run-formats`：`co_reviewer_cli.py:12557-12565`，处理器 `cmd_analyze_run_formats:11607-11624`，调用 `:11611,11615`；参数仅 `--docx/--styles/--theme/--out`，**没有** `--fr`/`--fr-current-ids`，处理器用 `getattr(args,"fr_current_ids",None)` :11613 → FR 绑定路径 CLI 不可达 |
| Schema 调用 | `run-format-analysis.schema.json` 仅由 `validate_run_format_analysis:434-446` 加载（schema 文件缺失时追加错误 :439-440；`jsonschema` 缺失时仅结构校验 :434-437） |
| Tests | `test_run_format_analysis_batch2.py:26-224`，12 项，全部合成 DOCX（`_build_docx:36-85`）；`test_property_decisions:162` 通过直接 Python 调用传 `fr`/`fr_current_ids` :170-181；`test_cli_command_and_schema_validation:203` 使用无 `fr_current_ids` 的 `SimpleNamespace` |
| 文档 | SKILL/Workflow/WORKFLOW.md 无 Batch 2 入口要求；只在 `docs/reports/format_run_safety_batch2_report.md` 与后续 batch report 中出现 |

### 2.3 Batch 3 — Format Compiler（提交 `89b95bb`，7 文件 +1188；`c55d102` 纯文档回填）[CONFIRMED]

| 项目 | 证据 |
|---|---|
| 独有文件 | `scripts/co_reviewer/format_compiler.py`（506 行）；`schemas/format-compile-trace.schema.json`（39 行）；`tests/test_format_compiler_batch3.py`（405 行，19 测试）；`docs/reports/deterministic_format_compiler_batch3_report.md`（+188） |
| 新增函数 | `has_format_requirements` :26；`trace_sha256` :39；`validate_compile_trace` :44；`_blocked` :67；`_run_spans` :83；`_covering_spans` :96；`_safe_candidate` :100；`_strip_properties` :109；`_blocked_properties` :116；`_choose_source_offset` :123；`_build_map` :141；`_operations` :186；`compile_format_requirements` :211；`apply_compiled_formats` :442 |
| 导入 | `format_requirements.py`：`semantic_fingerprint` :44、`validate_format_requirements` :66（导入 `format_compiler.py:19`，调用 :231,:244）；`run_format_analysis.py`：`ANALYSIS_VERSION`/`SAFE_BASE_CLASSES`/`canonical_json`/`validate_run_format_analysis`（导入 :20，调用 :225）；`document_engine.py` 的 `NS`/`parse_xml`/`run_plain_text`/`visible_text` :18。**不导入** `format_execution`（依赖方向相反） |
| 新增数据字段 | `plan["format_compilation"]`（`format_compiler.py:509`）；target `format_requirement_ids`、`format_requirement_id`/`version_id`、`source_occurrence_id`、`replacement_format_map`、`format_operations`（`:455-504`）；Schema `revision-plan.schema.json` +14（`format_compilation`、operation id/FR id/version id、target FR ids）；`format-compile-trace.schema.json` 由 `validate_compile_trace:56-59` 加载 |
| CLI | `--run-format-analysis`：`co_reviewer_cli.py:12183-12187`；唯一生产调用块 `cmd_compile_plan:11693-11722`，`compile_format_requirements` :11703、`validate_compile_trace` :11706、`apply_compiled_formats` :11720；`has_format_requirements` :11698，无 FR 时 :11699 直接跳过 |
| 对 `plan_compiler.py` 的影响 | **无**：`git log -- scripts/co_reviewer/plan_compiler.py` 只有 `c90f3e0`；行内标记解析 `parse_inline_format_markup` :47,521-546,738-754 是基线通用代码 |
| Tests | `test_format_compiler_batch3.py:31-405`，19 项，全部合成 DOCX；`test_cli_compile_plan_integration:257`、`test_non_format_plan_is_left_unchanged:361`、`test_table_context_mismatch_blocked:247` |
| 文档 | SKILL/Workflow 无 Batch 3 要求；仅 batch report 与架构审查报告提及 |

### 2.4 Batch 4（提交 `6820375`，6 文件 +1821；`ceef221` 纯文档）[CONFIRMED，用于依赖判断]

`scripts/co_reviewer/format_execution.py` 新建 785 行；函数：`recompute_properties:124`、`target_occurrence_id:247`、`assign_target_occurrences:274`、`validate_target_occurrences:318`、`_current_fr_versions:363`、`fr_completion_status:379`、`format_apply_preflight:402`、`build_execution_record:542`、`verify_format_execution:624`、`format_final_gate_guard:745`。
通用改动：`final_gate.py` `evaluate_final_gate(..., format_gate=None)` :383 与可选阻断 :764-774；`format_compiler.py:475-478` 为 Batch 3 生成的 format-only target 补 `applies_to_target_index`（沿用通用 `plan_compiler.py:753` 约定）；CLI 新增 `--format-*` 参数 :12322-12325,:12333-12336,:12248-12251 与调用 :8622-8659,:8704-8712,:9298-9342,:11973-12005。

---

## 3. 主流程逐入口调用矩阵

矩阵证据均来自 `scripts/co_reviewer_cli.py` 的 argparse 与 `cmd_*` 函数、`co_reviewer/instruction_analysis.py`、`instruction_coverage.py`、`plan_compiler.py`、`document_engine.py` 的直接调用点。`CONDITIONAL` 优先于 `DIRECT/INDIRECT`。

| 主流程入口 | Batch 1 | Batch 2 | Batch 3 | Batch 4 | 公共功能是否受影响 |
|---|---|---|---|---|---|
| `validate-instruction-analysis`（`CLI:12189-12192` → `cmd:11627`） | **INDIRECT，无条件**（`CLI:11630` → `instruction_analysis.py:93-94` FR 校验；无 FR 时 `format_requirements.py:68-70,177-179` 直接 `return []`） | NOT_CALLED | NOT_CALLED | NOT_CALLED | 是，但仅执行空操作校验 |
| `sync-analysis-to-coverage`（`CLI:12193-12197` → `cmd:11640`） | **INDIRECT，无条件**（`CLI:11648` → `analysis_to_coverage:165`；`CLI:11649` → `crosscheck_coverage:118`；`CLI:11650` → `validate:93-94`） | NOT_CALLED | NOT_CALLED | NOT_CALLED | 是；Coverage 可能被写入 `format_requirement_ids`（无 FR 时不写） |
| `compile-plan`（`CLI:12169-12188` → `cmd:11658`） | **INDIRECT**（`CLI:11601,11684`）+ **CONDITIONAL**（`--run-format-analysis` 内 `format_compiler.py:19,231`） | **CONDITIONAL**（`CLI:11693` flag → `cmd_analyze_run_formats` 逻辑不在此；`format_compiler.py:225`） | **CONDITIONAL**（`CLI:11693` + `has_format_requirements:11698` → `:11703,11706,11720`） | **CONDITIONAL**（同 flag → `:11713,11719`） | `compile_revision_plan`（`plan_compiler.py:433-768`）只 import `document_engine`/`v232_contract`/`insert_authorization`（:15-29），**不导入 B1/B3**；默认路径不受 B2/B3 影响 |
| `preflight-plan`（`CLI:12303-12308` → `cmd:5840`） | NOT_CALLED | NOT_CALLED | NOT_CALLED | NOT_CALLED | 仅通用 `preflight_plan:5869`（`CLI:2565-2604`） |
| `authorize-instruction-plan`（`CLI:12207-12217` → `cmd:11761`） | **CONDITIONAL/INDIRECT**（`CLI:11779` 仅在提供 `--instruction-analysis` 时；`format_requirements_present:11782-11784` → `instruction_coverage.py:337-356,402-412`） | NOT_CALLED | NOT_CALLED | NOT_CALLED | 通用 scope 校验 `instruction_coverage.py:126-157,243-256` 未被 B1 改动 |
| `apply`（`CLI:12315-12326` → `cmd:8536`） | **CONDITIONAL/INDIRECT**（`CLI:3031-3041` 对 `instruction_based` 计划调用 `validate_instruction_based_authorization`，数据门控 FR 锚 :402-412；`CLI:8669` 传 analysis_file） | **CONDITIONAL**（经 B4 preflight `format_execution.py:429`） | **CONDITIONAL**（经 B4 preflight `:432-433`） | **CONDITIONAL**（`CLI:8622 --format-run-analysis` → `format_apply_preflight:8643`；`:8704` execution record） | 无 `--format-run-analysis` 时**没有任何 B4 函数执行**；通用 `apply_plan` 不受 B2/B3 影响 |
| `compile-clean-from-marked`（`CLI:12160-12168` → `cmd:11525`） | NOT_CALLED | NOT_CALLED | NOT_CALLED | NOT_CALLED | 仅 `v232_contract.compile_clean_from_marked`，不在任何 batch 提交 |
| `verify`（`CLI:12327-12337` → `cmd:9210`） | **CONDITIONAL/INDIRECT**（`CLI:9256-9263` 对 `instruction_based` 计划 → `instruction_coverage.py:402-412`） | NOT_CALLED（只有 `format_execution.py:20-23` 模块级 import） | NOT_CALLED（同上） | **CONDITIONAL**（`CLI:9298 --format-execution-record` → `verify_format_execution:9324`） | `verify_apply_report:4692` 与 `verify_character_format_integrity:3858` 均为基线通用 |
| `final-gate`（`CLI:12218-12252` → `cmd:11907`） | **CONDITIONAL/INDIRECT**（`CLI:11983` → `final_gate.py:391-398` → `instruction_coverage.py:874-882` → B1 锚 :402-412；且调用点**不传** `analysis_file`，FR 回执会报“Bounded Format Requirements file was not supplied”） | NOT_CALLED | NOT_CALLED | **CONDITIONAL，report-only**（`CLI:11974-11977` 读取 `--format-gate-report` → `final_gate.py:764-774`；`format_final_gate_guard:745` 在 scripts 内无生产者） | `final_gate.py` 的 CFI(:418-502)、format contract(:422-503)、零静默回退(:715-738) 均为基线 `c90f3e0` |

**关键否定证据** [CONFIRMED]：
- `document_engine.py` 的 `validate_replacement_format_map:606`、`build_auto_replacement_format_map:774`、`make_insert_runs:1456`、`build_paragraph_insertion_runs:1483`、`prepare_document:2072`、`validate_replacement_format_map_in_paragraph:1928`、`authorized_ranges_from_format_operations:731`、`apply_format_operation:1217` 全部 blame 到 `c90f3e0`，没有任何 Batch 修改。
- `git log -S "character_format_integrity" -- scripts/co_reviewer_cli.py` 与 `git log -S "replacement_format_map" -- plan_compiler.py document_engine.py` 都只返回 `c90f3e0`。
- 因此：`FormatChange`、`format_operations`、`replacement_format_map`、`inserted_format_map` 的存在**不能证明** Batch 3 被使用。

---

## 4. Batch 4 对前三个 Batch 的吸收与依赖

### 4.1 Batch 4 直接依赖 Batch 1–3 的函数 [CONFIRMED]

| Batch 4 代码 | 依赖 | 调用点 |
|---|---|---|
| `format_execution.py:19` import `validate_format_requirements`（B1） | FR 完整性 | `:431` |
| `format_execution.py:20-22` import `ANALYSIS_VERSION`/`SAFE_BASE_CLASSES`/`canonical_json`/`validate_run_format_analysis`（B2） | Run 分析校验与安全 base 过滤 | `:240,314`（canonical_json）、`:341,350`（版本）、`:427`（版本）、`:429`（校验）、`:453`（SAFE_BASE_CLASSES） |
| `format_execution.py:239,312,432` import `trace_sha256`/`validate_compile_trace`（B3） | trace 指纹与校验 | `:240,:314,:433` |
| `format_execution.py:501,504-507` 调用 `validate_instruction_based_authorization` | **通用函数**，其 FR 锚分支由 B1 加入 `instruction_coverage.py:402-412` | `:507` 传 `analysis_file` |
| `format_compiler.py:475-478`（B4 修改 B3 文件） | 沿用通用 `applies_to_target_index` 约定（`plan_compiler.py:753`，`co_reviewer_cli.py:1397-1419,2109-2128`） | 仅在 `--run-format-analysis` 下可达 |

### 4.2 分类 A–E

| 分类 | 代码对象 | 证据 |
|---|---|---|
| **A. Batch 专属功能** | `run_format_analysis.py` 全部函数、`format_compiler.py` 全部函数、`format_execution.py` 全部函数、三个 batch schema、五个 batch 测试文件 | 均为 batch 提交新建；生产调用者仅在 flag 门控内 |
| **B. 已被公共主流程吸收的功能** | **仅 Batch 1**：`validate_format_requirements`、`validate_format_requirements_schema`、`crosscheck_coverage_fr`、`fr_ids_for_requirement`（`instruction_analysis.py:93-94,118,165`）；`instruction_coverage.py:402-412` FR 锚分支 | 无标志默认路径可触发；空 FR 时为空操作 |
| **C. 被其他功能复用的通用函数** | B2 `canonical_json`（B3 :20,:41、B4 :20-23,:260）；B2 `validate_run_format_analysis`（B3 :225、B4 :429）；B3 `trace_sha256`/`validate_compile_trace`（B4 :239,:312,:432-433）；B1 `semantic_fingerprint`（B3 :19,:244）、`validate_format_requirements`（B3 :231、B4 :431）；通用 `applies_to_target_index` 机制（B4 在 B3 上扩展） | 复用发生在 Batch 簇内部或 B1→后续 Batch；未进入无标志通用路径 |
| **D. 仅 Batch 参数启用时执行** | `cmd_analyze_run_formats`(:11607)、`--run-format-analysis` 块(:11693-11722)、`--format-run-analysis` preflight(:8622)、`--format-execution-record` verify(:9298)、`--format-gate-report`(:11974)、`authorize --instruction-analysis`(:11775)、B1 回执 FR 锚（数据门控） | argparse 与 guard 行号见 §3 |
| **E. 当前没有发现有效调用者** | `format_execution.format_final_gate_guard:745`（scripts 内仅定义；测试 `test_format_execution_batch4.py:370,464,472,485,493` 引用；CLI 只读手供报告 :11974-12004）；`format_requirements.current_versions:195`（仅内部 :215；compiler :229 注释明确不用）、`version_sha256:50`（仅内部 :138,152 + 测试） | `git grep` 全仓确认 |

**Batch 4 吸收结论** [CONFIRMED]：**没有** B4 函数被无标志公共主流程吸收；B4 只是把 B1/B2/B3 的函数用 flag 门控的方式组合起来。`final_gate.py:764-774` 是 B4 对通用门禁的唯一改动，且需要外部提供报告。

---

## 5. 此前修复过程中三个 Batch 的实际贡献

拓扑 [CONFIRMED]：`a7e32f1` → `1171590` → `0a6ae17` → `89b95bb` → `6820375` → `179c2c5` → `32e08dd`。

| 提交 | 修复由什么实现 | 是否改通用门禁 |
|---|---|---|
| `1171590` B1 | FR 模型与版本链、`semantic_fingerprint:44`、`version_sha256:50`、校验 :66-192、Coverage 引用 :208-244 | 仅把通用 `instruction_analysis.py`/`instruction_coverage.py` 接上调用；未改 Gate 逻辑 |
| `0a6ae17` B2 | Run 分析模块与 CLI 子命令 | 未改任何质量门禁模块 |
| `89b95bb` B3 | Compiler 模块、trace schema、`--run-format-analysis` | **未改 `plan_compiler.py`**；通用 Plan 编译逻辑保持原样 |
| `179c2c5` B5 代码修复 | 纯 B1：FR 并发分叉检测 `format_requirements.py:96-112` + `test_batch5_matrix_gaps.py` | 未改调用方，未改通用门禁 |
| `6820375` B4 | 新建 `format_execution.py`；借用 B1/B2/B3 函数（§4.1） | 通用改动仅 `final_gate.py:383,764-774` 的可选 `format_gate`，不改既有 CFI/contract/零回退 |
| `32e08dd` B5 | **纯文档/证据**，无代码修复 | 否 |

**上次真实运行中的 8 次 fail-closed 错误全部来自通用代码** [CONFIRMED]：`document_engine.py:1241 VERTICAL_ALIGNMENT_TARGET_TOO_BROAD`、`:1722 NAIVE_SINGLE_SEGMENT_FORMAT_FLATTEN_BLOCKED`、`:1760 MIXED_RUN_FORMAT_REPLACEMENT_REQUIRES_EXPLICIT_SEGMENT_PLAN`、`:2187 tabs/newlines`、`:1529 PARAGRAPH_INSERTION_AMBIGUOUS_CHARACTER_FORMAT`、`plan_compiler.py:287 Unsupported doc_role`；这些文件在六个 Batch 提交中均未被修改。

**连接 Batch 与通用质量门禁的测试** [CONFIRMED]：
- B1 `test_external_anchor_roundtrip_and_tamper:287`、`test_fr_coverage_reference_not_semantic_copy:321`；
- B3 `test_cli_compile_plan_integration:257`、`test_non_format_plan_is_left_unchanged:361`；
- B4 `test_final_gate_cli_honours_format_guard:500-515`、`test_end_to_end_real_apply_verify_and_gate:354-399`（**合成 DOCX**）、`test_instruction_based_receipt_and_fr_anchor_are_checked:629`；
- B5 `test_t11_15_concurrent_version_fork_is_blocked`，同文件 `test_t10_07_complex_script_flags_are_not_applied_without_context` 反而只测通用 `_applicable_format_flags`。

对照：把守本次真实运行的通用门禁测试 `tests/test_character_format_integrity.py`、`tests/test_format_operation_integrity.py`、`tests/test_final_gate.py` 中**没有任何 Batch 引用** [CONFIRMED]。

**结论** [CONFIRMED]：此前修复中三个 Batch 的贡献集中在“新增格式治理模块”；**没有**通过 Batch 代码修复通用质量门禁；通用门禁的修复即使没有 Batch 也成立。

---

## 6. 上次真实 DOCX 成功结果的逐项对应

证据：审计分支 `ad3c5fe`/`6593cf5` 下的 `MACHINE_EVIDENCE_INDEX.json`、`FINAL_REPORT.md`、`RUN_LOG.md`、`REAL_DOCX_BLIND_TEST_AUDIT_ADDENDUM.md`、脱敏 `reports/*`。

**决定性否定证据** [CONFIRMED]：上述目录中不存在 `format_compilation`、`compile_fingerprint`、`format_pass`、`Pass-2`、`effective_format`、`direct_format`、`safety_classification`、`analyze-run-formats`、`--run-format-analysis`、`FR-`、`source_occurrence_id`、`format_apply_preflight`、`format_verification`、`format_final_gate_guard`、`format_execution_record`；唯一相关字段是 `format_requirements_present: false`；`RUN_LOG.md:13` 的 compile-plan 命令无 `--run-format-analysis`。

| 实际能力/产物 | Batch 1 | Batch 2 | Batch 3 | 实际替代来源 |
|---|---|---|---|---|
| 作者格式要求识别（17 条 format 行） | **无调用**：Analysis 无 `format_requirements`，B1 校验为空操作 | 否 | 否 | Agent 判定 + `instruction_analysis.py` 通用校验 |
| 48 项要求 / Coverage | **无调用**：仅 `format_requirements_present:false`（B1 改过的通用代码字段） | 否 | 否 | 通用 `analysis_to_coverage`（B1 仅加 FR 引用，无 FR 时不写） |
| 45 个目标 | 否 | 否 | 否 | 通用 `plan_compiler` + `preflight-plan` + `authorize` |
| 格式来源判断 | 否 | 否 | 否 | Agent 阅读 + 本地脚本 + 源 Run 签名；**不是** Run Analysis |
| `replacement_format_map` | 否 | 否 | 否（无 Compiler） | 基线 `document_engine` 机制 + 本地 `add_format_maps.py`（未上传，行为来自叙述）[INFERRED] |
| `inserted_format_map` | 否 | 否 | 否 | 同上（含硬编码源偏移）[INFERRED] |
| 15 个 FormatChange | 否 | 否 | 否 | 基线 `plan_compiler.parse_inline_format_markup:47,521-546,738-754`；`applies_to_target_index` 来自通用 `:753`，**不是** B4 的 `format_compiler.py:478` |
| 字符级格式验证 | 否 | 否 | 否 | 通用 `verify_character_format_integrity:3858` / `final_gate.py:418-502`（基线） |
| Apply / Verify | 否（B1 FR 锚因 flag false 空转） | 否 | 否 | 通用 `apply_plan` / `verify_apply_report` |
| Final Gate | 否（未传 `format_gate`） | 否 | 否 | 通用 `evaluate_final_gate` |

**明确回答** [CONFIRMED]：**三个 Batch 没有为这次三份文档成功结果提供任何直接或间接的 Batch 函数调用。** 四种情况必须区分：
1. 实际调用 Batch 函数：**没有**。
2. 沿用 Batch 开发时修改过的通用代码：**有，但只有 B1 改过的 `instruction_analysis.py`/`instruction_coverage.py`，且 FR 分支在此次为空操作**。
3. 只是使用了相同的数据结构或概念：**有**（`format_operations`、`format_requirements_present:false`、Plan 字段）。
4. 完全没有使用 Batch 功能：**对 B2/B3/B4 成立**。

---

## 7. 公共质量门禁与 Batch 专项门禁的依赖

- **公共门禁（基线，必须保留）**：`document_engine.py:606,774,1456,1483,1928,2004,2072`；`plan_compiler.py:47,521-546,711-754`；`co_reviewer_cli.py:3858 verify_character_format_integrity`、`:4692 verify_apply_report`、`:9285` 目标覆盖检查；`final_gate.py:388-404,416-502,592-738`；`instruction_coverage.py:46-157,511-680`；`insert_authorization.py:205-319`。
- **Batch 专项门禁**：B1 FR 哈希锚 `instruction_coverage.py:337-356,402-412`（缺省放行）；B2 `UNRESOLVED/UNSAFE/BLOCKED_IF_UNKNOWN` 分类 `run_format_analysis.py:214-228,267-293`；B3 trace/BLOCKED `format_compiler.py:417-439`；B4 preflight/verify/guard `format_execution.py:402-523,624-739,745-785`。
- **依赖关系** [CONFIRMED]：公共门禁不依赖 Batch 专项门禁即可运行（本次真实运行已证明）；Batch 专项门禁依赖通用底层（`document_engine`/`v232_contract`/`instruction_coverage`），而非相反。

---

## 8. 删除影响清单

| 代码对象 | 只服务 Batch | 公共主流程使用 | 其他模块使用 | 拆除影响 | 初步处置 |
|---|---|---|---|---|---|
| `format_requirements.py` | 否（B1 函数被通用模块 import） | **是**：`instruction_analysis.py:16-21,93-94,118,165` 无条件 | B3 `format_compiler.py:19,231,244`；B4 `format_execution.py:19,431` | **直接删除会使通用 import 失败**；必须先改 `instruction_analysis.py` 与 `instruction_coverage.py` | 迁移候选（不可单删） |
| `run_format_analysis.py` | 是 | 否（仅 flag 链） | B3 :20,225；B4 :20-23,429；CLI :11609,11611,11615 | 单独删除会破坏 B3/B4 import | 与 B3/B4 成组删除候选 |
| `format_compiler.py` | 是 | 否（`compile_revision_plan` 不导入） | B4 :239,312,432；CLI :11694-11720 | 同上 | 与 B2/B4 成组删除候选 |
| `format_execution.py` | 是 | 否（全 flag 门控） | CLI flag 调用 | 无通用破坏；但 `--format-*` 参数与测试需同步 | 成组删除候选 |
| `schemas/format-requirements.schema.json` | 是 | 否 | 仅 B1 :184 | 单独删无通用影响；历史 FR 产物读取 [UNKNOWN] | 暂缓 |
| `schemas/run-format-analysis.schema.json` | 是 | 否 | 仅 B2 :438-442 | 同上 | 暂缓 |
| `schemas/format-compile-trace.schema.json` | 是 | 否 | 仅 B3 :56-59 | 同上 | 暂缓 |
| `schemas/instruction-coverage.schema.json`（B1 +21 字段） | 否（共享 schema） | 是（通用校验 `_validate_manifest`） | Coverage 产物 | 删除字段可能影响历史 FR 产物校验 [UNKNOWN] | `DO_NOT_TOUCH_YET` |
| `schemas/revision-plan.schema.json`（B3 +14 字段） | 否（共享 schema） | 是（通用 Plan 校验） | 历史 Plan | 同上 | `DO_NOT_TOUCH_YET` |
| CLI 参数 `--run-format-analysis` / `--format-*` / `--format-gate-report` | 是 | 否 | 外部脚本/BAT [UNKNOWN] | 删除可能影响仓库外脚本；仓库内无证据 | `DO_NOT_TOUCH_YET` |
| `document_engine.py` / `plan_compiler.py` 格式机制 | 否 | 是（基线） | 全部普通 FormatChange | **不可删除** | 保留 |
| `final_gate.py:383,764-774` | 否（通用文件的可选分支） | 条件调用 | CLI :11974-12004 | 删除不影响通用门禁；但需同步 CLI 参数 | 迁移/随 B4 处置 |
| 五个 batch 测试文件 | 是 | 否 | 对应 batch 模块 | 模块删除则测试必须同步删除 | 随模块处置 |
| `format_final_gate_guard:745` | 是 | 否 | 仅测试 | 删除无通用影响；CLI 手供报告将永远为空 | 随 B4 处置 |

**风险点回答** [CONFIRMED / UNKNOWN 分开]：
- 其他模块 import Batch 代码：仅 B1 被通用模块无条件 import（CONFIRMED）；B2/B3 仅被 B3/B4 与 flag 链 import（CONFIRMED）。
- Schema 被公共流程复用：`instruction-coverage.schema.json` 与 `revision-plan.schema.json` 是共享 Schema（CONFIRMED）；三个 batch 专属 schema 仅被对应 validate 函数加载（CONFIRMED）。
- Plan 字段依赖 Batch 生成逻辑：`format_compilation` 由 B3 生成，但本次 Plan 无该字段仍通过所有门禁（CONFIRMED）。
- 授权回执含 Batch 专属数据：`format_requirements_present:false` 由 B1 改过的通用代码写入（CONFIRMED）。
- 历史产物读取依赖：仓库内无验证；[UNKNOWN]。
- Skill/Workflow 是否仍要求已退出模块：主文档无 B2/B3 要求（CONFIRMED）；B1 也未以 FR 名称要求（CONFIRMED）。
- 删除 CLI 参数影响旧脚本：仓库内无 BAT/脚本证据，[UNKNOWN]。
- 删除格式辅助函数影响普通 FormatChange：`document_engine`/`plan_compiler` 中的函数**不是 Batch 代码**，不受影响（CONFIRMED）。

---

## 9. 三份处置清单

### 清单 A：可确认独立、可成组删除的 Batch 专属代码（前提：成组、同步删 CLI 门控与测试）

- `scripts/co_reviewer/run_format_analysis.py`（B2）
- `scripts/co_reviewer/format_compiler.py`（B3）
- `scripts/co_reviewer/format_execution.py`（B4）
- `schemas/run-format-analysis.schema.json`、`schemas/format-compile-trace.schema.json`、`schemas/format-requirements.schema.json`
- 五个测试文件：`test_format_requirements_batch1.py`、`test_run_format_analysis_batch2.py`、`test_format_compiler_batch3.py`、`test_format_execution_batch4.py`、`test_batch5_matrix_gaps.py`
- CLI 中相应 flag 门控块与参数（`co_reviewer_cli.py:11607-11624,11693-11722,8621-8659,8704-8712,9298-9342,11973-11977` 及 argparse `:12183-12187,12248-12251,12322-12325,12333-12336`）

证据：这些文件的生产调用者全部位于上述 flag 门控内或彼此之间；`preflight-plan`、`compile-clean-from-marked`、`verify` 核心、`final_gate` 核心均不调用（§3）。**注意**：`format_requirements.py` 不在此清单，因为它被通用模块无条件 import。

### 清单 B：必须保留或迁移的公共能力

- `instruction_analysis.py:16-21,47-95,98-147,150-171`（含 B1 加入的调用；若退出 FR，必须同步删除这些行而非删文件）。
- `instruction_coverage.py:259-377,380-508,511-680,719-898`（含 B1 FR 锚分支；通用授权必须保留）。
- `document_engine.py` 与 `plan_compiler.py` 的全部格式映射/FormatChange 机制（基线，非 Batch）。
- `co_reviewer_cli.py:3858,4692,9285` 与 `final_gate.py:388-502,715-738` 的通用 Verify/Gate 机制。
- `insert_authorization.py:205-319`、`v232_contract.py:493` 等被 Batch 与通用共用的底层函数。
- `format_requirements.py` 中若决定退出 FR，必须先迁移/删除通用调用点；在迁移完成前**保留**。

### 清单 C：暂时不能删除、需要进一步实证（`DO_NOT_TOUCH_YET`）

- `schemas/instruction-coverage.schema.json` 与 `schemas/revision-plan.schema.json` 中 Batch 新增字段：历史 Plan/Coverage/回执的读取兼容性未知。
- CLI `--run-format-analysis` / `--format-*`：仓库外脚本/BAT 依赖未知。
- 五个 batch schema：历史产物恢复依赖未知（脚本内是否可重建未知）。
- `format_final_gate_guard` 的外部生产者：仓库内无 CLI 调用者，但可能存在外部调用 [UNKNOWN]。
- 任何“只在合成测试中验证过”的功能（B2/B3/B4 全部）：删除前需确认无真实用户依赖。
- 上次真实运行的失败期证据（8 次失败、第 8 次 Verify、Clean 重建）：与 Batch 无关，但不得因此改变验收记录。

---

## 10. 最终回答

**拆除三个 Batch 是否可能做到不影响现有成功流程？**

- **可以做到，但不能按“删三个文件”的方式做。** 上次真实 DOCX 成功流程只用了通用代码，因此从功能上移除 B2/B3/B4 不会改变那次结果 [CONFIRMED + INFERRED]。
- **Batch 1 是例外**：它的函数已被通用 `instruction_analysis.py` 无条件 import/调用，单独删除 `format_requirements.py` 会导致默认主流程 ImportError [CONFIRMED]。必须先修改通用调用点，或保留一个最小兼容层。
- **Batch 2/3/4 是自洽簇**：把它们与对应 CLI 参数、schema、测试一起移除，静态分析未发现通用主流程调用者 [CONFIRMED]；但仓库外调用者与历史产物读取 [UNKNOWN]，因此清单 A 的前置条件是“确认无外部依赖”。
- **已知风险**：共享 Schema 字段的历史兼容性；CLI 参数被旧脚本引用；外部 `format_final_gate_guard` 生产者；`jsonschema` 运行时可用性决定 schema 分支是否执行（UNKNOWN）。
- **未确认点**：运行时是否存在动态调用（`getattr`/反射）无法由静态检查完全排除；本次未运行任何完整工作流验证拆除后的行为。

**本次只清点，不执行拆除。** 任何实际删除都必须先完成清单 C 的实证，并按清单 B 迁移公共能力。
