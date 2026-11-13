[CmdletBinding()]
param(
    [switch]$InstallDependencies,
    [switch]$InstallPdfDependencies,
    [string]$LogDirectory = "artifacts/quality-checks"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogRoot = Join-Path $RepoRoot $LogDirectory
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$RunStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$LogPath = Join-Path $LogRoot "quality-checks-$RunStamp-$PID.log"
$script:PassedChecks = 0

Push-Location $RepoRoot

function Invoke-QualityCheck {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$PythonArgs
    )

    $SafeName = $Name -replace '[^A-Za-z0-9_.-]', '_'
    $StepLogPath = Join-Path $LogRoot "step-$SafeName-$RunStamp-$PID.log"

    & python @PythonArgs *> $StepLogPath
    $ExitCode = $LASTEXITCODE

    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ([Environment]::NewLine + "===== $Name (exit $ExitCode) =====")
    if (Test-Path -LiteralPath $StepLogPath) {
        Get-Content -LiteralPath $StepLogPath | Add-Content -LiteralPath $LogPath -Encoding UTF8
    }

    if ($ExitCode -ne 0) {
        Write-Host "[FAIL] $Name (exit $ExitCode)" -ForegroundColor Red
        if (Test-Path -LiteralPath $StepLogPath) {
            Get-Content -LiteralPath $StepLogPath -Tail 80 | ForEach-Object { Write-Host $_ }
        }
        throw "$Name failed. Full log: $LogPath"
    }

    Remove-Item -LiteralPath $StepLogPath -Force -ErrorAction SilentlyContinue
    $script:PassedChecks++
    Write-Host "[PASS] $Name" -ForegroundColor Green
}

try {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Python was not found on PATH. Activate the intended Python/Conda environment first."
    }

    $VersionText = (& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to query the active Python interpreter."
    }
    $VersionParts = $VersionText -split '\.'
    $Major = [int]$VersionParts[0]
    $Minor = [int]$VersionParts[1]
    if (($Major -lt 3) -or (($Major -eq 3) -and ($Minor -lt 10))) {
        throw "Co_Reviewer requires Python 3.10 or newer; active version is $VersionText."
    }
    Write-Host "Python $VersionText" -ForegroundColor Green

    if ($InstallPdfDependencies) {
        Invoke-QualityCheck -Name "Install project and PDF dependencies" -PythonArgs @("-m", "pip", "install", "-r", "requirements-pdf.txt")
    }
    elseif ($InstallDependencies) {
        Invoke-QualityCheck -Name "Install project dependencies" -PythonArgs @("-m", "pip", "install", "-r", "requirements.txt")
    }

    Invoke-QualityCheck -Name "Check required Python packages" -PythonArgs @("-c", "import lxml, docx; print('lxml and python-docx are available')")
    Invoke-QualityCheck -Name "Compile source and tests" -PythonArgs @("-m", "compileall", "-q", "scripts", "tests")
    Invoke-QualityCheck -Name "Run complete unit suite" -PythonArgs @("-m", "unittest", "discover", "-s", "tests", "-v")
    Invoke-QualityCheck -Name "Check revision CLI entry point" -PythonArgs @("scripts/co_reviewer_cli.py", "--help")
    Invoke-QualityCheck -Name "Check evidence CLI entry point" -PythonArgs @("scripts/evidence_tools.py", "--help")
    Invoke-QualityCheck -Name "Check PDF evidence command" -PythonArgs @("scripts/evidence_tools.py", "pdf-inspect", "--help")
    Invoke-QualityCheck -Name "Check structured evidence command" -PythonArgs @("scripts/evidence_tools.py", "structured-summary", "--help")
    Invoke-QualityCheck -Name "Check CrossRef evidence command" -PythonArgs @("scripts/evidence_tools.py", "crossref-index", "--help")
    Invoke-QualityCheck -Name "Check Response Letter DOCX command" -PythonArgs @("scripts/co_reviewer_cli.py", "render-response-docx", "--help")
    Invoke-QualityCheck -Name "Check revision status command" -PythonArgs @("scripts/co_reviewer_cli.py", "status", "--help")
    Invoke-QualityCheck -Name "Check approval command" -PythonArgs @("scripts/co_reviewer_cli.py", "approve-plan", "--help")
    Invoke-QualityCheck -Name "Check source preflight command" -PythonArgs @("scripts/co_reviewer_cli.py", "preflight-plan", "--help")
    Invoke-QualityCheck -Name "Check verified-state confirmation command" -PythonArgs @("scripts/co_reviewer_cli.py", "confirm-verified", "--help")
    Invoke-QualityCheck -Name "Check Word compatibility fixture generator" -PythonArgs @("scripts/create_word_compatibility_fixture.py", "--help")

    $TestSummary = Select-String -LiteralPath $LogPath -Pattern '^Ran \d+ tests? in ' | Select-Object -Last 1
    $SkipSummary = Select-String -LiteralPath $LogPath -Pattern '^OK \(skipped=\d+\)' | Select-Object -Last 1

    Write-Host ""
    Write-Host "[PASS] $script:PassedChecks quality checks completed." -ForegroundColor Green
    if ($TestSummary) {
        Write-Host "Tests: $($TestSummary.Line.Trim())"
    }
    if ($SkipSummary) {
        Write-Host "Optional tests: $($SkipSummary.Line.Trim())"
        Write-Host "Install PDF test/runtime dependency with: python -m pip install -r requirements-pdf.txt" -ForegroundColor Yellow
    }
    Write-Host "Full command output: $LogPath"
    Write-Host "Microsoft Word opening/Track Changes compatibility still requires a separate visual acceptance."
}
catch {
    Write-Host "Quality checks stopped. Full log: $LogPath" -ForegroundColor Red
    throw
}
finally {
    Pop-Location
}
