[CmdletBinding()]
param(
    [switch]$InstallDependencies,
    [string]$PythonExe = "python",
    [string]$LogPath = ""
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

if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $UniqueSuffix = [guid]::NewGuid().ToString("N").Substring(0, 8)
    $LogName = "Co_Reviewer_QualityChecks_{0}_{1}.log" -f $Timestamp, $UniqueSuffix
    $LogPath = Join-Path $env:TEMP $LogName
}
$LogPath = [System.IO.Path]::GetFullPath($LogPath)
$LogDirectory = Split-Path -Parent $LogPath
if (-not (Test-Path -LiteralPath $LogDirectory)) {
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
}
Set-Content -LiteralPath $LogPath -Value @(
    "Co_Reviewer quality checks"
    "Started: $([DateTimeOffset]::Now.ToString('o'))"
    "Repository: $RepoRoot"
    "Python: $PythonCommand"
) -Encoding UTF8

Write-Host "Persistent quality-check log: $LogPath" -ForegroundColor Cyan

$OriginalPythonPath = $env:PYTHONPATH
$ScriptsPath = Join-Path $RepoRoot "scripts"
if ([string]::IsNullOrWhiteSpace($OriginalPythonPath)) {
    $env:PYTHONPATH = $ScriptsPath
}
else {
    $env:PYTHONPATH = "$ScriptsPath$([IO.Path]::PathSeparator)$OriginalPythonPath"
}

$script:FailedCheck = $null
$script:FailedExitCode = $null

function Add-QualityLog {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Message
    )
    Add-Content -LiteralPath $LogPath -Value $Message -Encoding UTF8
}

function ConvertTo-WindowsCommandLineArgument {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    # ProcessStartInfo.ArgumentList is unavailable in Windows PowerShell 5.1
    # on .NET Framework. This fallback follows Windows CRT backslash/quote rules.
    $Builder = [System.Text.StringBuilder]::new()
    [void]$Builder.Append('"')
    $Backslashes = 0

    foreach ($Character in $Value.ToCharArray()) {
        if ($Character -eq [char]92) {
            $Backslashes++
            continue
        }
        if ($Character -eq [char]34) {
            if ($Backslashes -gt 0) {
                [void]$Builder.Append(('\' * (($Backslashes * 2) + 1)))
            }
            else {
                [void]$Builder.Append('\')
            }
            [void]$Builder.Append('"')
            $Backslashes = 0
            continue
        }
        if ($Backslashes -gt 0) {
            [void]$Builder.Append(('\' * $Backslashes))
            $Backslashes = 0
        }
        [void]$Builder.Append($Character)
    }

    if ($Backslashes -gt 0) {
        [void]$Builder.Append(('\' * ($Backslashes * 2)))
    }
    [void]$Builder.Append('"')
    return $Builder.ToString()
}

function Invoke-QualityCheck {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$PythonArgs
    )

    Write-Host ""
    Write-Host "== $Name ==" -ForegroundColor Cyan
    Add-QualityLog ""
    Add-QualityLog "===== $Name ====="
    Add-QualityLog "COMMAND: $PythonCommand $($PythonArgs -join ' ')"

    # Capture native stdout/stderr through .NET instead of PowerShell's
    # error stream. Python tests may intentionally write diagnostics to stderr
    # while the process still exits successfully.
    $ProcessInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $ProcessInfo.FileName = $PythonCommand
    $ProcessInfo.WorkingDirectory = $RepoRoot
    $ProcessInfo.UseShellExecute = $false
    # Redirected Python output must be UTF-8 on Windows PowerShell 5.1; the
    # system ANSI code page (often CP1252) cannot encode Unicode CLI help text.
    $ProcessInfo.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
    $ProcessInfo.CreateNoWindow = $true
    $ProcessInfo.RedirectStandardOutput = $true
    $ProcessInfo.RedirectStandardError = $true
    $ArgumentListProperty = $ProcessInfo.GetType().GetProperty("ArgumentList")
    if ($null -ne $ArgumentListProperty) {
        foreach ($Argument in $PythonArgs) {
            [void]$ProcessInfo.ArgumentList.Add([string]$Argument)
        }
    }
    else {
        $QuotedArguments = foreach ($Argument in $PythonArgs) {
            ConvertTo-WindowsCommandLineArgument -Value ([string]$Argument)
        }
        $ProcessInfo.Arguments = $QuotedArguments -join ' '
    }

    $Process = [System.Diagnostics.Process]::new()
    try {
        $Process.StartInfo = $ProcessInfo
        if (-not $Process.Start()) {
            throw "Unable to start Python process for quality check: $Name"
        }

        # Read both streams asynchronously before waiting to avoid pipe deadlocks.
        $StdoutTask = $Process.StandardOutput.ReadToEndAsync()
        $StderrTask = $Process.StandardError.ReadToEndAsync()
        $Process.WaitForExit()
        $StdoutText = $StdoutTask.GetAwaiter().GetResult()
        $StderrText = $StderrTask.GetAwaiter().GetResult()
        $CommandExitCode = $Process.ExitCode
    }
    finally {
        $Process.Dispose()
    }

    foreach ($Stream in @(
        [pscustomobject]@{ Name = "STDOUT"; Text = $StdoutText }
        [pscustomobject]@{ Name = "STDERR"; Text = $StderrText }
    )) {
        if (-not [string]::IsNullOrEmpty($Stream.Text)) {
            Add-QualityLog "--- $($Stream.Name) ---"
            $Lines = [regex]::Split($Stream.Text, "\r\n|\n|\r")
            for ($Index = 0; $Index -lt $Lines.Count; $Index++) {
                # Split() adds one trailing empty item when the stream ends in a newline.
                if (($Index -eq ($Lines.Count - 1)) -and ($Lines[$Index].Length -eq 0)) {
                    continue
                }
                Add-QualityLog $Lines[$Index]
                Write-Host $Lines[$Index]
            }
        }
    }

    Add-QualityLog "EXIT_CODE=$CommandExitCode"
    if ($CommandExitCode -ne 0) {
        $script:FailedCheck = $Name
        $script:FailedExitCode = $CommandExitCode
        throw "$Name failed with exit code $CommandExitCode. Full output: $LogPath"
    }
}

try {
    Add-QualityLog "PYTHONPATH=$env:PYTHONPATH"
    Add-QualityLog ""

    Invoke-QualityCheck -Name "Check Python interpreter version" -PythonArgs @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
    $VersionText = (& $PythonCommand -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to query the active Python interpreter. See $LogPath"
    }
    $VersionParts = $VersionText -split '\.'
    $Major = [int]$VersionParts[0]
    $Minor = [int]$VersionParts[1]
    if (($Major -lt 3) -or (($Major -eq 3) -and ($Minor -lt 10))) {
        throw "Co_Reviewer requires Python 3.10 or newer; active version is $VersionText."
    }
    Write-Host "Python $VersionText" -ForegroundColor Green
    Add-QualityLog "Validated Python version: $VersionText"

    if ($InstallDependencies) {
        Invoke-QualityCheck -Name "Install project and test dependencies" -PythonArgs @("-m", "pip", "install", "-e", ".[test]")
    }

    Invoke-QualityCheck -Name "Check required Python packages" -PythonArgs @("-c", "import lxml, docx, jsonschema; print('lxml, python-docx and jsonschema are available')")
    Invoke-QualityCheck -Name "Compile source and tests" -PythonArgs @("-m", "compileall", "-q", "scripts", "tests")
    Invoke-QualityCheck -Name "Run unit tests" -PythonArgs @("-m", "unittest", "discover", "-s", "tests", "-v")
    $CoreWorkflowCliSmoke = "import argparse, sys; sys.path.insert(0, 'scripts'); import co_reviewer_cli as cli; parser = cli.build_parser(); action = next(item for item in parser._actions if isinstance(item, argparse._SubParsersAction)); names = ('approve-workflow-tasks', 'build-revision-ledger', 'export-revision-plan', 'sync-workflow-ledger', 'create-working-clean-views', 'confirm-response-units', 'map-response-locations', 'complete-response-units', 'render-response', 'render-response-docx', 'revision-baseline', 'targeted-revision-gate', 'resolve-historical-revisions', 'preview-omml-docx', 'begin-qa-remediation', 'complete-qa-remediation'); missing = [name for name in names if name not in action.choices]; assert not missing, f'missing workflow CLI command(s): {missing}'; [action.choices[name].format_help() for name in names]; print(f'{len(names)} core workflow and historical-revision CLI commands are available')"
    Invoke-QualityCheck -Name "Check core Workflow Ledger CLI commands" -PythonArgs @("-c", $CoreWorkflowCliSmoke)
    Invoke-QualityCheck -Name "Check CLI entry point" -PythonArgs @("scripts/co_reviewer_cli.py", "--help")
    Invoke-QualityCheck -Name "Check cached review audit command" -PythonArgs @("scripts/co_reviewer_cli.py", "audit", "--help")
    Invoke-QualityCheck -Name "Check pending decision-plan validator" -PythonArgs @("scripts/validate_decision_plan.py", "--help")
    Invoke-QualityCheck -Name "Check cached inspection command" -PythonArgs @("scripts/co_reviewer_cli.py", "inspect", "--help")
    Invoke-QualityCheck -Name "Check historical revision baseline command" -PythonArgs @("scripts/co_reviewer_cli.py", "revision-baseline", "--help")
    Invoke-QualityCheck -Name "Check target-scoped historical revision gate" -PythonArgs @("scripts/co_reviewer_cli.py", "targeted-revision-gate", "--help")
    Invoke-QualityCheck -Name "Check historical revision resolver command" -PythonArgs @("scripts/co_reviewer_cli.py", "resolve-historical-revisions", "--help")
    Invoke-QualityCheck -Name "Check deterministic OMML preview command" -PythonArgs @("scripts/co_reviewer_cli.py", "preview-omml-docx", "--help")
    Invoke-QualityCheck -Name "Check one-round QA remediation entry command" -PythonArgs @("scripts/co_reviewer_cli.py", "begin-qa-remediation", "--help")
    Invoke-QualityCheck -Name "Check bounded QA remediation completion command" -PythonArgs @("scripts/co_reviewer_cli.py", "complete-qa-remediation", "--help")
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
    Add-QualityLog ""
    Add-QualityLog "RESULT=PASS"
}
catch {
    Add-QualityLog ""
    Add-QualityLog "RESULT=FAIL"
    Add-QualityLog "FAILED_CHECK=$($script:FailedCheck)"
    Add-QualityLog "FAILED_EXIT_CODE=$($script:FailedExitCode)"
    Add-QualityLog "EXCEPTION=$($_.Exception.ToString())"
    Write-Host "Quality checks failed. Full output is preserved at: $LogPath" -ForegroundColor Red
    throw
}
finally {
    Add-QualityLog "Finished: $([DateTimeOffset]::Now.ToString('o'))"
    $env:PYTHONPATH = $OriginalPythonPath
    Pop-Location
}
