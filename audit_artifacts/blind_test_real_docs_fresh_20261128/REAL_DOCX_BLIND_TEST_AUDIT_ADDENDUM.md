# 真实文档盲测补充审计（只读）

> 本文件仅补充审计说明，**不重新评价、不改变** `FINAL_REPORT.md` / 既有验收结论。
> 全程只读本地 Round、基线仓库代码与既有审计提交；未重新运行 Apply / Verify / Final Gate，未重新生成 DOCX/PDF，未补填任何 FR / Run Analysis / Format Compilation 证据。
> 公开仓库脱敏：不引用逐字指令或论文正文，作者姓名以 `[AUTHOR_REDACTED]` 代替，路径以 `<ROUND_ROOT>` 代替。哈希、ID、状态、时间、错误代码均保留原值。

审计对象：`<ROUND_ROOT>`（`blind_test_real_docs_fresh_20261128`）
基线：`Tuzhidingdong/co_reviewer` @ `32e08dd42613c38a101b720cbf420e33fcff4813`，分支 `feat/format-real-docx-acceptance-batch5-20261128`
既有审计分支：`audit/real-docx-blind-test-20261128` @ `ad3c5fe854c98bbc9af12fb17f0df3e6a890efe8`

## 一、八次 fail-closed 修正的真实执行过程

### 1.1 总体证据限制

Round 的 `logs/` 目录**没有任何 `*.log` / `*.out` 命令输出文件**；`reports/` 也只保留最终 PASS 报告。八次失败的逐字错误文本、精确时刻与退出码**没有持久化为机器证据**，仅存在于叙述记录：

- 本地 `RUN_LOG.md:36-45`（八条逐项记录）
- 审计副本 `RUN_LOG.md:32-43`、`MACHINE_EVIDENCE_INDEX.json:125`（第 8 条）

可直接复核的时序证据只有文件 mtime 与多版本中间产物：`instruction_coverage.synced.json`(20:44:51)、`synced2`(20:45:15)、`synced3`(20:46:44)、`synced4`(20:50:05)、`synced5`(20:52:37)、`synced6`(21:00:05)；`build_artifacts.py` 末次改写 20:59:54；`add_format_maps.py` 末次改写 20:52:30。

### 1.2 逐次时间线（时间均为本地 +08:00；精确失败时刻无法确认）

| # | 阶段 / 归属 | 错误代码（RUN_LOG 记载） | 触发命令 | 官方 Gate 还是内部检查 | Agent 修正（修改的输入/中间产物） | 修正后复核 | 证据 |
|---|---|---|---|---|---|---|---|
| 1 | Intent/Plan 构建（`compile-plan`） | `Unsupported doc_role` | `compile-plan --intents …` | 官方 CLI 编译校验 | `plan/build_artifacts.py`：`doc_role` 改为合法角色 `Other` | 后续 compile-plan 成功；`instruction_coverage.synced2.json` 20:45:15 | `RUN_LOG.md:38` |
| 2 | Preflight（格式标记） | `VERTICAL_ALIGNMENT_TARGET_TOO_BROAD`（`word/document.xml P[8]`） | `preflight-plan …` | **官方 Gate** | `build_artifacts.py`：字面约等记号改为转义写法 | `synced3` 20:46:44；compile → preflight 继续至下一阻塞 | `RUN_LOG.md:39` |
| 3 | Preflight（混合格式映射） | `MIXED_RUN_FORMAT_REPLACEMENT_REQUIRES_EXPLICIT_SEGMENT_PLAN`（`P[24]`） | `preflight-plan …` | **官方 Gate** | 新增 `plan/add_format_maps.py`，从本地段落 Run 清单生成完整 `replacement_format_map` | `synced4`/`synced5`；compile → preflight 继续 | `RUN_LOG.md:40`；`add_format_maps.py:34-53` |
| 4 | Preflight（映射扁平化） | `NAIVE_SINGLE_SEGMENT_FORMAT_FLATTEN_BLOCKED`（`P[8]`） | `preflight-plan …` | **官方 Gate** | `add_format_maps.py`：源偏移由 0 改为非零 plain 偏移 | `synced5` 20:52:37；compile → preflight 继续 | `RUN_LOG.md:41`；`add_format_maps.py:44-48` |
| 5 | Preflight（文本约束） | `Tabs/newlines are not supported in safe find/replace text` | `preflight-plan …` | **官方 Gate** | `build_artifacts.py`：两条新文献拆为两个 `insert_paragraph`，去掉换行 | `synced6` 21:00:05；compile → preflight 继续 | `RUN_LOG.md:42` |
| 6 | Preflight（插入格式目标） | 插入段落格式目标在源文档中不存在（错误文本为“Target … not found in word/document.xml”） | `preflight-plan …` | **官方 Gate** | `build_artifacts.py`：取消插入目标的旁通道格式目标，改用 `inserted_format_map` 继承源 Run 格式 | compile → preflight 继续 | `RUN_LOG.md:43` |
| 7 | Preflight（插入锚点格式） | `PARAGRAPH_INSERTION_AMBIGUOUS_CHARACTER_FORMAT` | `preflight-plan …` | **官方 Gate** | `add_format_maps.py`：分段 `inserted_format_map`，把期刊名/卷号映射到源中斜体/加粗 Run | 最终 preflight PASS：`reports/preflight_report.json` `generated_at=2026-11-28T13:02:13.093486Z`，45/45 safe | `RUN_LOG.md:44`；`add_format_maps.py:70-90` |
| 8 | Verify（字符级格式完整性） | `character_format_integrity FAIL (1 target)` | `verify …`（首次） | 正式 `verify` CLI 失败；逐字符详情来自直接调用 `verify_apply_report` 的诊断（**推断，见 1.3**） | `build_artifacts.py` 增加显式 `i:false` FormatChange；重建 Analysis/Coverage/Intents/Plan，重新 preflight/authorize/apply/clean/verify | 最终 Verify 45/45、0 failed、CFI PASS；Final Gate 8 项 PASS | `RUN_LOG.md:45`；`revision_plan.instruction_authorized.json` MT09B 两个格式操作（`i:false` / `i:true`） |

### 1.3 第 8 次失败的明确结论

1. **是否来自正式 `verify` 命令？** 叙述记录 `RUN_LOG.md:45` 写为“Verify 首次 … 失败”，指向正式命令。但 Round 内**没有任何失败版 Verify 报告文件**：`reports/verify_report.json` 是唯一的 Verify 报告，`targets_failed=0`、CFI `PASS`。代码路径支持这一点：`co_reviewer_cli.py:9285` 调用 `verify_apply_report`，`:9290-9297` 在 `targets_failed>0` 时先 `raise ValueError("Verify target coverage is incomplete…")`，而落盘发生在 `:9348`——失败时**不会写入报告**。因此“无失败报告”与“正式 verify 失败”并不矛盾。
2. **逐字符违规详情来自哪里？** CLI 在 `:9285` 取得 report 后直接抛错，不打印报告内容；所以“哪个字符、期望/实际格式”的详情只能来自对同一函数 `verify_apply_report`（`:4694`）的**直接 Python 调用诊断**。本 Round **没有保存该诊断脚本或输出**；兄弟轮次存在类似 `diag_verify2.py` 但不属于本 Round。故该细节来源为**基于代码路径的推断，无法由本 Round 持久化证据直接确认**。
3. **正式 Workflow 停止规则是否被触发？** `SKILL.md:544`：`A failed official preflight, approval, Apply, or verification is a hard stop. Preserve the failed plan and evidence, report the exact blocker, and request an engineering change…`
   - 若第 8 次确为正式 `verify` 失败，则构成 hard-stop 事件；现有产物显示 Agent **未**按该条执行（未保留失败 plan/失败报告，未请求工程变更，而是回到更早输入重生成全链）。这是**流程偏离**。
   - 若仅为直接诊断调用、正式 `verify` 从未失败，则 hard-stop 不适用。
   - 两种分支均**无法从持久化机器证据最终判定**；但 `RUN_LOG.md:45` 的措辞更支持前一分支。脱敏 `RUN_LOG.md:43` 所写“所有失败均按 fail-closed 处理，未绕过任何 gate”**过于笼统，不能作为已遵守 hard-stop 规则的证明**。
4. **最终证据链是否同一条更新后链？** 是，且可复算：
   - `plan/revision_plan.instruction_authorized.json` 文件 SHA-256 `148c6d4c14d372b5e29b28066665b02e5edadc7866df9274469a376903c51f14`，等于授权回执 `plan_sha256` 与最终 Verify `authorized_plan_sha256`。
   - `plan/revision_plan.preflighted.json` SHA-256 `b513ab9aa4d2831f50faa228276f8e576d1d9d3a10e7d59aa5197b22d2a7a3e1`，等于 assessment `preflight_plan_sha256`。
   - 授权回执规范摘要 `10fac4f2f888cda967133a73d71c473f8d2d52c25b878eb33064eaddb10cfb2b`，等于最终 Verify `authorization_sha256`；回执整文件 SHA `7fec7362…b172` 不同属自引用规范化设计（`instruction_coverage.py:33-36,376,399`），非矛盾。
   - Coverage 规范摘要 `82d16c8cae10f19693938d67f3c595c73e450da0430f75c99b79d3ce533a04f6`，等于回执与 Final Gate 记录。
   - `apply_report.json` 三份输入/输出哈希分别等于原始 DOCX 与当前 Marked DOCX 实测 SHA；最终 Verify `45/45`、`passed=true`。
   - 时间：preflight 13:02:13Z、授权 13:02:14Z、Apply 13:02:59、Verify 13:03:54、Final Gate 13:04:36Z——全部晚于修正，属同一条更新后链。

### 1.4 重新编译 / 重新 Preflight / 重新授权的文件与时间

| 产物 | SHA-256 | mtime (本地) |
|---|---|---|
| `plan/instruction_analysis.json` | 规范摘要 `2e20e32fa1e9103e343a8c4cfb53ae8a307835ef5fe3effcc5b9b5aaa8abecd0` | 21:00:04 |
| `plan/instruction_coverage.synced6.json` → `instruction_coverage.json` | 规范摘要 `82d16c8cae10f19693938d67f3c595c73e450da0430f75c99b79d3ce533a04f6` | 21:00:05 |
| `plan/intents.mapped.json` | — | 21:00:05 |
| `plan/revision_plan.json` | — | 21:00:06 |
| `plan/revision_plan.preflighted.json` | `b513ab9aa4d2831f50faa228276f8e576d1d9d3a10e7d59aa5197b22d2a7a3e1` | 21:02:13 |
| `plan/instruction_authorization_assessment.json` | — | 21:02:13 |
| `plan/instruction_based_authorization.json` | 规范摘要 `10fac4f2f888cda967133a73d71c473f8d2d52c25b878eb33064eaddb10cfb2b` | 21:02:14 |
| `plan/revision_plan.instruction_authorized.json` | `148c6d4c14d372b5e29b28066665b02e5edadc7866df9274469a376903c51f14` | 21:02:13 |

## 二、Batch 1 FR 版本链为何未进入本轮

### 2.1 事实

- `plan/instruction_analysis.json`：48 条 analyses；`replace` 27、`format` **17**、`insert` 3、`delete` 1；**没有 `format_requirements` 键**；规范摘要 `2e20e32f…ecd0`（`reports/instruction_analysis_validation.json` `passed:true` 即校验此摘要）。
- `plan/instruction_coverage.json`：48 条；**没有 `format_requirement_ids`**，也没有任何 `FR-…-vN` ID；摘要 `82d16c8c…04f6`。
- `plan/instruction_based_authorization.json:6`：`format_requirements_present: false`；**没有** `format_requirements_file` / `format_requirements_file_sha256` / `analysis_file_sha256`。
- 三份 `revision_plan*.json`、`intents*.json`：无 FR ID、无 `format_compilation`。
- 辅助脚本 `plan/build_artifacts.py` 仅从官方模块导入 `analysis_sha256`（`:18`），直接序列化 Agent 判定；`finalize_coverage.py` 仅计算 `coverage_sha256`；`build_assessment.py` 仅组装评估；`add_format_maps.py` 仅导入 `parse_inline_format_markup`（`:15`）。**没有任何 FR API 调用**。

### 2.2 原因与判定

- 官方 FR 校验在数组缺省时是 no-op（`scripts/co_reviewer/format_requirements.py:69-70,178-179,211-212`），因此 `validate-instruction-analysis` 的 `passed:true` **不能**证明 FR 已生成或校验。
- 完整 FR 流需具备：`format_requirement_id`、`requirement_id`、`current_version_id`、`versions[]`（每版含 `version_id` 匹配 `^FR-.+-v[1-9][0-9]*$`、`parent_version_id`、`parent_fingerprint`、`source_excerpt`、`token`、`properties`、`scope`、`fingerprint`、`version_sha256`）、fork/环检测（`format_requirements.py:96-112,140-171`）、schema 校验（`:175-192`）、Coverage 引用交叉核对（`:208-244`）、授权 SHA 锚定（`instruction_coverage.py:336-350,402-412`）。本轮**全部不存在**。
- 结论：**Agent 识别了 17 条格式要求**并写入 `instruction_action=format` 的原子需求，但**没有进入 Batch 1 FR 生成/校验/版本链/SHA 锚定流程**。直接以本地辅助脚本生成基础 Analysis 是造成该结果的实际原因之一（脚本只产出基础 schema 字段）。

### 2.3 影响

17 条格式要求因此**没有**独立版本追溯、语义指纹、并发版本分叉检测、SHA 锚定授权完整性保护，也没有 FR 级 Final Gate 守卫；无法证明某个格式操作对应哪个 FR 版本与原始摘录。

**必须区分**：本轮**实际执行并验证了 FORMAT 操作**（15 个 format change、`format_operation_contract PASS`、独立格式检查 25/25）；本轮**没有验证 FR 版本链**。二者不是同一件事。

## 三、Batch 2 / Batch 3 正式分析与编译路径为何未进入本轮

### 3.1 事实（全 Round 检索）

以下标记在 Round 全部 `.json/.py/.md/.txt` 中**均不存在**：`format_compilation`、`compile_fingerprint`、`format_pass`、`Pass-2`、`effective_format`、`direct_format`、`safety_classification`、`analyze-run-formats`、`--run-format-analysis`、`FR-`、`source_occurrence_id`、`run-safety/1.0`。`runs/` 目录为空；无 `*run*format*.json`。`RUN_LOG.md:27,31-34,49-54` 记录的 `compile-plan` 命令**没有** `--run-format-analysis`。

### 3.2 `plan/add_format_maps.py` 实际承担的工作

读取 `intents.json` 与本地 stdlib 段落 Run 清单 `logs/{CL,Main,SI}_paragraphs.json`；用 `plan_compiler.parse_inline_format_markup` 解析行内格式标记（`:34`）；为每个 replace 构造逐字符格式签名并写一个覆盖全部替换字符的 `replacement_format_map`，源偏移取非零 plain 源字符（`:38-53`）；为 insert 写分段 `inserted_format_map`，其中期刊名/卷号到源斜体/加粗 Run 的偏移是脚本内硬编码（`:70-90`）；输出 `intents.mapped.json`。它**没有**调用 `analyze_docx`、`validate_run_format_analysis`、`compile_format_requirements`、`recompute_properties`、`assign_target_occurrences`、`apply_compiled_formats`、`validate_compile_trace` 或任何 FR 函数。

### 3.3 谁生成了格式操作与映射

- `format_operations`：由现有 `scripts/co_reviewer/plan_compiler.py:47,521-546,738-754` 的行内标记解析器从 Intent 生成（斜体/粗体/上下标），随 Plan 进入授权与 Apply；不是 Batch 3 `format_compiler.py` 的输出。
- `replacement_format_map` / `inserted_format_map`：由本地 `add_format_maps.py` 生成，依据是本地段落 JSON 与硬编码偏移；不是 `analyze-run-formats` 或官方 Format Compiler 的输出。
- 官方入口：`co_reviewer_cli.py:12557-12565`（`analyze-run-formats`）与 `:12183-12187,11693-11722`（`compile-plan --run-format-analysis`，输出 `format_compilation` 与 `compile_fingerprint`）本轮均未调用。

### 3.4 Batch 2 / Batch 3 是否真正执行

- Batch 2 的 Direct Format / Effective Format / Provenance / Safety Classification：由 `run_format_analysis.py:305-360` 生成到每个 Run 行（`run_id`、`direct_format`、`effective_format`、`provenance`、`safety_classification`、`analysis_fingerprint`）。本轮无此 JSON，**未生成**。
- Batch 3 消费 Batch 2 输出：`format_compiler.py:211-254,419-439,509` 需要 Coverage 引用的 FR ID 与 run analysis；本轮两者皆无，**未执行**。

### 3.5 格式检查通过能证明什么、不能证明什么

能证明：原生 Track Changes 中写入了作用域明确的格式修订；Marked/Clean 视图的字符级斜体/粗体/上下标与授权操作一致（`character_format_integrity PASS`、`format_operation_contract PASS`、15/15）；无未跟踪文本；文档质量与 PDF 渲染 PASS。

不能证明：FR 版本链完整性；Batch 2 Run 安全分类（直接/有效格式、来源、安全结论）；Batch 3 编译 trace / fingerprint；每个格式操作由官方编译器从 FR 版本映射到具体 Run 来源与源字符位置；FR 级 Final Gate 守卫。

**不能把“Plan 中存在格式映射”等同于“官方 Format Compiler 已执行”。**

## 四、Clean 文档事后重建的证据链

### 4.1 发现与重建时间

- 所有验收报告 mtime ≤ 21:06:36（13:06:36Z）：`verify_report.json` 21:03:54、`final_gate_report.json/.md` 21:04:36、`independent_acceptance.json` 21:05:09、`hashes.json` 21:05:58、`RUN_LOG.md` 21:06:15、`FINAL_REPORT.md` 21:06:36。
- 三份 Clean DOCX 与三个 `*.clean.compile.json` 的 mtime 为 **21:42:33–21:42:36（13:42:33–13:42:36Z）**，晚于审计提交 `ad3c5fe…`（13:40:43Z）。
- **发现缺失的确切时刻无法确认**：没有任何文件记录该事件；只能确认重写发生在 13:42:33Z，之前是否已缺失、何时发现、由谁执行，**无法确认**。

### 4.2 重建输入与命令

- 输入 Marked DOCX SHA-256（亦为 `apply_report.json` 的 `output_sha256`）：Main `7835a50ef432f441ca302e4914404f6e848732698fbdca04c1c74bf97f7d0a07`；Cover Letter `0c06429d3ef12d3196efc1c390f523cc4d8a99ce4fcda347c2f94800ad5bb444`；SI `691fe765c316b71706dadb7e9eca038beabf7f0ee736dec6085903e7c036f1ca`。
- `reports/apply_report.json` 整文件 SHA-256（本次只读实测）：`167528e698feae9b26f5149316317fcda4d6b5f75624f4d58ef0865090cd7c31`；该值未被验收记录保存，是否即重建时读取的字节只能内容一致意义下推断。
- 命令形式（据 `RUN_LOG.md:52` 与 CLI 接口 `co_reviewer_cli.py:12160-12167`）：`compile-clean-from-marked --marked <marked.docx> --out <clean.docx> --report reports/<name>.clean.compile.json --apply-report reports/apply_report.json`。**逐字完整参数串未持久化，无法确认**。

### 4.3 哈希核对

| 文档 | `hashes.json` / `independent_acceptance.json` 原记录 Clean SHA | 当前实测 | 结论 |
|---|---|---|---|
| Main | `f1b2a3cb4004212a90509778261c71513a61b02c761fd66924fc2afaaebcd8d7` | 同 | 字节一致 |
| Cover Letter | `dbd0dfbb6184092ea3401287883b3d493e16fc6d0da9712d1e8d62051ba97c6a` | 同 | 字节一致 |
| SI | `26b87b26fcc16d0202f33f31e9c15e45a7c9d35891418d2e6896f880b0c558e3` | 同 | 字节一致 |

三个 `*.clean.compile.json` 内的 `marked_sha256` / `clean_sha256` 亦与上述一致。`final_gate_report.json` **不含 Clean DOCX SHA-256**（只有路径与 PASS），因此无法与 Final Gate 做“同一 Clean SHA”的直接比对；`independent_acceptance.json` 含 Clean SHA 且一致。

### 4.4 是否改动其他验收记录

Marked DOCX、`apply_report.json`(13:02:59)、`verify_report.json`(13:03:54)、`final_gate_report.json/.md`(13:04:36)、`hashes.json`(13:05:58)、`RUN_LOG.md`(13:06:15) 的 mtime 均早于 13:42，内容交叉引用仍一致；**未发现被重写**。三个 `*.clean.compile.json` 与 Clean DOCX 同在 13:42 被写入，属派生中间报告。由于 Round 未保存这些 JSON 自身的 SHA-256，哈希级强确认**无法确认**。

### 4.5 字节同一性 vs 连续存在性

**可确认**：重建后字节哈希与原验收记录一致，12 个交付件当前哈希全部匹配。
**无法确认 / 已被证据否定**：Clean DOCX 在 Final Gate（13:04:36Z）至 13:42:33Z 之间是否连续存在、是否为同一物理文件——mtime 显示 13:42 被重写，不能声称“从未被覆盖”。审计分支既有 `README.md` 中“not moved, deleted, overwritten”的表述与 mtime 证据不一致。重建后是否重新通过官方 gate 也无新报告可证，只能确认字节与先前通过 gate 时记录的哈希相同。

## 五、本次真实 DOCX 验收已证明的能力

- 三个文档角色的真实编辑已写入原生 Track Changes；Marked 含 `w:ins/w:del`，Clean 无未解决修订/批注。
- 48 条原子需求的机器授权链、45 个目标的 Preflight/Apply/Verify、15 个格式修订、字符级格式完整性、0 untracked-text 违规、文档质量与 Word COM PDF 渲染均通过。
- 独立 stdlib OOXML 验收：58 项文本检查、25 项格式检查全部通过。
- 原始输入与 Round 副本 SHA-256 一致；最终证据链内部哈希一致。

## 六、本次尚未证明的能力

- Batch 1 FR 版本链（FR ID、语义指纹、父版本/分叉检测、SHA 锚定授权、FR 级 Final Gate）。
- Batch 2 Run 格式分析（Direct/Effective/Provenance/Safety Classification/analysis_fingerprint）。
- Batch 3 官方 Format Compiler 编译 trace、`compile_fingerprint`、FR→Run→源字符位置的完整映射与 `source_occurrence_id`。
- 第 8 次失败的正式机器证据（失败版 plan / Verify 报告 / 逐字错误输出）与 hard-stop 规则是否被遵守。
- Clean DOCX 的连续存在性，以及重建后再次通过官方 gate 的独立报告。
- `ROUND_COMPLETE`（Final Gate 状态为 `PATCH_READY`、`round_status=NOT_VERIFIED`）。

## 七、建议后续补验的工序（本任务不执行）

1. 用完整 Analysis FR 数组重跑 Batch 1：生成 FR 版本链、schema/语义指纹校验、Coverage `format_requirement_ids` 交叉核对、授权 SHA 锚定。
2. 运行 `analyze-run-formats` 生成 Run Format Analysis JSON，并以 `compile-plan --run-format-analysis` 重编译，保留 `format_compilation` / `compile_fingerprint` 与 Pass-2 trace。
3. 对第 8 次失败补做可复现实验：保留失败版 Plan/Apply/Verify 报告，按 `SKILL.md:544` 记录阻塞与工程变更请求，再决定是否重跑。
4. 为 Clean 交付建立连续存在性证据（生成时与交付时的 SHA 绑定、不可变产物清单），避免仅凭事后字节同一性推断。
5. 如需整轮完成声明，补 `verify-final-round` 与正式 Patch 证据，或在报告中明确维持 `PATCH_READY`。

## 八、仍然无法确认的事项（明确清单）

1. 八次失败各自的精确时刻、CLI 退出码与逐字输出（无命令输出日志）。
2. 第 8 次失败究竟由正式 `verify` CLI 抛出，还是仅来自直接调用 `verify_apply_report` 的诊断；是否曾存在失败版 Verify 报告后被删除/覆盖。
3. hard-stop 规则是否实际被触发与遵守（无失败期证据）。
4. 首次失败版 Apply/Plan 是否被保留（首次 `apply_report.json` 已被后续同名文件覆盖）。
5. FR / Run Analysis / Format Compiler 相关产物是否曾短暂存在后删除（现存 Round 中无痕迹）。
6. Clean DOCX 缺失的发现时刻、执行者、逐字命令，以及 13:04–13:42 之间是否连续存在。
7. `apply_report.json` / `verify_report.json` / `final_gate_report.json` 等 JSON 自身的 SHA-256 是否自重建以来未变（Round 未记录）。
