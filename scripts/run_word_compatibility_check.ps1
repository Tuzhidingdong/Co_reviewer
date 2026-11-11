[CmdletBinding()]
param(
    [string]$OutputDirectory = "artifacts\word-compatibility-check"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot

function Invoke-CoReviewerPython {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$PythonArgs
    )

    Write-Host ""
    Write-Host "== $Name ==" -ForegroundColor Cyan
    & python @PythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE."
    }
}

try {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Python was not found on PATH. Activate the intended Python/Conda environment first."
    }

    $OutputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
    $FixtureGenerator = "scripts/create_word_compatibility_fixture.py"
    $Cli = "scripts/co_reviewer_cli.py"

    Invoke-CoReviewerPython -Name "Create isolated Word compatibility fixture" -PythonArgs @(
        $FixtureGenerator, "--output-dir", $OutputPath
    )

    $Plan = Join-Path $OutputPath "word_compatibility_plan.json"
    $Preflight = Join-Path $OutputPath "word_compatibility_preflight.json"
    $OutputDocx = Join-Path $OutputPath "word_compatibility_revised.docx"
    $ApplyReport = Join-Path $OutputPath "word_compatibility_apply_report.json"
    $Verification = Join-Path $OutputPath "word_compatibility_verification_before_word.json"

    Invoke-CoReviewerPython -Name "Preflight synthetic DOCX targets" -PythonArgs @(
        $Cli, "preflight-plan", "--plan", $Plan, "--out", $Preflight
    )
    Invoke-CoReviewerPython -Name "Approve synthetic acceptance item" -PythonArgs @(
        $Cli, "approve-plan", "--plan", $Plan, "--ids", "WORD-QA-01"
    )
    Invoke-CoReviewerPython -Name "Apply native Track Changes to five locations" -PythonArgs @(
        $Cli, "apply", "--plan", $Plan, "--author", "Co_Reviewer Word QA", "--report", $ApplyReport
    )
    Invoke-CoReviewerPython -Name "Verify exact Track Changes before opening Word" -PythonArgs @(
        $Cli, "verify", "--plan", $Plan, "--apply-report", $ApplyReport, "--out", $Verification
    )

    Write-Host ""
    Write-Host "Word compatibility fixture is ready." -ForegroundColor Green
    Write-Host "Open this file in Microsoft Word:"
    Write-Host "  $OutputDocx"
    Write-Host ""
    Write-Host "In Word, select Review > Tracking > All Markup and inspect both body edits, the formula, table cell, header and footer."
    Write-Host "For a consistent red display, set Review > Tracking dialog > Advanced Options > Insertions > Color to Red."
    Write-Host "Confirm that the formula uses true subscript/superscript formatting (not Unicode glyphs on the baseline)."
    Write-Host "Save the same DOCX, close Word, and reopen it. Do not accept or reject the revisions."
    Write-Host "Then rerun the CLI verify command against the saved DOCX; use the command shown in the repository guide."
}
finally {
    Pop-Location
}
