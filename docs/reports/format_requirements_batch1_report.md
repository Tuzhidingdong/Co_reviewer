# Phase 3 / Batch 1 — Format Requirements 数据模型、版本链与完整性锚定报告

日期：2026-11-28

## 1. 基线与分支身份

- 基线仓库：`E:\Writting\Capillary_bridge\08\co_reviewer_runs\instruction_intent_producer_minfix_20261128\isolated_repo`
- 基线 HEAD：`a7e32f1b79994173439cb2755ef52a9c99ce872b`（`master`，检查时工作区干净）
- 本地开发分支：`feat/format-requirements-batch1-20261128`（从基线创建，未推送）
- 最终 commit SHA：`FINAL_COMMIT_SHA_PLACEHOLDER`
- 未添加 remote，未 fetch/pull/push，未创建 GitHub 分支或 PR，未修改其他分支。

## 2. 修改文件与目的

| 文件 | 状态 | 目的 |
|---|---|---|
| `scripts/co_reviewer/format_requirements.py` | 新增 | FR 规范化序列化、语义 fingerprint、版本 SHA、版本链校验、Coverage FR 引用检查 |
| `schemas/format-requirements.schema.json` | 新增 | Draft 2020-12 FR Schema，表达版本链与语义字段约束 |
| `scripts/co_reviewer/instruction_analysis.py` | 修改 | 调用 FR 结构与 Schema 校验；`analysis_to_coverage` 只复制 FR ID 引用；Coverage 交叉检查纳入 FR |
| `scripts/co_reviewer/instruction_coverage.py` | 修改 | authorization Receipt 绑定 FR 文件 SHA-256 与存在性标志；验证时 fail-closed 复核 |
| `schemas/instruction-coverage.schema.json` | 修改 | Coverage 增加 `format_requirement_ids` / `format_coverage` 引用字段（非必填，不复制语义） |
| `scripts/co_reviewer_cli.py` | 修改 | `authorize-instruction-plan` 支持 `--instruction-analysis`；apply/verify 传入同一路径做锚点校验 |
| `tests/test_format_requirements_batch1.py` | 新增 | Batch 1 定向测试（FR Schema、fingerprint、版本链、外部锚点、Coverage 引用） |
| `docs/reports/format_requirements_batch1_report.md` | 新增 | 本报告 |

本批次未修改 Run 安全映射、`replacement_format_map` 生成、Pass-2 编译、Apply/Verify/Final Gate 主流程或真实 DOCX。

## 3. FR 数据结构示例

```json
{
  "schema_version": "1.0",
  "source_instruction": {"file": "<original instruction>", "sha256": "<sha256>"},
  "analyses": [{"requirement_id": "REQ-T-01-EDIT", "...": "existing fields"}],
  "format_requirements": [
    {
      "schema_version": "1.0",
      "format_requirement_id": "FR-REQ-T-01-EDIT-01",
      "requirement_id": "REQ-T-01-EDIT",
      "current_version_id": "FR-REQ-T-01-EDIT-01-v2",
      "versions": [
        {
          "format_requirement_id": "FR-REQ-T-01-EDIT-01",
          "version_id": "FR-REQ-T-01-EDIT-01-v1",
          "parent_version_id": null,
          "parent_fingerprint": null,
          "source_excerpt": "format requirement: token-A superscript.",
          "token": "token-A",
          "properties": {"vertAlign": "superscript"},
          "scope": {"replacement": "<replacement text>", "start": 11, "end": 18, "occurrence_index": 0},
          "semantic_role": "exponent",
          "status": "superseded",
          "reviewer_decision_ref": null,
          "authorization_ref": null,
          "fingerprint": "<semantic fingerprint v1>",
          "version_sha256": "<full version hash v1>"
        },
        {
          "version_id": "FR-REQ-T-01-EDIT-01-v2",
          "parent_version_id": "FR-REQ-T-01-EDIT-01-v1",
          "parent_fingerprint": "<semantic fingerprint v1>",
          "reviewer_decision_ref": "<existing reviewer decision id>",
          "status": "confirmed",
          "...": "same semantic fields plus new fingerprint/version_sha256"
        }
      ]
    }
  ]
}
```

权威语义只在 `format_requirements[].versions[]` 中；当前版本指针 `current_version_id` 与历史版本数组分离；历史版本不可原地覆盖。

## 4. Schema 校验方式

- 新增 `schemas/format-requirements.schema.json`（Draft 2020-12），约束 FR 列表、版本对象、父链字段、fingerprint/version_sha256 格式、scope 结构和必填语义字段。
- `validate_instruction_analysis()` 调用 `validate_format_requirements_schema()`：当 `jsonschema` 可用时按 Schema 校验（CI 测试依赖包含该库）；无论是否可用，都会执行 Python 端同语义约束校验，避免运行时缺库时静默降级。
- Coverage Schema 增加引用字段但仍保持 1.1 兼容；FR 语义字段不允许出现在 Coverage 中，`crosscheck_coverage_fr()` 会拒绝 `format_token`/`format_properties`/`format_scope` 等重复定义键。

## 5. Fingerprint 与 version SHA 规则

- canonical JSON：`json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`；不执行 NFC 归一化；数组顺序保持原样。
- `fingerprint`：SHA-256(canonical(仅语义字段))。语义字段包括 `format_requirement_id`、`source_excerpt`、`token`、`properties`、`scope`、`semantic_role` 等；状态、历史容器、授权引用、编译/执行状态与当前版本指针均不参与。
- `version_sha256`：SHA-256(canonical(完整版本对象，排除 `version_sha256` 自身))；用于检测历史版本被覆盖，不形成自引用。
- 文件 SHA-256：整个 `instruction_analysis.json` 的字节哈希，写入既有 `instruction_based_authorization.json` 作为外部锚点。
- Git commit SHA：仅标识源码版本，不替代以上任一哈希。

## 6. 外部可信锚点的实际绑定与验证路径

复用 V3 现有 `instruction_based_authorization.json` 可信链：

1. `authorize-instruction-plan --instruction-analysis <path>` 读取并校验该文件中的 FR；若存在 FR 但未提供该参数，直接报错（fail closed）。
2. `authorize_instruction_based_plan()` 将 `format_requirements_file`、`format_requirements_file_sha256`、`format_requirements_present` 写入 Receipt，并由既有 `instruction_based_authorization_sha256()` 绑定。
3. `validate_instruction_based_authorization()` 在 Apply/Verify 授权复核时，若 Receipt 声明 FR 存在，要求传入同一文件、校验文件存在且 SHA 与绑定值一致；文件缺失/锚点缺失/SHA 不一致/版本链断裂均返回明确错误，不降级。
4. 该改动只增加完整性绑定，不改变授权判定对象、Reviewer 生命周期或 Receipt 的授权含义；FR 不产生独立授权结论。

## 7. 新增测试与结果

`tests/test_format_requirements_batch1.py` 共 15 个测试：

- A. Schema/基础结构：合法 FR、缺字段、非法 token/properties、scope 错误、重复 ID、悬空引用、非法当前指针。
- B. Fingerprint：语义相同/不同、状态变化不变、canonical key 顺序、版本 SHA 可重复且自排除。
- C. 版本链：合法父子链、父不存在、父 fingerprint 不一致、历史篡改、version ID 内容冲突、循环、断链。
- D. 外部完整性锚定：绑定后一致、文件修改、文件删除、锚点缺失、无锚点拒绝授权。
- E. Coverage 引用：只引用 FR ID、不复制 token/properties/scope。

定向命令：

```
PYTHONPATH=scripts python -m unittest discover -s tests -p 'test_format_requirements_batch1.py' -v
```

结果：15/15 PASS，退出码 0。

相关回归：`test_instruction_analysis_producer.py` 9/9 PASS（退出码 0）；`test_instruction_based_authorization.py` 11/11 PASS（退出码 0）。

完整测试：

```
PYTHONPATH=scripts python -m unittest discover -s tests -v
```

结果：Ran 790 tests，787 PASS、2 FAIL、1 ERROR，退出码 `1`。

失败项均为 **Batch 1 之前已存在**，通过把仓库四个修改文件恢复为 `a7e32f1` 基线内容、移除本批新增文件，在独立临时目录重建基线树后逐项复现（退出码均为 1）：

1. `test_numbering_transaction.py:test_author_decision_can_be_patch_provenance_without_fabricating_comment_ids` — 测试正则期望旧错误消息，实际错误消息含 `instruction_requirement_ids`；基线同样 FAIL。
2. `test_two_pass_overlap.py:test_two_target_compiled_plan_text_then_format_change_passes_verify` — 既有 `replacement_format_map`/script integrity 问题；基线同样 FAIL。
3. `test_revision_workflow.py:test_repreflight_of_changed_approved_source_requires_renewed_approval` — `author_approval scope changed for R1-C1`；基线同样 ERROR。

以上三项均不涉及 FR 数据模型、版本链、外部锚定或 Coverage 引用；Batch 1 未修改相关实现。

## 8. 兼容性检查

- 旧 `instruction_analysis.json`（无 `format_requirements` 字段）继续通过校验；FR 相关检查为空操作。
- 旧 Coverage 1.1 继续可用；新增字段为可选。
- 旧 Receipt 无 FR 绑定时，授权验证跳过 FR 文件检查；新 Receipt 有 FR 时强制检查。
- 未自动迁移任何旧 Plan，也未猜测历史自由文本中的格式要求。
- 未改动 Apply/Verify/Final Gate 的整体生命周期或真实 DOCX 处理。

## 9. 未完成项 / BLOCKED

- Batch 1 按范围不实现：Run 安全映射、确定性格式编译器、Pass-2 编译、Apply/Verify/Final Gate 格式链路、旧 Plan 迁移分类执行策略。这些留待后续批次。
- 未在 Batch 1 中修改 Final Gate 主流程；FR 锚点通过 Apply/Verify 的既有授权复核路径执行。若后续要求 Final Gate 独立复核 FR 文件 SHA，需要单独授权的增长点。
- 完整套件 3 个失败项已确认为基线既有问题（见第 7 节），本批未修改相关代码，也未降低测试期望。

## 10. `git status` 最终结果

`FINAL_GIT_STATUS_PLACEHOLDER`

## 11. 三个最终问题

1. **现在 V3 是否已经有唯一、可校验、可追溯版本的格式语义权威来源？** 是。权威来源是 `instruction_analysis.json` 的 `format_requirements[]`；具备 Schema/Python 双重校验、不可覆盖版本链、语义 fingerprint 和版本 SHA。
2. **FR 文件被外部修改后，系统是否能在后续处理前可靠发现？** 是（对采用新 Receipt 的路径）。FR 文件 SHA-256 绑定进既有 `instruction_based_authorization.json`，Apply/Verify 的授权复核会重新计算并比对；不一致即阻断。不依赖 FR 文件内部 fingerprint 自证。
3. **Coverage 是否已改成引用 FR，而没有引入第二套格式语义或授权机制？** 是。`analysis_to_coverage()` 只写入 `format_requirement_ids`；Coverage Schema 仅增加引用字段；校验器拒绝 Coverage 复制 FR 语义。授权仍由既有 instruction-based authorization 负责，FR 只保存引用。
