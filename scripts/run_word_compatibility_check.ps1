[CmdletBinding()]
param(
    [string]$OutputDirectory = "artifacts\word-compatibility-check",
    [string]$PythonExe = "python",
    [switch]$VerifyAfterWord
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot

$PythonCommand = $null
if (Test-Path -LiteralPath $PythonExe -PathType Leaf) {
    $PythonCommand = (Resolve-Path -LiteralPath $PythonExe).Path
}
else {
    $PythonApp = Get-Command $PythonExe -CommandType Application -ErrorAction SilentlyContinue
    if ($PythonApp) {
        $PythonCommand = $PythonApp.Source
    }
}
if (-not $PythonCommand) {
    Pop-Location
    throw "Python executable was not found: $PythonExe"
}

function Invoke-CoReviewerPython {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$PythonArgs
    )

    Write-Host ""
    Write-Host "== $Name ==" -ForegroundColor Cyan
    & $PythonCommand @PythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE."
    }
}

try {
    $OutputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
    $FixtureGenerator = "scripts/create_word_compatibility_fixture.py"
    $Cli = "scripts/co_reviewer_cli.py"
    $Plan = Join-Path $OutputPath "word_compatibility_plan.json"
    $ApplyReport = Join-Path $OutputPath "word_compatibility_apply_report.json"
    $WordVerification = Join-Path $OutputPath "word_compatibility_verification_after_word.json"

    Write-Host "Python interpreter: $PythonCommand" -ForegroundColor Cyan

    if ($VerifyAfterWord) {
        foreach ($RequiredPath in @(
            (Join-Path $OutputPath "word_compatibility_revised.docx"),
            $Plan,
            $ApplyReport
        )) {
            if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
                throw "Cannot verify the Word round-trip because an acceptance artifact is missing: $RequiredPath"
            }
        }
        Invoke-CoReviewerPython -Name "Verify native Track Changes after Word save/reopen" -PythonArgs @(
            $Cli, "verify",
            "--plan", $Plan,
            "--apply-report", $ApplyReport,
            "--out", $WordVerification
        )
        Write-Host ""
        Write-Host "Post-Word verification report: $WordVerification" -ForegroundColor Green
        return
    }

    Invoke-CoReviewerPython -Name "Create isolated Word compatibility fixture" -PythonArgs @(
        $FixtureGenerator, "--output-dir", $OutputPath
    )

    $Preflight = Join-Path $OutputPath "word_compatibility_preflight.json"
    $OutputDocx = Join-Path $OutputPath "word_compatibility_revised.docx"
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
    Write-Host "Confirm that every text change appears as a native deletion/insertion revision, not a direct text rewrite."
    Write-Host "Confirm that the formula uses true subscript/superscript formatting (not Unicode glyphs on the baseline)."
    Write-Host "Save the same DOCX, close Word, and reopen it. Do not accept or reject the revisions."
    Write-Host "After saving, closing and reopening the DOCX, rerun this script with -VerifyAfterWord and the same -OutputDirectory."
}
finally {
    Pop-Location
}
