# V2.3.2 RC — Local Sync and Acceptance

This branch is an isolated engineering-candidate branch based on the frozen V2.3.2 plan. It is **not GA**, is not merged to main, and must not be used to overwrite a working tree with uncommitted changes.

## 1. Sync the RC branch on Windows

Open PowerShell in the local Co_Reviewer repository root. Check git status first. Preserve or commit any local changes before switching branches.

```powershell
git status --short --branch
git fetch origin

$branch = "upgrade/v0.5.1-p1-rc-closeout-20261120"
git show-ref --verify --quiet "refs/heads/$branch"
if ($LASTEXITCODE -eq 0) {
    git switch $branch
    if ($LASTEXITCODE -ne 0) { throw "Could not switch to the RC branch." }
    git pull --ff-only origin $branch
}
else {
    git switch --track -c $branch "origin/$branch"
}
if ($LASTEXITCODE -ne 0) { throw "Could not sync the RC branch." }

git status --short --branch
git log -1 --oneline --decorate
```

Expected branch: upgrade/v0.5.1-p1-rc-closeout-20261120

Do not merge PR #9 or switch to main for this acceptance pass.

## 2. Run the deterministic Windows quality suite

The project supports Python 3.10+. The existing local Anaconda interpreter can be used directly:

```powershell
$python = "D:\Software\Anacoda\python.exe"
& $python --version
& $python -m pip install -e ".[test]"
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

.\scripts\run_quality_checks.ps1 -PythonExe $python
if ($LASTEXITCODE -ne 0) { throw "Co_Reviewer quality checks failed. Preserve the full log path printed by the script." }
```

The runner compiles source/tests, executes the complete unittest suite, and smoke-checks CLI entry points. It writes a persistent log under the Windows temporary directory. Record the exact commit SHA and log path; a pass on a different commit is not evidence for this branch head.

## 3. Microsoft Word Track Changes round-trip

The synthetic Word compatibility fixture is independent of any real manuscript. Create it in a new, empty directory:

```powershell
$fixture = Join-Path $env:TEMP ("CoReviewer_WordCompatibility_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
& $python scripts/create_word_compatibility_fixture.py --output-dir $fixture
if ($LASTEXITCODE -ne 0) { throw "Could not create the isolated Word compatibility fixture." }
```

The fixture contains five synthetic targets: two body paragraphs (including subscript/superscript conversion), one table cell, one header and one footer. Follow the generated plan through the CLI normal preflight, explicit Patch approval, native Track Changes Apply and verification gates. Do not edit the Marked DOCX with python-docx or raw XML.

Open the generated Marked DOCX in Microsoft Word, confirm there is no repair warning, inspect the five insertions/deletions and script formatting, save, close and reopen it. Run the post-Word verification command from the repository Word compatibility workflow and retain its JSON report. Do not claim Word round-trip acceptance before the post-Word report passes.

## 4. Word COM / PDF mapping fixture

The Word/PDF mapping runner consumes compatibility artifacts after the Word save/close/reopen check. It requires Microsoft Word on Windows plus pywin32 and PyMuPDF:

```powershell
.\scripts\run_word_pdf_mapping_acceptance.ps1 -FixtureDirectory $fixture -PythonExe $python
```

Use a new fixture directory for every run. The runner refuses to overwrite existing outputs. A pass must produce the explicit [WORD/PDF ACCEPTANCE] PASS evidence from its verifier, matching Word/PDF pagination and passing Response Letter quality checks.

This is synthetic one-ResponseUnit Word/PDF acceptance; it is not the full AC-45 two-batch workflow and is not acceptance of a real manuscript.

## 5. User review-round acceptance

After deterministic and synthetic Word checks pass, create a separate real review round using user-selected Reviewer comments, Manuscript and optional SI. Keep source documents immutable. Review the Decision Plan, exact PatchOperations and proposed ResponseUnits before authorizing any task. Inspect Marked/Clean DOCX, final PDF and Response Letter yourself.

The Tier 1 gate now requires a separate high-risk review acknowledgement when authorizing scientific/numeric/reference/formatting/cross-document work. After a refresh/recheck, the previous Tier 1 acknowledgement is cleared and must be renewed.

## 6. Report back with evidence

Please retain these items for the final RC decision:

- RC branch HEAD commit SHA.
- run_quality_checks.ps1 exit code and full log path.
- Word compatibility post-save/reopen verification JSON and its status.
- Word COM/PDF acceptance output directory, mapping/quality reports and explicit PASS line.
- For a real review round: round root, final Release Package path, Manifest/receipt, unresolved/deferred list and your GUI acceptance decision.

M1 engineering readiness and M2 user acceptance are separate decisions. Developer/CI checks cannot sign off the user's Word GUI inspection or real manuscript workflow.
