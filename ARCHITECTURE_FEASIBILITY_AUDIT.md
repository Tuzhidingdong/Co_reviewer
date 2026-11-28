# Co_Reviewer 架构可行性审查（只读）

仓库：`Tuzhidingdong/co_reviewer`
代码基线：`32e08dd42613c38a101b720cbf420e33fcff4813`
当前 HEAD：`6593cf54a79ab8376b503fa5f158cbc6199a600b`（相对基线只有 `audit_artifacts/` 文档新增，代码 `scripts/schemas/tests/SKILL.md/.agent` 零差异）
审计证据分支：`audit/real-docx-blind-test-20261128`
报告首次引入提交 SHA：`dba7b77b9b80fc0bb88771a4a37140852ba28d8f`
报告固定提交（本分支 HEAD）：见 GitHub 分支页；该后续提交仅把自身前一提交的 SHA 写入本文件

证据标注约定：**[已证实]** = 代码/真实产物可直接复核；**[合理推断]** = 基于代码路径或产物的一致性推论；**[尚未验证]** = 无证据，明确标出。

---

## 0. 直接回答两个问题

### 问题 1：这次 Agent 的成果是碰运气，还是确实走通了一条可行路径？

**答：真实成功，但不是“已证实的通用路线”，也不是“纯碰运气”。分类为 `PROVEN_SCOPED_PATH`（只证明在一定文档结构上可行）。**

必须区分三件事：

1. **本次真实成功 [已证实]**：三个真实文档完成修改；Apply 45/45 目标、Verify 45/45、独立验收 58 项文本 + 25 项格式检查全过；Final Gate `verdict=READY`、`status=PATCH_READY`。证据：`blind_test_real_docs_fresh_20261128/reports/{apply_report,verify_report,final_gate_report,independent_acceptance}.json`。
2. **当前可复用性 [已证实]**：官方执行链（Analysis 校验 → Coverage 同步 → compile-plan → preflight → 授权 → Apply → Clean → Verify → Final Gate）是确定性的；相同输入产物会得到相同 Plan/授权/输出哈希。本次 8 次失败中 1–7 次都是官方校验的真实失败，Agent 修正的是**输入产物**而非绕过 Gate。证据：`RUN_LOG.md:36-45`、多版 `instruction_coverage.synced*.json` 时间戳、最终链哈希一致。
3. **通用泛化能力 [尚未验证]**：`build_artifacts.py:16,26-34` 硬编码仓库路径、三份文档名与本地段落 JSON；`add_format_maps.py:70-72` 对插入段落使用硬编码源 Run 偏移（斜体 125、加粗 145），且 `replacement_format_map` 是单段扁平映射（`:49-53`）。没有证据表明换一份结构不同的 DOCX（表格、域、书签、混合字体字号、多段插入）仍能自动生成正确映射。

**所以：不是靠运气蒙中了结果——官方 Gate 是真实通过的；但成功路径依赖任务局部脚本与本次文档特征，不能宣称通用。**

### 问题 2：三个 Batch 能否保证稿件修改质量，还是主要增加了程序执行门禁？

**答：三个 Batch 主要增加的是可执行性门禁与溯源记录，没有证据证明它们单独提升了真实稿件的修改质量；它们也没有在本次真实路径中被完整执行。**

- **FR（Batch 1）[已证实]**：提供版本链、语义指纹、SHA 锚定与 Coverage ID 引用；但缺少 FR 时三处校验直接返回 `[]`（`format_requirements.py:69-70,178-179,211-212`），授权只记 `format_requirements_present:false`（`instruction_coverage.py:356`），Final Gate 根本没有 FR 分析文件入口（`co_reviewer_cli.py:12218-12252`）。它不能保证作者意图理解正确，只能记录格式语义并阻止篡改。
- **Run Analysis（Batch 2）[已证实]**：能真实提取 Direct/Effective Format、来源与安全分类（`run_format_analysis.py:186-329,364-404`）；但 CLI 无法传入 FR（`co_reviewer_cli.py:12557-12565` 只有 `--docx/--styles/--theme/--out`），遇到不确定格式是 `BLOCK`（`:214-228,267-293`），不是“解决格式问题”。它本身不改变稿件。
- **Format Compiler（Batch 3）[已证实]**：具备把 FR + Run Analysis 编译成格式操作与 trace 的真实能力（`format_compiler.py:211-439,442-510`），但默认 `compile-plan` 不调用（`co_reviewer_cli.py:11693,11698-11699`），需要外部手工编写 FR 与 run analysis；Batch 4 的 `format_final_gate_guard` 没有 CLI 生产者（`format_execution.py:745-785` 仅测试引用；`final_gate.py:764-774` 只读手供报告）。
- **有替代方案 [已证实]**：本次通过 Agent 行内标记 + `add_format_maps.py` + 官方 Verify 字符级完整性检查，同样证明格式结果正确（25/25 格式检查、`character_format_integrity PASS`）。这说明三个 Batch 不是唯一的质量路径。

---

## 1. 审查范围与可核查状态

- 代码基线与当前 HEAD 的差异仅为审计文档（`git diff --stat 32e08dd..HEAD -- scripts schemas tests SKILL.md .agent` 为空）**[已证实]**。
- 真实运行证据来自审计分支 `audit/real-docx-blind-test-20261128` 的脱敏副本（19 个原文件 + 补充审计 `REAL_DOCX_BLIND_TEST_AUDIT_ADDENDUM.md`）与本地未脱敏 Round。
- 本审查未运行 Apply / Verify / Final Gate，未重生成 DOCX/PDF，未修改代码、Schema、Skill、Workflow 或任何历史产物。

---

## 2. 本次真实成功路径还原

### 2.1 执行链（文本流程图）[已证实]

```
本地只读勘察（自建 stdlib 脚本）
 logs/inspect_docx.py → {CL,Main,SI}_paragraphs.json（段落/Run 文本与格式）
 logs/anchor_report.py → 锚点唯一性；条件项（γ）前提核实
        │
        ▼
Agent 语义判定（plan/build_artifacts.py，任务局部脚本）
 硬编码：仓库路径(:16)、文档名(:26-30)、段落索引、find/replace、行内格式标记
 ⇒ plan/instruction_analysis.json（48 条；17 条 format；analysis_sha256）
 ⇒ plan/instruction_coverage.raw.json（Coverage 1.1 骨架）
 ⇒ plan/intents.json（30 intents）
        │
        ▼
官方 CLI：validate-instruction-analysis（RUN_LOG:27）
        │
        ▼
官方 CLI：sync-analysis-to-coverage（RUN_LOG:31；CLI:11640-11655）
 ⇒ instruction_coverage.synced6.json → finalize_coverage.py 写 coverage_sha256
        │
        ▼
本地脚本 plan/add_format_maps.py（RUN_LOG:32）
 读取 intents.json + 本地段落 JSON；生成 replacement_format_map / inserted_format_map
 ⇒ plan/intents.mapped.json
        │
        ▼
官方 CLI：compile-plan（RUN_LOG:33；CLI:11658-11736，无 --run-format-analysis）
 ⇒ revision_plan.json（30 items / 45 targets / 15 FormatChange / 1 insert_paragraph）
        │
        ▼
官方 CLI：preflight-plan（RUN_LOG:34）⇒ 45/45 safe；revision_plan.preflighted.json
        │
        ▼
本地脚本 build_assessment.py → 官方 CLI：authorize-instruction-plan（RUN_LOG:50）
 ⇒ instruction_authorized.json + instruction_based_authorization.json（机器授权）
        │
        ▼
官方 CLI：apply（RUN_LOG:51）⇒ 3 份 Marked DOCX + apply_report.json
        │
        ▼
官方 CLI：compile-clean-from-marked（RUN_LOG:52）⇒ 3 份 Clean DOCX
        │
        ▼
官方 CLI：verify（RUN_LOG:53）⇒ 45/45、0 failed、CFI PASS
        │
        ▼
官方 CLI：final-gate（RUN_LOG:54）⇒ READY / PATCH_READY，6 份 DOCX 质量与 PDF PASS
        │
        ▼
本地独立脚本 logs/acceptance_check.py → 58 文本 + 25 格式检查（RUN_LOG:58）
```

### 2.2 信息与产物来源分类 [已证实]

| 信息类别 | 来源 | 关键代码位置 |
|---|---|---|
| 真实 Word 文档段落文本与 Run 格式 | 本地 stdlib 解压 `word/document.xml` | `logs/inspect_docx.py`；`build_artifacts.py:31-34`；`add_format_maps.py:21-26` |
| 原子需求边界、action/polarity、find/replace、目标段落索引 | Agent 阅读指令与文档后写入 Python 字面量 | `build_artifacts.py:67-129` 及 30 个 `add_edit`/`add_insert` 调用 |
| 行内格式意图（斜体/粗体/上下标） | Agent 写入 `*…*`/`**…**`/`^…^`/`~…~`；由官方 `parse_inline_format_markup` 解析 | `plan_compiler.py:47,521-546,738-754` |
| `replacement_format_map` | 本地脚本按源 Run 属性签名选“非零 plain 偏移”，单段覆盖全部替换字符 | `add_format_maps.py:38-53` |
| `inserted_format_map` | 本地脚本硬编码斜体/加粗源偏移 | `add_format_maps.py:70-90` |
| 分析/Coverage/Plan 合法性 | 官方校验器独立检查 | `instruction_analysis.py:47-95,98-147`；`plan_compiler.py:433-768`；`instruction_coverage.py:126-157,511-680` |
| Apply/Verify/Final Gate | 官方 CLI 全部 | `co_reviewer_cli.py:8536+,9210+,11907+` |

### 2.3 三个数字分别证明什么 [已证实]

- **45/45 Verify**：每个授权目标的实际 Marked 输出包含精确 `w:del`/`w:ins`、正确段落位置、脚本/字符格式完整性、无未跟踪文本；**不证明**科学语义正确或 FR/Run/Compiler 链存在。
- **58 项文本检查**：独立 stdlib 解析确认每条替换/插入/删除结果出现在 Clean，旧文本出现在 Marked 的 `w:delText`；**不证明**作者意图理解无误。
- **25 项格式检查**：具体 token 在真实 Run 中具有 italic/bold/subscript/superscript；**不证明**格式来源由官方 Compiler 从 FR 与 Run 安全分析推导。

### 2.4 可复用性 A/B/C/D 分类

**A. 已证明可自动、确定性生成的能力 [已证实]**
- 给定合法 Analysis/Coverage/Intents，官方链稳定产出 Plan、授权回执、Marked/Clean、报告与哈希；`analysis_sha256`、`coverage_sha256`、回执规范摘要可复算一致。
- `compile-plan` 对 `find` 唯一性、授权 token、格式契约、插入授权的检查（`plan_compiler.py:509-511,578-607,617-665`）。
- Verify 的原生修订、字符级格式、脚本完整性、未跟踪文本检查（`co_reviewer_cli.py:9285,5287-5308,5385-5503`）。

**B. 依赖本次文档结构或特定脚本的能力 [已证实]**
- `build_artifacts.py:16,26-34` 的硬编码路径/文档/段落索引；`span()`/`exact()` 依赖本地段落 JSON 的唯一锚点（`:37-51`）。
- `add_format_maps.py:70-72` 的硬编码插入源偏移；`:49-53` 的扁平单段映射。
- SI-09 把删除与格式合成整段替换（`build_artifacts.py:430-445`）——针对本次段落重叠的局部策略。

**C. 依赖人工判断、人工修正或隐含假设的能力 [已证实]**
- 48 条原子需求边界、17 条格式要求的具体 token、条件项（γ 已存在故 `not_applicable`）、行号/excerpt。
- 8 次失败中的修正（尤其第 8 次增加显式 `i:false`，`revision_plan.instruction_authorized.json` MT09B 两个格式操作）——属于人工/会话内判断，未持久化为机器证据。
- 假设：源 `find` 跨 Run 时可用一个 plain 源偏移承载整段替换且不丢失语义。

**D. 目前没有证据证明能够通用处理的能力 [尚未验证]**
- 混合字体/字号/颜色组合、样式继承与主题解析、表格/编号/页眉页脚/域/书签/超链接、跨段重叠目标、多段插入、不同语言或非拉丁文本、多份结构差异大的 DOCX。
- 去掉局部脚本后，格式来源自动发现能力基本只剩“Agent 自行理解 + 行内标记”，无程序化证据链。

### 2.5 最终判断

`PROVEN_SCOPED_PATH`。理由：官方执行与验证链是真实且可复核的，因此不是碰运气；但决定“改成什么、格式从哪里继承”的关键输入由任务局部脚本与硬编码偏移提供，缺少跨文档泛化证据，因此不能升级为 `PROVEN_GENERAL_PATH`。**[合理推断]**

---

## 3. 三个 Batch 的实际代码调用与数据流

### 3.1 Batch 1 — FR [已证实]

```
Agent 手写 analysis["format_requirements"][]（无 CLI 生成器；format_requirements.py:2-7）
 → validate_format_requirements(analysis)        format_requirements.py:66-172
 → validate_format_requirements_schema(analysis) format_requirements.py:175-192（无 jsonschema 时 no-op :180-183）
 → crosscheck_coverage_fr(coverage, analysis)     format_requirements.py:208-244（经 instruction_analysis.py:118）
 → sync-analysis-to-coverage 仅复制 FR ID        instruction_analysis.py:150-171
 → authorize-instruction-plan：present=true 时锚定整文件 SHA
      instruction_coverage.py:269,337-356；CLI:11782-11784,11851-11861
 → apply/verify 只在 present=true 时重查文件 SHA  instruction_coverage.py:402-412；CLI:8669,9262
 → final-gate 无 analysis 入口，只读可选 --format-gate-report
      CLI:12218-12252；final_gate.py:764-774
```
缺省时：`format_requirements.py:69-70,178-179,211-212` 全部 `return []`；`compile-plan` 打印“no Format Requirements; non-format Plan left unchanged”（`co_reviewer_cli.py:11698-11699`）。**[已证实]**

数据流缺口：无自然语言→FR 生成器；授权校验只比对整文件 SHA、**不重跑版本链/指纹校验**（`instruction_coverage.py:380-508`）；`validate_instruction_coverage` 调用授权校验时**不传 analysis_file**（`:868-882`），使 FR 锚定在 Final Gate 路径必然跳过。测试 `test_format_requirements_batch1.py:31-327` 只测手工构造对象的库函数，无 CLI、无自然语言生成、无真实 DOCX。**[已证实]**

### 3.2 Batch 2 — Run Analysis [已证实]

```
analyze-run-formats --docx [--styles --theme] --out        CLI:12557-12565, handler 11607-11624
 → analyze_docx(docx_path, styles_path, theme_path, fr=None, fr_current_ids=None)
      run_format_analysis.py:364-404
 → 每 Run：direct_format / effective_format(含 provenance)
      / resolution / safety_classification / property_decisions / analysis_fingerprint
      run_format_analysis.py:186-329
 → validate_run_format_analysis                            run_format_analysis.py:407-447
      （fingerprint 只查 64-hex 格式，不重算 :432-433）
```
CLI **没有** `--fr` / `--fr-current-ids`，`analyze_docx` 的 FR 参数无法从命令行传入（`co_reviewer_cli.py:12557-12565`）；因此 FR 绑定的 `EXPLICIT_OPERATION_REQUIRED` / `UNSAFE_STYLE_INHERITANCE` 只能通过直接 Python 调用触达（测试 `test_run_format_analysis_batch2.py:170-181`）。表格/编号的 rPr 上下文在 `analyze_docx` 中未填充（`:385-397`）。模糊时全部 `BLOCKED_IF_UNKNOWN`（`:135-137,267-278`）。测试 12 项，全部合成 DOCX。**[已证实]**

### 3.3 Batch 3 — Format Compiler [已证实]

```
compile-plan --run-format-analysis <json>                  CLI:12183-12187
 条件：has_format_requirements(analysis, coverage)         CLI:11698；format_compiler.py:26-36
 → compile_format_requirements(plan, analysis, coverage, run_analysis, source_docx)
      format_compiler.py:211-439
      - FR 必须来自 coverage.format_requirement_ids（:248-257）
      - FR / Run SHA 与源 DOCX 绑定（:259-284）
      - 唯一 find / token / 结构上下文 / 安全 base run（:299-369）
      - difflib 生成 replacement_format_map；歧义 → pending/BLOCKED（:109-183,366-369）
      - trace: status/source_sha/input_validation/results/blockers/compile_fingerprint（:417-439）
 → recompute_properties + assign_target_occurrences（format_execution.py:124-241,274-357）
 → apply_compiled_formats 写入 Plan 的 replacement_format_map / format_requirement_ids /
      format_operations(source_occurrence_id) / plan.format_compilation（format_compiler.py:442-510）
```
默认不调用：CLI `:11693` 只在 flag 存在时进入；无 FR 时 `:11698-11699` 跳过整个块。只有一个调用点 `CLI:11703`。测试 19 项（`test_format_compiler_batch3.py`），均为合成 DOCX，无真实稿件。**[已证实]**

### 3.4 Batch 4（相关扩展）[已证实]

`apply --format-run-analysis` 触发格式 preflight（`co_reviewer_cli.py:8621-8659`），`verify --format-execution-record` 触发独立格式验证（`:9298-9342`），`format_final_gate_guard`（`format_execution.py:745-785`）**没有 CLI 生产者**，Final Gate 只读手供报告（`final_gate.py:764-774`）。测试 30 项，含一次真实文件级 Apply 但内容为合成 DOCX（`test_format_execution_batch4.py:354-399`）。

### 3.5 关键结论

- 三个 Batch 的代码**实现了真实能力**（分析/编译/执行校验），但**入口未完全接通**：无 FR 生成器、CLI 无法绑定 FR 到 Run Analysis、无格式 Guard 的 CLI 生产者。
- 本次真实路径**完全没有进入这三条链**：`instruction_analysis.json` 无 `format_requirements[]`、授权 `format_requirements_present=false`、Plan 无 `format_compilation`、无 Run Analysis JSON。证据：`REAL_DOCX_BLIND_TEST_AUDIT_ADDENDUM.md:64-113`、Round `plan/*`。**[已证实]**

---

## 4. 质量保障 vs 执行门禁

判断标准：**质量保障** = 能减少错误修改并能用独立证据证明结果正确；**执行门禁** = 发现输入/来源/授权/风险不完整并拒绝执行。

| 能力 | 能否直接提高修改质量 | 能否提供可验证证据 | 是否可能单纯阻断执行 | 结论 |
|---|---|---|---|---|
| FR | 仅间接：强制显式格式语义；本身不改 DOCX。无证据证明提升真实稿件正确率 [尚未验证] | 版本链/指纹/整文件 SHA 锚定可被复算；但 Final Gate 不重验 [已证实] | 是：声明存在但文件/SHA 不符即失败；缺省时完全放行 [已证实] | 主要是治理/门禁，质量贡献未证实 |
| Run Analysis | 不能直接；它只描述格式与风险 [已证实] | direct/effective/provenance/safety 字段可查；fingerprint 只查格式不重算 [已证实] | 是：不明确即 `UNRESOLVED/UNSAFE/BLOCKED_IF_UNKNOWN` [已证实] | 主要是诊断+阻断，需 Compiler 才能影响输出 |
| Format Compiler | 具备减少格式错误的机制（唯一 find、安全 base run、来源映射）；真实稿件端无证据 [合理推断] | trace 有 `compile_fingerprint`/results/blockers，可复核；但默认不启用 [已证实] | 是：任一歧义 → `BLOCKED` [已证实] | 有潜力，但链路未接通，不能算已证实的质量贡献 |

### 必须回答的五个问题

1. **有 FR 是否意味着作者意图理解正确？** 否。FR 只记录 Agent 写的格式语义与版本；`instruction_analysis` 的 polarity 语义也由 Agent 判定，Python 只做结构/哈希/逐字对齐（`instruction_analysis.py:47-95`）。**[已证实]**
2. **有 Run Analysis 是否意味着格式选择正确？** 否。它输出的是“格式状态 + 安全分类”，错误或不一致的 FR 仍可产生错误格式；且模糊时不给答案只 BLOCK（`run_format_analysis.py:267-293`）。**[已证实]**
3. **有 Format Compiler 是否意味着生成的格式操作正确？** 否。它保证可追踪与确定性，但 FR 本身错误时编译结果同样错误；它只是把输入转换为操作（`format_compiler.py:296-416`）。**[合理推断]**
4. **三个 Batch 全通过仍可能产生错误稿件吗？** 是。它们都不验证科学语义、漏改、目标漂移；只有 Coverage 原子需求覆盖与 Apply 后独立 Verify 能发现部分此类错误。**[合理推断]**
5. **不经过三个 Batch 能否证明修改正确？** 能，但必须提供等价证据：本次的 `instruction_analysis` 覆盖、目标绑定、授权、原生 Track Changes、Verify 字符级格式、Marked/Clean 一致性、独立文本/格式检查（58/25）。**[已证实]**

### 真正保护稿件的质量机制（必须保留，与实现技术无关）

- 原始指令 SHA 绑定 + 逐行逐字 Coverage（`instruction_coverage.py:539-605`）。
- 原子需求 → Plan 目标的精确绑定与全目标覆盖（`:46-157`）。
- `authorized_change` / 保护 token / 授权意图绑定（`insert_authorization.py:205-242,245-319`；`plan_compiler.py:617-665`）。
- 机器授权回执与人工批准严格区分（`:259-377`；`SKILL.md:16`）。
- 官方原生 Track Changes Apply，禁止替代 writer（`document_engine.py:1173-1214`；`SKILL.md:541-543`）。
- Apply 后独立 Verify：修订 ID、文本、位置、脚本/字符格式、未跟踪文本（`co_reviewer_cli.py:4694-5503`）。
- Final Gate 角色化质量、Word PDF 渲染、零静默回退（`final_gate.py:592-785`）。
- 独立解析的 Marked/Clean 一致性与来源/输出哈希核对（`logs/acceptance_check.py`；`reports/hashes.json`）。

**这些是“必须存在的能力”；三个 Batch 只是实现其中一部分能力的一种技术方案。**

---

## 5. Skill 可被绕过的程序原因

### 5.1 文字约束 vs 代码约束 [已证实]

`SKILL.md:16,535-544,574-623`、`.agent/workflows/co-reviewer.md:11,19,21-26,37-41` 主要是叙述性规定。代码中真正落实的是哈希/结构/状态检查；**“Agent-produced”、三个 Batch 的顺序与存在性、生成者身份都没有代码字段或校验**：`validate_instruction_analysis`（`instruction_analysis.py:47-95`）没有 `producer`/`agent_id`/`run_id`；全仓 grep 未发现绑定生成器身份的字段。

### 5.2 具体绕过点 [已证实]

| # | 机制 | 代码位置 | 性质 |
|---|---|---|---|
| 1 | `compile-plan` 只有 `--intents` 必填，Coverage/Analysis 可选且自动发现 | `co_reviewer_cli.py:12169-12188,11589-11604,11669-11679` | 程序设计允许的灵活性 |
| 2 | 若无 Analysis，crosscheck 全部跳过 | `co_reviewer_cli.py:11681-11686,11723-11726` | 同上 |
| 3 | 自造 TXT + 自算 SHA + 逐字 excerpt + 自算 `analysis_sha256` 即可通过 Analysis 校验 | `instruction_analysis.py:47-95` | 只校验自洽，不校验作者身份；违反 Workflow 约定 |
| 4 | 省略 FR 即可跳过所有 FR 校验与授权锚定 | `format_requirements.py:69-70,178-179,211-212`；`instruction_coverage.py:336-356,402` | 程序设计的 fail-open |
| 5 | 省略 `--run-format-analysis` / `--format-run-analysis` / `--format-execution-record` 即可跳过 Compiler/Preflight/Verify | `co_reviewer_cli.py:11693,8622,9298` | 可选参数 |
| 6 | Final Gate 只读可选、未哈希绑定的 `format_gate`，且无 `--instruction-analysis` | `co_reviewer_cli.py:11973-12005,12218-12252`；`final_gate.py:764-774` | 可省略或手供报告 |
| 7 | 辅助脚本可直接产出同构 JSON；官方校验只认结构 | `build_artifacts.py:447-472`；`add_format_maps.py:93`；`plan_compiler.py:433-768` | 程序允许，违反 Workflow 约定 |

### 5.3 应在哪个程序入口落实

**[合理推断，非已实现]** 若目标是强制“最终证据与不变量”而非强制 Batch 名称/顺序，应把约束放在：
- `compile-plan` argparse / `cmd_compile_plan`（`co_reviewer_cli.py:12169-12188,11658-11736`）：要求证据清单或显式无 FR attestation；
- `authorize-instruction-plan`（`:11761-11883`）与 `authorize_instruction_based_plan`（`instruction_coverage.py:259-377`）：把 FR/格式来源哈希变成必选不变量，而非 `if present`；
- `apply`（`:8536+,3018-3050`）、`verify`（`:9210+,9253-9271`）、`final-gate`（`:11907+,final_gate.py:388-785`）：把格式来源与 trace 校验从可选 flag 提升为“有格式变更即必需”；
- `format_final_gate_guard`（`format_execution.py:745-785`）：提供 CLI 生产者并把输出哈希绑定进回执；
- `validate_instruction_analysis`（`instruction_analysis.py:47-95`）：增加生成者/运行 ID 绑定（或由上层证据清单绑定），使“谁写的判定”可追溯。

---

## 6. 三种（+一种）架构对比

| 维度 | A 原三 Batch 强制串行 | B 本次简化路径（原样） | C 混合：基础不变量 + 按需模块 + 等价证据 | D（C 的程序化形态）证据契约架构 |
|---|---|---|---|---|
| 正确性保障 | 依赖 FR/Run/Compiler 质量；FR 生成未实现 | 依赖 Agent 判定 + 官方 Verify | 基础不变量 + 按需强模块 + 独立验证 | 以“必需证据清单”为唯一强制项，模块可替换 |
| 真实端到端可行性 | **未证实**：需手工 FR，CLI 无法绑定 FR 到 Run Analysis [已证实] | **已证实在本次文档成功**；跨文档未证 [已证实] | 合理推断可端到端，待真实验证 [尚未验证] | 同 C |
| 泛化能力 | 理论强，实现缺口限制 [已证实] | 弱：硬编码路径/偏移/段落索引 [已证实] | 中-强：能力按需要加载，不绑定文档结构 [合理推断] | 强：只约束证据，不约束内部实现 [合理推断] |
| 成本/简单稿件 | 高：三套 JSON + 版本链 + 预检 | 中：局部脚本维护成本被隐藏 | 低-中：简单文档只走基础链 | 低：证据清单可裁剪 |
| 失败定位与恢复 | trace/blockers 清晰，但入口未接通 | 8 次失败靠会话内修补，失败证据未保留 | 可由不变量失败点定位；需保留失败产物 | 同 C |
| Agent 可绕过性 | 当前代码下可绕过（§5） | 可绕过 | 关键证据不可绕过 | 最低：证据缺失即拒绝 |
| 维护成本 | 高（三模块 + 版本链 + guard） | 隐性高（任务局部脚本） | 中 | 中低 |
| 对质量的可证明贡献 | 未证实 | 本次格式结果已证实、来源链未证实 | 待验证 | 待验证 |

**推荐：方案 C**，其程序化落地采用 D 的“证据契约”形态：强制**最终证据与不变量**，不强制三个 Batch 的名称与顺序；任何替代路线只要提供等价、可验证证据即可执行。方案 A 在现有代码下无法无损端到端运行；方案 B 依赖任务局部脚本，不可直接复用。

---

## 7. 最优方案与明确结论

### 7.1 唯一推荐

**采用方案 C（按需能力模块 + 强制证据契约）。** 不推荐“保留现有架构、后续持续完善”作为默认答案。

### 7.2 必须保留的机制

1. 原指令 SHA + 逐字 Coverage 1.1 + 原子需求全目标绑定。
2. Agent 指令理解（action/polarity）与官方校验/交叉核对。
3. 机器授权回执（明确非人工批准）+ 人工批准仅用于真实例外。
4. Plan 精确 `find` 唯一性、保护 token/授权意图、格式契约。
5. 官方 Apply（原生 Track Changes）+ 官方 Verify + Final Gate + 角色化质量/PDF + 零静默回退。
6. 独立 Marked/Clean 文本与格式验收、全部 SHA 对照。

### 7.3 按需保留的能力模块

- **FR**：当格式要求需逐项追踪、版本变更、授权完整性或跨轮次复用时启用；否则不强制。
- **Run Analysis**：当原文格式继承、混合 Run、样式/主题来源不明确时启用。
- **Format Compiler**：当 FR + Run Analysis 齐备且格式操作复杂时启用，替代 `add_format_maps.py` 一类局部脚本。
- **Batch 4 执行证据**：一旦启用 Compiler，强制 preflight + execution record + verify + gate guard。

### 7.4 应合并或删除的设计

- 合并 FR 的语义字段与 `instruction_analysis` 的格式行：FR 只保留版本链/指纹/授权锚定，避免双重建模；Coverage 的 `script_format` 应与 FR token/properties 交叉核对（`instruction_coverage.py:606-626` 目前无 FR 核对）。
- 删除“所有文档必须经过三 Batch”的顺序性规定；改为证据契约。
- 把 `format_final_gate_guard` 内建到 `final-gate`，取消手供 `--format-gate-report` 的外部依赖。
- 把 `add_format_maps.py` 的能力吸收为官方 Format Compiler 的降级模式（无 FR 时基于 Run 清单生成有来源的映射），而非保留任务局部脚本。

### 7.5 必须在程序中强制落实的门禁 [合理推断]

1. `compile-plan`：Plan 中存在格式操作时，必须存在 `format_compilation` trace 或等价的格式来源证据（含源字符偏移与安全分类）。
2. `authorize-instruction-plan`：授权回执必须绑定该证据的 SHA；不能仅因 `format_requirements_present=false` 跳过。
3. `apply`/`verify`：格式来源证据作为必选校验，而非可选 flag。
4. `final-gate`：有格式变更时，`format_gate` 必须由程序内生成并哈希绑定，不接受手供 JSON。
5. 生成链身份：`instruction_analysis` / Coverage / FR 必须绑定生成者/运行 ID，或由上层证据清单绑定；禁止仅靠同目录自动发现。

### 7.6 下一步最值得做的一个真实验证

**用一份结构不同的真实 DOCX（含混合 Run、样式继承、至少一处表格或编号段落、至少一次段落插入），完整走一遍“按需能力”链：Agent 指令理解 → Coverage → 按需生成 FR + Run Analysis（经可绑定的 CLI）→ Format Compiler → 官方 Apply/Verify/Final Gate，并保留所有失败期证据。** 该验证一次性回答当前最大的未知：格式映射能否脱离硬编码偏移、CLI 能否绑定 FR、Compiler 能否替代局部脚本。

---

## 8. 尚未验证的关键问题 [尚未验证]

1. 换结构不同的真实 DOCX，`replacement_format_map`/`inserted_format_map` 能否自动生成且正确。
2. FR 从自然语言自动生成是否可实现；目前设计明确由 Agent 手写。
3. CLI 能否把 FR 绑定到 Run Analysis；当前 CLI 无参数。
4. `format_final_gate_guard` 能否由 CLI 生成并哈希绑定。
5. 混合字体/字号/颜色、表格、编号、域、书签、跨段重叠目标、多段插入的真实端到端行为。
6. 第 8 次 Verify 失败的正式机器证据与 hard-stop 是否被遵守（见补充审计 §八）。
7. 方案 C/D 的实际质量效果与成本；目前是设计判断，不是实测结论。

---

## 9. 结论

- 本次真实 DOCX 验收**是真的**，但成功路径属于 `PROVEN_SCOPED_PATH`：官方执行链通用、输入生成链任务局部。
- 三个 Batch 是**部分实现、入口未接通、以 fail-closed 门禁为主**的模块；不能宣称它们保证稿件质量，也不能用它们的单元测试替代真实稿件端到端验证。
- 最合理的架构是**方案 C**：强制最终证据与不变量，按需调用 FR / Run Analysis / Format Compiler，允许等价替代路线。
- 必须保留的是质量机制本身，不是三个 Batch 的名称与顺序；必须删除的是强制串行的顺序性规定与外部手供格式 Gate 报告；必须合入程序的是证据契约与生成链身份绑定。

---

## 10. 报告提交信息

- 仓库：`Tuzhidingdong/co_reviewer`
- 审查基线：`32e08dd42613c38a101b720cbf420e33fcff4813`
- 报告分支：`audit/architecture-feasibility-20261128`
- 报告首次引入提交 SHA：`dba7b77b9b80fc0bb88771a4a37140852ba28d8f`（由后续固定提交写入）
- 本报告链接：`https://github.com/Tuzhidingdong/co_reviewer/blob/audit/architecture-feasibility-20261128/ARCHITECTURE_FEASIBILITY_AUDIT.md`
