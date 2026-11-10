[CmdletBinding()]
param(
    [switch]$InstallDependencies
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot

function Invoke-QualityCheck {
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

    if ($InstallDependencies) {
        Invoke-QualityCheck -Name "Install project dependencies" -PythonArgs @("-m", "pip", "install", "-r", "requirements.txt")
    }

    Invoke-QualityCheck -Name "Check required Python packages" -PythonArgs @("-c", "import lxml, docx; print('lxml and python-docx are available')")
    Invoke-QualityCheck -Name "Compile source and tests" -PythonArgs @("-m", "compileall", "-q", "scripts", "tests")
    Invoke-QualityCheck -Name "Run unit tests" -PythonArgs @("-m", "unittest", "discover", "-s", "tests", "-v")
    Invoke-QualityCheck -Name "Check CLI entry point" -PythonArgs @("scripts/co_reviewer_cli.py", "--help")
    Invoke-QualityCheck -Name "Check Response Letter DOCX command" -PythonArgs @("scripts/co_reviewer_cli.py", "render-response-docx", "--help")
    Invoke-QualityCheck -Name "Check revision status command" -PythonArgs @("scripts/co_reviewer_cli.py", "status", "--help")
    Invoke-QualityCheck -Name "Check approval command" -PythonArgs @("scripts/co_reviewer_cli.py", "approve-plan", "--help")
    Invoke-QualityCheck -Name "Check source preflight command" -PythonArgs @("scripts/co_reviewer_cli.py", "preflight-plan", "--help")

    Write-Host ""
    Write-Host "All automated Co_Reviewer checks passed." -ForegroundColor Green
    Write-Host "Microsoft Word opening/Track Changes compatibility still requires a separate visual acceptance."
}
finally {
    Pop-Location
}
