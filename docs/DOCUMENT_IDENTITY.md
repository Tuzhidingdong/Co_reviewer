# Parser-backed DOCX identity and stable TargetRefs

## Build a first-round identity manifest

After the normal workspace inventory has been created:

```powershell
python scripts/co_reviewer_cli.py build-document-identity `
  --inventory workspace_inventory.json `
  --out document_identity_manifest.json
```

The command reads the actual source DOCX packages, verifies that each file SHA-256 matches the inventory, then cross-checks every inventory paragraph's part, p_idx, visible text and available paraId against the parsed DOCX before computing the five document identity dimensions. It registers Word paragraphs, tables, reviewer comments, headers/footers, footnotes and endnotes. A stale paragraph map is rejected rather than trusted.

## Carry object identity into the next revision round

Keep the previous manifest as a project artifact. After new source DOCX files have been inspected, pass it to the next identity build:

```powershell
python scripts/co_reviewer_cli.py build-document-identity `
  --inventory workspace_inventory_r2.json `
  --previous-manifest document_identity_manifest.json `
  --out document_identity_manifest_r2.json
```

Matching policy is deliberately conservative:

1. A unique Word `w14:paraId` within the same document part and object type preserves the object ID even if text or position changes.
2. When no unique native ID exists, a unique exact object fingerprint may preserve the ID across moves and formatting-independent changes.
3. Duplicate or ambiguous candidates receive new IDs; no paragraph-index guess is used to claim continuity.

The match report reports native-ID matches, exact-fingerprint matches, new objects and ambiguities. Paragraph indices remain advisory location hints. Workflow Ledger validation also requires each DocumentIdentity source hash to match the source_files hash for the same role.

## Build the Workflow Ledger with stable targets

Supply the matching manifest when validating/promoting the decision plan:

```powershell
python scripts/validate_decision_plan.py `
  --plan revision_decision_plan.json `
  --audit review_audit_report.json `
  --inventory workspace_inventory.json `
  --identity-manifest document_identity_manifest.json `
  --workflow-ledger-out workflow_ledger.json
```

The validator binds manifest source hashes to the decision plan, resolves each mutating target by its current part/paragraph hint and (when supplied) native paraId, then persists document identities, object registry and TargetRefs. The Workflow Engine schedules by `document_id|object_id`; `p_idx` is retained only as a hint. Without `--identity-manifest`, the legacy p_idx-based compatibility path remains available.

## Scope and limits

- The parser reads DOCX WordprocessingML parts and does not infer object continuity from filenames.
- Exact-fingerprint matching is only used when the candidate is unique on both sides.
- When Word does not preserve `paraId` and content changes, the object is treated as new. This is safer than incorrectly reusing an identity.
- Citation-profile classification is rule-based and records its evidence fingerprint. It deliberately leaves numbering scope as unknown unless later evidence proves a scope; it is a hint for review, not a final citation-style judgement.
- Table-cell paragraphs are registered under their parent table object. Their row/cell positions are advisory hints, not permanent keys.
- This first registry version supports one current DOCX per logical role (Reviewers, Manuscript, SI). Multiple SI files need explicit logical-document IDs before combining them into one role.
