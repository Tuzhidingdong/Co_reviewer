# Co_Reviewer 真实文档盲测 — 最终报告（公开仓库脱敏版）

> 本文件是公开仓库用的**脱敏副本**。原始 Round 保留在本地、未被修改。
> 论文正文、指令原文逐字摘录、作者姓名以及 DOCX/PDF 正文件均**未上传**。
> 所有 SHA-256、操作/需求/目标 ID、状态、时间、修正过程与验证结果均与本地原始验收记录一致，未作任何伪造或改动。

## 一、结论

**代码与流程验证通过；真实文档验收通过；无未通过项。**

唯一作用域说明：Final Gate 状态为 `PATCH_READY`（patch 作用域），不是 `ROUND_COMPLETE`。本直接指令路径没有正式 Patch/RevisionLedger，故未运行 `verify-final-round`。这不影响三份真实文档的修改验收。

## 二、基线与隔离

| 项目 | 值 |
|---|---|
| 基线提交 | `32e08dd42613c38a101b720cbf420e33fcff4813` |
| 基线分支 | `feat/format-real-docx-acceptance-batch5-20261128` |
| GitHub 仓库 | `Tuzhidingdong/co_reviewer`（公开仓库，故执行脱敏上传） |
| Round 目录 | `<ROUND_ROOT>`（本地独立目录） |
| Skill/Workflow | 基线仓库自带，未使用生产旧版 Skill / V1 脚本 / 历史数据 |
| 原始输入 | 四份原始文件仅在本地复制并使用；SHA-256 运行前后相同；未写入原目录 |

## 三、原始文件身份（文件名中的作者姓名已脱敏）

| 角色（文件名脱敏） | 大小 (B) | SHA-256 |
|---|---:|---|
| 主论文 `.docx` | 4,478,596 | `2C7F55B85721F1F73ACF300E25367BDCC202E25F9AD3FEB3E5CF0205C0DFA051` |
| 投稿信 `.docx` | 29,744 | `9FBF54EBA50D7080405BABC6630640077F7AD88D0654FFB64B54499156964CD5` |
| 补充信息 `.docx` | 5,001,910 | `BDD617FAA4AA158E038F7AB32CAE0AA6D2763088928FF555A4199B8F58F84C78` |
| 修改指令 `.txt` | 22,692 | `D544523BF733ADD1BFEFF5FC4D0112BA9CAACAADA592652268D3051C30FCDB5F` |

运行后四份文件 SHA-256 与上表完全相同；Round 副本哈希逐一相同（`reports/hashes.json`）。

## 四、代码与流程验证通过

- `validate-instruction-analysis`：`passed: true`。Agent 独立判定 48 条原子需求；`instruction_action` / `instruction_polarity` 未经人工填写，由 Analysis 经官方 `sync-analysis-to-coverage` 确定性复制到 Coverage，`compile-plan` 再交叉核对 Plan。
- 新增授权机制：48 条需求（47 planned、1 not_applicable）、45 个 Plan 目标全部进入 `instruction_based` 授权。授权回执为机器记录，明确 **not represented as human approval**；未伪造人工批准。
  - 条件项：`insert / conditional`，源文档条件前提为假，判为 `not_applicable` 并附证据，未生成操作。
  - 动作覆盖：`replace`、`delete`、`insert`、`format`；格式含斜体/加粗/下标/上标。
- `compile-plan`：30 revision items / 45 targets（15 FormatChange + 1 insert_paragraph）；`preflight-plan`：45/45 safe。
- `apply`：7（Cover Letter）/ 32（Main）/ 6（SI）编辑写入原生 `w:ins` / `w:del`。
- `compile-clean-from-marked`：三份 Clean 由各自 Marked 确定性编译。
- `verify`：45/45 targets verified，0 failed，0 untracked-text violations。
- `final-gate`：`verdict=READY`、`status=PATCH_READY`；八项 gate 全 PASS；30 inputs / 30 applied text edits / 15 applied format changes / 0 failed / 0 manual fallback / 0 skipped format ops；六份交付 DOCX 的角色化质量与 Word COM PDF 渲染全 PASS。
- REPLACE / DELETE / FORMAT 与格式保真未退化；8 次失败尝试均按 fail-closed 修正（见脱敏 `RUN_LOG.md`）。

## 五、真实文档验收通过（独立解析，不依赖 CLI 退出码）

| 文档 | Marked 原生修订 | Clean 修订/批注 | 文本检查 | 格式检查 |
|---|---|---|---|---|
| Main | 23 × `w:ins`, 21 × `w:del` | 0 / 0，无 comment part | 全部通过 | 全部通过 |
| Cover Letter | 4 × `w:ins`, 4 × `w:del` | 0 / 0，无 comment part | 全部通过 | 全部通过 |
| SI | 3 × `w:ins`, 3 × `w:del` | 0 / 0，无 comment part | 全部通过 | 全部通过 |

合计 58 项文本检查、25 项格式检查全部通过；逐字期望文本已从公开副本中删除，仅保留检查数量、PASS/FAIL、修订计数与哈希（`reports/independent_acceptance.json`）。

## 六、关键机器指纹

| 记录 | SHA-256 |
|---|---|
| 修改指令原文 | `d544523bf733add1bfeff5fc4d0112ba9caacaada592652268d3051c30fcdb5f` |
| `instruction_analysis.json` | `2e20e32fa1e9103e343a8c4cfb53ae8a307835ef5fe3effcc5b9b5aaa8abecd0` |
| `instruction_coverage.json` | `82d16c8cae10f19693938d67f3c595c73e450da0430f75c99b79d3ce533a04f6` |
| preflighted Plan | `b513ab9aa4d2831f50faa228276f8e576d1d9d3a10e7d59aa5197b22d2a7a3e1` |
| instruction-authorized Plan | `148c6d4c14d372b5e29b28066665b02e5edadc7866df9274469a376903c51f14` |
| `instruction_based_authorization.json` | `10fac4f2f888cda967133a73d71c473f8d2d52c25b878eb33064eaddb10cfb2b` |

时间（UTC）：preflight `2026-11-28T13:02:13.093486`；authorization `2026-11-28T13:02:14.318758`；final gate `2026-11-28T13:04:36.722734`。

## 七、交付文件与限制

本公开仓库副本**不包含**：三份 Marked DOCX、三份 Clean DOCX、六份 PDF、四份原始输入 DOCX/TXT、含逐字正文的未脱敏 JSON 与日志。原因：仓库为公开仓库，按数据安全要求不得公开含论文正文、个人信息或原始文本内容的文件；不得为脱敏伪造或改变验收数据。

本副本**包含**：脱敏后的 `FINAL_REPORT.md`、`RUN_LOG.md`、全部计划/授权/Apply/Verify/Final Gate/独立验收/哈希机器证据的脱敏投影，保留 SHA、ID、状态、时间与验证结果。完整 DOCX/PDF 仅保存在本地原始 Round，未上传、未移动、未删除。
