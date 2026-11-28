# Phase 3 / Batch 2 — Effective Format Analysis & Run Safety Classification 报告

日期：2026-11-28

## 1. 基线与分支

- Batch 1 基线 HEAD：`95c1806a9878a5661736aae9490746096e043317`（工作区 clean）
- Batch 1 实现 commit：`1171590f33ca12340ffcd15ab1187bf2b7d8a112`（已确认为当前 HEAD 祖先）
- Batch 2 本地分支：`feat/format-run-safety-batch2-20261128`
- Batch 2 实现 commit：`BATCH2_COMMIT_PLACEHOLDER`
- 未推送、未创建 PR、未添加/修改 remote、未重建远程基线，未覆盖 Batch 1 提交。

## 2. 修改文件与目的

| 文件 | 状态 | 目的 |
|---|---|---|
| `scripts/co_reviewer/run_format_analysis.py` | 新增 | 复用 `effective_run_properties`，输出 Direct/Effective/Provenance/Resolution/Safety Classification/Property Decisions |
| `schemas/run-format-analysis.schema.json` | 新增 | Draft 2020-12 分析结果 Schema |
| `scripts/co_reviewer_cli.py` | 修改 | 新增只读 `analyze-run-formats` 命令 |
| `tests/test_run_format_analysis_batch2.py` | 新增 | 使用合成 DOCX 覆盖 T10 相关场景 |
| `docs/reports/format_run_safety_batch2_report.md` | 新增 | 本报告 |

未修改 `plan_compiler.py`、`document_engine.py` 的映射/Apply 逻辑，未生成 `replacement_format_map`，未编译 Pass-2，未执行真实 DOCX Apply。

## 3. `effective_run_properties` 的实际复用方式

`run_format_analysis.analyze_run()` 直接调用 `v232_contract.effective_run_properties()`（当前版本第 493 行），传入：
- `document_xml`、`styles_xml`、`theme_xml`；
- `paragraph_style_id`（从 `w:pPr/w:pStyle` 读取）、`paragraph_rpr`；
- `run_style_id`（解析器从 `w:rPr/w:rStyle` 读取）；
- Run 的 `w:rPr` 及表格/编号上下文（由调用方提供时透传）。

没有新建样式继承引擎。解析失败（样式缺失、主题缺失、循环链等）捕获为分析失败，不会产生乐观的 SAFE 结果。

## 4. 实际数据结构

每个 Run 的分析结果包含：

```json
{
  "schema_version": "1.0",
  "analysis_version": "run-safety/1.0",
  "run": {"part": "word/document.xml", "p_idx": 0, "run_index": 0, "run_id": "..."},
  "text": "...",
  "direct_format": {"flags": ["..."], "applicable_flags": ["..."],
                    "properties": {"vertAlign": null, "i": true, "...": "..."}},
  "effective_format": {
    "properties": {"vertAlign": "superscript", "i": null, "sz": 22, "rFonts": {"ascii": "MinorFace"}},
    "sources": {"vertAlign": "CHARACTER_STYLE", "sz": "DOC_DEFAULTS", "rFonts.ascii": "THEME"}
  },
  "provenance": {"vertAlign": "CHARACTER_STYLE", "sz": "DOC_DEFAULTS", "rFonts.ascii": "THEME"},
  "resolution": {
    "vertAlign": {"status": "RESOLVED_PRESENT", "direct": null,
                  "provenance": "CHARACTER_STYLE", "layers": [{"layer": "characterStyle:C1", "value": "superscript"}]},
    "i": {"status": "CONFLICT_RESOLVED", "direct": false, "provenance": "DIRECT_RUN", "layers": [...]}
  },
  "context_ready": true,
  "warnings": [],
  "safety_classification": "UNSAFE_SCRIPT_FORMAT",
  "safety_reasons": ["..."],
  "property_decisions": [
    {"property": "vertAlign", "direct": null, "effective": "superscript",
     "provenance": "CHARACTER_STYLE", "decision": "STRIP", "reason": "..."}
  ],
  "format_requirement_id": null,
  "analysis_fingerprint": "<sha256>"
}
```

Direct 视图来自 `_direct_format_flags`/`_applicable_format_flags` 与 Run 自身 `w:rPr`；Effective 视图来自解析器层叠结果。两者互不覆盖。布尔属性区分直接 true、直接 false（`w:val="0"`）、缺失和继承值；多层冲突记录为 `CONFLICT_RESOLVED`。

## 5. 五种 Run 安全分类规则（优先级由高到低）

1. `UNRESOLVED_EFFECTIVE_FORMAT`：样式/Theme/关键属性无法解析、FR 引用无效或当前指针失效。
2. `UNSAFE_SCRIPT_FORMAT`：Effective 或 Direct 存在 `vertAlign = superscript/subscript`。
3. `UNSAFE_STYLE_INHERITANCE`：非直接来源的属性与目标 FR 冲突，或出现无法判定来源的关键属性；不等同于“只要有继承”。
4. `SAFE_WITH_COMPATIBLE_CONTEXT`：已解析的非脚本格式上下文，且与 FR 兼容。
5. `SAFE_NEUTRAL`：已解析且无相关格式。

只有前两种 SAFE 类别允许成为 Batch 3 基础 Run 候选；模块中导出 `SAFE_BASE_CLASSES` 供后续复用。

## 6. 五种 Property Safety Decisions 规则

- `EXPLICIT_OPERATION_REQUIRED`：FR 明确要求该属性（如 `vertAlign`、`i`、`b`），必须由显式格式操作承载。
- `STRIP`：属性存在且不被 FR 要求（脚本格式或非直接来源的强调属性会污染替换文本）。
- `RECOMPUTE`：字体/字号/颜色等来自继承上下文且不能直接复用，需按目标上下文重算。
- `PRESERVE`：直接显式设置、默认值、Theme 或已确认兼容的上下文可保留。
- `BLOCKED_IF_UNKNOWN`：解析状态为 UNRESOLVED 或来源不可判定时阻断，不猜测。

决策逐属性生成，关联 FR 时只写 `format_requirement_id`，不复制 FR 语义。

## 7. Schema 与运行时校验入口

- Schema：`schemas/run-format-analysis.schema.json`。
- 运行时入口：`co_reviewer.run_format_analysis.validate_run_format_analysis()`（Python 结构校验 + jsonschema 可用时的 Draft 2020-12 校验）。
- CLI：`python scripts/co_reviewer_cli.py analyze-run-formats --docx <file> --out <file> [--styles ...] [--theme ...]`，只读。

## 8. T10 相关测试与结果

`tests/test_run_format_analysis_batch2.py` 使用合成 DOCX（script 生成，非论文内容），12 项测试覆盖：

- Direct/Effective 分离、直接上下标、直接 italic；
- 字符样式继承上标、段落样式继承 italic、直接 false 覆盖继承 true、Direct 缺失但 Effective 已确定；
- Theme 字体来源、docDefaults 来源、缺失样式导致 UNRESOLVED；
- 五种分类、五种属性决策、`SAFE_BASE_CLASSES` 约束；
- CLI 命令与 Schema 校验；原有 `_direct_format_flags`/`_applicable_format_flags` 行为不变。

命令：`PYTHONPATH=scripts python -m unittest discover -s tests -p 'test_run_format_analysis_batch2.py' -v`
结果：12/12 PASS，退出码 0。

## 9. Batch 1 定向回归结果

- `test_format_requirements_batch1.py`：15/15 PASS，退出码 0。
- `test_instruction_analysis_producer.py`：9/9 PASS，退出码 0。
- `test_instruction_based_authorization.py`：11/11 PASS，退出码 0。

## 10. 完整测试结果

命令：`PYTHONPATH=scripts python -m unittest discover -s tests -v`
结果：Ran 802 tests，799 PASS、2 FAIL、1 ERROR，退出码 `1`。较 Batch 1 增加 12 项（本批新增），未出现新的失败项。

Batch 1 记录的三个基线既有失败复核：仍为完全相同的三项、相同失败类型与位置（numbering transaction 错误消息正则不匹配、two-pass overlap 既有 format-map/script 问题、revision workflow 的 `author_approval scope changed for R1-C1`），均已在未修改的 `a7e32f1` 基线树中复现，与 Batch 2 无关。

## 11. 兼容性检查

- 未修改 `_direct_format_flags`、`_applicable_format_flags`、`effective_run_properties` 的实现；只在新模块中调用。
- 未修改 `plan_compiler.py`，未改变 FormatChange、replacement_format_map、Pass-2、Apply、Verify、Final Gate 流程。
- Batch 1 FR 数据模型、版本链、fingerprint、外部锚定均未修改；本批次只读取 FR ID 和属性。
- 未在 Run 分析结果中复制 FR 语义；`format_requirement_id` 只是引用。
- 未对真实 DOCX 执行 Apply 或覆盖。

## 12. 未解决问题 / BLOCKED

- Batch 3 仍需消费本批次输出的 Run 安全分析结果，生成确定性安全基础映射与完整 Pass-2 操作；本批次不实现。
- `UNSAFE_STYLE_INHERITANCE` 的判定基于“非直接来源属性与 FR 冲突或未判来源”；复杂条件表格样式/编号上下文需要 Batch 3 传入更完整的 `table_style_rpr`/`numbering_rpr`，当前接口已支持透传但合成测试未覆盖。
- 完整套件 3 项失败均为基线既有问题，未混入 Batch 2 修复范围，也未修改无关模块。

## 13. `git status` 最终状态

- 实现 commit 提交后工作区：clean。
- 回填本报告 SHA 字段的 docs-only commit 提交后工作区：clean。
- Batch 1 分支与提交保持不变；`master` 仍指向 `a7e32f1b79994173439cb2755ef52a9c99ce872b`。

## 14. 完成后回答

**问题 1：Pipeline 是否能区分 Run 直接格式和 Word 样式继承后真正生效的格式？**
是。`direct_format` 与 `effective_format` 是两个独立视图，分别来自 Run 直接 `w:rPr` 与 `effective_run_properties` 层叠结果；provenance 标注来源。

**问题 2：如果上标/下标隐藏在字符样式、段落样式或其他继承层中，是否仍能识别并阻止其成为不安全基础？**
是。测试中的字符样式继承上标被识别为 `effective vertAlign=superscript`，分类为 `UNSAFE_SCRIPT_FORMAT`，不属于 `SAFE_BASE_CLASSES`。

**问题 3：无法解析的样式、Theme 或属性是否 fail-closed？**
是。样式缺失导致解析异常时归为 `UNRESOLVED_EFFECTIVE_FORMAT`；关键属性 UNRESOLVED 时所有属性决策为 `BLOCKED_IF_UNKNOWN`，不会记为 false 或 SAFE。

**问题 4：Batch 3 是否已具备结构化 Run 安全分析输入？还缺什么？**
已具备：逐 Run 的 Direct/Effective/Provenance/Resolution/Safety Classification/Property Decisions，并有 Schema 和 CLI。仍缺：Batch 3 需把这些决策转成 `replacement_format_map` 与显式 Pass-2 操作，并接入编译器；复杂表格/编号样式的完整 rPr 上下文需由编译器按文档结构传入。
