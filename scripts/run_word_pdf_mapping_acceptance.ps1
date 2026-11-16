[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$FixtureDirectory,
    [string]$OutputDirectory = "",
    [string]$PythonExe = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot
$PythonCommand = $null

function Invoke-CoReviewerPython {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$PythonArgs
    )
    Write-Host ""
    Write-Host "== $Name ==" -ForegroundColor Cyan
    & $PythonCommand @PythonArgs
    $CommandExitCode = $LASTEXITCODE
    if ($CommandExitCode -ne 0) {
        throw "$Name failed with exit code $CommandExitCode."
    }
}

try {
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
        throw "Python executable was not found: $PythonExe"
    }

    $FixturePath = [System.IO.Path]::GetFullPath($FixtureDirectory)
    if (-not (Test-Path -LiteralPath $FixturePath -PathType Container)) {
        throw "Word compatibility fixture directory does not exist: $FixturePath"
    }

    if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
        $OutputDirectory = Join-Path $FixturePath "word_pdf_mapping_acceptance"
    }
    $OutputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
    if (Test-Path -LiteralPath $OutputPath) {
        if (-not (Test-Path -LiteralPath $OutputPath -PathType Container)) {
            throw "Acceptance output path exists and is not a directory: $OutputPath"
        }
        $Existing = @(Get-ChildItem -LiteralPath $OutputPath -Force)
        if ($Existing.Count -gt 0) {
            throw "Acceptance output directory is not empty; refusing to overwrite: $OutputPath"
        }
    }
    else {
        New-Item -ItemType Directory -Path $OutputPath | Out-Null
    }

    $SourceDocx = Join-Path $FixturePath "word_compatibility_input.docx"
    $MarkedDocx = Join-Path $FixturePath "word_compatibility_revised.docx"
    $PostWordReport = Join-Path $FixturePath "word_compatibility_verification_after_word.json"
    $WorkingCleanDocx = Join-Path $FixturePath "word_compatibility_revised.working_clean.docx"
    foreach ($RequiredFile in @($SourceDocx, $MarkedDocx, $PostWordReport)) {
        if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
            throw "Required post-Word compatibility artifact is missing: $RequiredFile"
        }
    }
    if (Test-Path -LiteralPath $WorkingCleanDocx) {
        throw "A Working Clean file already exists; refusing to overwrite: $WorkingCleanDocx"
    }

    Write-Host "Python interpreter: $PythonCommand" -ForegroundColor Cyan
    Invoke-CoReviewerPython -Name "Check Word COM and PDF dependencies" -PythonArgs @(
        "-c", "import pythoncom, win32com.client, fitz; print('pywin32 and PyMuPDF are available')"
    )

    $LedgerPath = Join-Path $OutputPath "word_pdf_acceptance_revision_ledger.json"
    $MappingDirectory = Join-Path $OutputPath "word_pdf_final"
    $ResponseMarkdown = Join-Path $OutputPath "Response_to_Reviewers.md"
    $ResponseDocx = Join-Path $OutputPath "Response_to_Reviewers.docx"
    $ResponseQuality = Join-Path $OutputPath "ResponseLetter_quality_report.json"

    Invoke-CoReviewerPython -Name "Create synthetic hash-bound mapping RevisionLedger" -PythonArgs @(
        "scripts/create_word_pdf_mapping_fixture.py",
        "--fixture-dir", $FixturePath,
        "--ledger-out", $LedgerPath
    )

    Invoke-CoReviewerPython -Name "Create accepted-changes Working Clean View" -PythonArgs @(
        "scripts/co_reviewer_cli.py",
        "create-working-clean-views",
        "--ledger", $LedgerPath,
        "--suffix", ".working_clean",
        "--actor", "Synthetic Word/PDF Acceptance"
    )

    Invoke-CoReviewerPython -Name "Run Microsoft Word COM pagination and PDF corroboration" -PythonArgs @(
        "scripts/co_reviewer_cli.py",
        "map-response-locations",
        "--ledger", $LedgerPath,
        "--out-dir", $MappingDirectory,
        "--actor", "Synthetic Word/PDF Acceptance"
    )

    Invoke-CoReviewerPython -Name "Render location-aware Response Letter Markdown" -PythonArgs @(
        "scripts/co_reviewer_cli.py",
        "render-response",
        "--revision-ledger", $LedgerPath,
        "--out", $ResponseMarkdown
    )

    Invoke-CoReviewerPython -Name "Render location-aware Response Letter DOCX" -PythonArgs @(
        "scripts/co_reviewer_cli.py",
        "render-response-docx",
        "--revision-ledger", $LedgerPath,
        "--out", $ResponseDocx,
        "--manuscript-title", "Synthetic Word/PDF Acceptance Fixture",
        "--author", "Co_Reviewer Test Fixture",
        "--quality-report", $ResponseQuality
    )

    Invoke-CoReviewerPython -Name "Verify Word COM/PDF/Response Letter evidence" -PythonArgs @(
        "scripts/verify_word_pdf_mapping_acceptance.py",
        "--ledger", $LedgerPath,
        "--mapping-dir", $MappingDirectory,
        "--response-markdown", $ResponseMarkdown,
        "--response-docx", $ResponseDocx,
        "--response-quality", $ResponseQuality
    )

    Write-Host ""
    Write-Host "Synthetic Word COM/PDF acceptance completed." -ForegroundColor Green
    Write-Host "RevisionLedger: $LedgerPath"
    Write-Host "Final Word/PDF directory: $MappingDirectory"
    Write-Host "Response Letter Markdown: $ResponseMarkdown"
    Write-Host "Response Letter DOCX: $ResponseDocx"
    Write-Host "Response Letter quality report: $ResponseQuality"
    Write-Host "This fixture is not a real manuscript acceptance or journal-ready release."
}
finally {
    Pop-Location
}
