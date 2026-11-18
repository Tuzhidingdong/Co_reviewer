[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReviewersDocx,

    [Parameter(Mandatory = $true)]
    [string]$ManuscriptDocx,

    [Parameter(Mandatory = $false)]
    [string]$SupportingInformationDocx,

    [Parameter(Mandatory = $false)]
    [string]$WorkspaceParent = (Join-Path $env:LOCALAPPDATA "Co_Reviewer\Rounds"),

    [Parameter(Mandatory = $false)]
    [string]$PythonExe = "python.exe"
)

$ErrorActionPreference = "Stop"

function Resolve-InputFile {
    param([string]$Path, [string]$Label)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    if ([System.IO.Path]::GetExtension($resolved).ToLowerInvariant() -ne ".docx") {
        throw "$Label must be a .docx file: $resolved"
    }
    return $resolved
}

function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

$reviewersSource = Resolve-InputFile -Path $ReviewersDocx -Label "Reviewers"
$manuscriptSource = Resolve-InputFile -Path $ManuscriptDocx -Label "Manuscript"
$siSource = Resolve-InputFile -Path $SupportingInformationDocx -Label "Supporting Information"

$sourceSet = @($reviewersSource, $manuscriptSource)
if ($siSource) { $sourceSet += $siSource }
if ($sourceSet | Group-Object { $_.ToLowerInvariant() } | Where-Object { $_.Count -gt 1 }) {
    throw "Reviewers, Manuscript and SI must be three distinct input files."
}

if (-not ($PythonExe -match '[\\/]' -and (Test-Path -LiteralPath $PythonExe -PathType Leaf))) {
    $pythonCommand = Get-Command $PythonExe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python executable was not found: $PythonExe"
    }
    $pythonResolved = $pythonCommand.Source
}
else {
    $pythonResolved = (Resolve-Path -LiteralPath $PythonExe).Path
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workspaceRoot = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($WorkspaceParent))
if (-not (Test-Path -LiteralPath $workspaceRoot)) {
    New-Item -ItemType Directory -Path $workspaceRoot -Force | Out-Null
}

$roundId = "round-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$workspace = Join-Path $workspaceRoot $roundId
if (Test-Path -LiteralPath $workspace) {
    throw "Refusing to reuse an existing review workspace: $workspace"
}
New-Item -ItemType Directory -Path $workspace | Out-Null

$inputDir = Join-Path $workspace "input"
$planDir = Join-Path $workspace "plan"
$reportsDir = Join-Path $workspace "reports"
$outputDir = Join-Path $workspace "output"
$runsDir = Join-Path $workspace "runs"
$cacheDir = Join-Path $workspace ".co-reviewer-cache"
foreach ($directory in @($inputDir, $planDir, $reportsDir, $outputDir, $runsDir, $cacheDir)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$reviewersCopy = Join-Path $inputDir "Reviewers.docx"
$manuscriptCopy = Join-Path $inputDir "Manuscript.docx"
Copy-Item -LiteralPath $reviewersSource -Destination $reviewersCopy
Copy-Item -LiteralPath $manuscriptSource -Destination $manuscriptCopy
$reviewersOriginalSha = Get-Sha256 $reviewersSource
$reviewersCopySha = Get-Sha256 $reviewersCopy
$manuscriptOriginalSha = Get-Sha256 $manuscriptSource
$manuscriptCopySha = Get-Sha256 $manuscriptCopy
if ($reviewersOriginalSha -ne $reviewersCopySha -or $manuscriptOriginalSha -ne $manuscriptCopySha) {
    throw "A copied source DOCX does not match its original SHA-256. Preserve this round workspace: $workspace"
}

$siCopy = $null
$siOriginalSha = $null
$siCopySha = $null
if ($siSource) {
    $siCopy = Join-Path $inputDir "SI.docx"
    Copy-Item -LiteralPath $siSource -Destination $siCopy
    $siOriginalSha = Get-Sha256 $siSource
    $siCopySha = Get-Sha256 $siCopy
    if ($siOriginalSha -ne $siCopySha) {
        throw "The copied SI file does not match its original SHA-256. Preserve this round workspace: $workspace"
    }
}

$inventoryPath = Join-Path $reportsDir "workspace_inventory.json"
$digestPath = Join-Path $reportsDir "workspace_digest.md"
$auditJsonPath = Join-Path $reportsDir "review_audit_report.json"
$auditMarkdownPath = Join-Path $reportsDir "review_audit_report.md"
$decisionQueuePath = Join-Path $reportsDir "review_decision_queue.json"

$auditArgs = @(
    (Join-Path $repoRoot "scripts/co_reviewer_cli.py"),
    "audit",
    "--rev", $reviewersCopy,
    "--man", $manuscriptCopy,
    "--out", $digestPath,
    "--json-out", $inventoryPath,
    "--audit-md-out", $auditMarkdownPath,
    "--audit-out", $auditJsonPath,
    "--decision-queue-out", $decisionQueuePath,
    "--cache-dir", $cacheDir
)
if ($siCopy) {
    $auditArgs += @("--si", $siCopy)
}

Write-Host "[WORKSPACE] $workspace"
Write-Host "[AUDIT] Running one inventory/audit pass; original source files are not modified."
& $pythonResolved @auditArgs
if ($LASTEXITCODE -ne 0) {
    throw "Co_Reviewer audit failed with exit code $LASTEXITCODE. Preserve this workspace for diagnosis: $workspace"
}

$revisionBaselineRows = @()
$baselineInputs = @(
    [pscustomobject]@{ role = "Reviewers"; file = $reviewersCopy },
    [pscustomobject]@{ role = "Manuscript"; file = $manuscriptCopy }
)
if ($siCopy) { $baselineInputs += [pscustomobject]@{ role = "SI"; file = $siCopy } }
foreach ($baselineInput in $baselineInputs) {
    $baselinePath = Join-Path $reportsDir ($baselineInput.role + "_revision_baseline.json")
    $baselineArgs = @((Join-Path $repoRoot "scripts/co_reviewer_cli.py"), "revision-baseline", "--docx", $baselineInput.file, "--out", $baselinePath)
    Write-Host "[REVISION BASELINE] Inventorying $($baselineInput.role) without model calls."
    & $pythonResolved @baselineArgs
    if ($LASTEXITCODE -ne 0) { throw "Historical revision baseline failed for $($baselineInput.role). Preserve this workspace: $workspace" }
    $baselineReport = Get-Content -LiteralPath $baselinePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $revisionBaselineRows += [pscustomobject]@{
        role = $baselineInput.role
        source_file = $baselineInput.file
        source_sha256 = $baselineReport.source_sha256
        existing_revision_count = $baselineReport.existing_revision_count
        report = $baselinePath
        report_sha256 = (Get-Sha256 $baselinePath)
    }
}

$sourceRows = @(
    [pscustomobject]@{
        role = "Reviewers"
        original_file = $reviewersSource
        original_sha256 = $reviewersOriginalSha
        file = $reviewersCopy
        sha256 = $reviewersCopySha
    },
    [pscustomobject]@{
        role = "Manuscript"
        original_file = $manuscriptSource
        original_sha256 = $manuscriptOriginalSha
        file = $manuscriptCopy
        sha256 = $manuscriptCopySha
    }
)
if ($siCopy) {
    $sourceRows += [pscustomobject]@{
        role = "SI"
        original_file = $siSource
        original_sha256 = $siOriginalSha
        file = $siCopy
        sha256 = $siCopySha
    }
}

$manifest = [ordered]@{
    schema_version = "1.0"
    review_round_id = $roundId
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    workspace = $workspace
    directories = [ordered]@{
        input = $inputDir
        plan = $planDir
        reports = $reportsDir
        output = $outputDir
        runs = $runsDir
        cache = $cacheDir
    }
    source_files = $sourceRows
    artifacts = [ordered]@{
        workspace_inventory = $inventoryPath
        workspace_digest = $digestPath
        review_audit_report = $auditJsonPath
        review_audit_markdown = $auditMarkdownPath
        decision_queue = $decisionQueuePath
        revision_baselines = $revisionBaselineRows
    }
    artifact_sha256 = [ordered]@{
        workspace_inventory = (Get-Sha256 $inventoryPath)
        workspace_digest = (Get-Sha256 $digestPath)
        review_audit_report = (Get-Sha256 $auditJsonPath)
        review_audit_markdown = (Get-Sha256 $auditMarkdownPath)
        decision_queue = (Get-Sha256 $decisionQueuePath)
        revision_baselines = @($revisionBaselineRows | ForEach-Object { [pscustomobject]@{ role = $_.role; sha256 = $_.report_sha256 } })
    }
    revision_baselines = $revisionBaselineRows
    workflow_ledger = (Join-Path $planDir "workflow_ledger.json")
    execution_scope = (Join-Path $planDir "workflow_execution_scope.json")
    prepared_workflow_ledger = (Join-Path $planDir "workflow_ledger.prepared.json")
    revision_ledger = (Join-Path $planDir "revision_ledger.json")
}

$manifestPath = Join-Path $workspace "round_manifest.json"
$json = $manifest | ConvertTo-Json -Depth 10
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($manifestPath, $json + [Environment]::NewLine, $utf8NoBom)

Write-Host ""
Write-Host "[ROUND READY] $roundId"
Write-Host "[WORKSPACE] $workspace"
Write-Host "[INVENTORY] $inventoryPath"
Write-Host "[DECISION QUEUE] $decisionQueuePath"
Write-Host "[AUDIT REPORT] $auditJsonPath"
Write-Host "[REVISION BASELINES] $($revisionBaselineRows.Count) role(s); reports in $reportsDir"
Write-Host "[ROUND MANIFEST] $manifestPath"
Write-Host "[NEXT] Review the complete decision queue and create an author-approved revision_decision_plan.json under $planDir."
