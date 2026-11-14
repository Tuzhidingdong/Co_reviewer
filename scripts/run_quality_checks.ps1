[CmdletBinding()]
param(
    [switch]$InstallDependencies,
    [string]$PythonExe = "python"
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

$OriginalPythonPath = $env:PYTHONPATH
$ScriptsPath = Join-Path $RepoRoot "scripts"
if ([string]::IsNullOrWhiteSpace($OriginalPythonPath)) {
    $env:PYTHONPATH = $ScriptsPath
}
else {
    $env:PYTHONPATH = "$ScriptsPath$([IO.Path]::PathSeparator)$OriginalPythonPath"
}

function Invoke-QualityCheck {
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
    $VersionText = (& $PythonCommand -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
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

    if ($InstallDependencies) {
        Invoke-QualityCheck -Name "Install project dependencies" -PythonArgs @("-m", "pip", "install", "-r", "requirements.txt")
    }

    Invoke-QualityCheck -Name "Check required Python packages" -PythonArgs @("-c", "import lxml, docx; print('lxml and python-docx are available')")
    Invoke-QualityCheck -Name "Compile source and tests" -PythonArgs @("-m", "compileall", "-q", "scripts", "tests")
    Invoke-QualityCheck -Name "Run unit tests" -PythonArgs @("-m", "unittest", "discover", "-s", "tests", "-v")
    Invoke-QualityCheck -Name "Check CLI entry point" -PythonArgs @("scripts/co_reviewer_cli.py", "--help")
    Invoke-QualityCheck -Name "Check cached review audit command" -PythonArgs @("scripts/co_reviewer_cli.py", "audit", "--help")
    Invoke-QualityCheck -Name "Check pending decision-plan validator" -PythonArgs @("scripts/validate_decision_plan.py", "--help")
    Invoke-QualityCheck -Name "Check cached inspection command" -PythonArgs @("scripts/co_reviewer_cli.py", "inspect", "--help")
    Invoke-QualityCheck -Name "Check Response Letter DOCX command" -PythonArgs @("scripts/co_reviewer_cli.py", "render-response-docx", "--help")
    Invoke-QualityCheck -Name "Check usage audit command" -PythonArgs @("scripts/co_reviewer_cli.py", "audit-usage", "--help")
    Invoke-QualityCheck -Name "Check usage event command" -PythonArgs @("scripts/co_reviewer_cli.py", "record-usage-event", "--help")
    Invoke-QualityCheck -Name "Check usage estimation command" -PythonArgs @("scripts/co_reviewer_cli.py", "estimate-usage", "--help")
    Invoke-QualityCheck -Name "Check DOCX quality audit command" -PythonArgs @("scripts/co_reviewer_cli.py", "audit-docx-quality", "--help")
    Invoke-QualityCheck -Name "Check revision status command" -PythonArgs @("scripts/co_reviewer_cli.py", "status", "--help")
    Invoke-QualityCheck -Name "Check approval command" -PythonArgs @("scripts/co_reviewer_cli.py", "approve-plan", "--help")
    Invoke-QualityCheck -Name "Check source preflight command" -PythonArgs @("scripts/co_reviewer_cli.py", "preflight-plan", "--help")
    Invoke-QualityCheck -Name "Check verified-state confirmation command" -PythonArgs @("scripts/co_reviewer_cli.py", "confirm-verified", "--help")
    Invoke-QualityCheck -Name "Check Word compatibility fixture generator" -PythonArgs @("scripts/create_word_compatibility_fixture.py", "--help")

    Write-Host ""
    Write-Host "All automated Co_Reviewer checks passed." -ForegroundColor Green
    Write-Host "Microsoft Word opening/Track Changes compatibility still requires a separate visual acceptance."
}
finally {
    $env:PYTHONPATH = $OriginalPythonPath
    Pop-Location
}
