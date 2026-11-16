[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RoundRoot,

    [Parameter(Mandatory = $true)]
    [string]$WorkflowLedger,

    [Parameter(Mandatory = $false)]
    [string]$BatchLabel,

    [Parameter(Mandatory = $false)]
    [string]$PythonExe = "python.exe"
)

$ErrorActionPreference = "Stop"

function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-ActiveDocument {
    param(
        [object]$ActiveDocuments,
        [string]$Role,
        [bool]$Required
    )

    $property = $ActiveDocuments.PSObject.Properties[$Role]
    if (-not $property) {
        if ($Required) {
            throw "Workflow Ledger has no active document pointer for required role '$Role'."
        }
        return $null
    }

    $pointer = $property.Value
    if (-not $pointer -or [string]::IsNullOrWhiteSpace([string]$pointer.file)) {
        throw "Active document pointer '$Role' has no file path."
    }

    $path = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables([string]$pointer.file))
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Active document file does not exist for '$Role': $path"
    }

    $actualSha = Get-Sha256 $path
    if ($actualSha -ne [string]$pointer.sha256) {
        throw "Active document SHA-256 mismatch for '$Role'. Ledger=$($pointer.sha256); actual=$actualSha; file=$path"
    }

    return [pscustomobject]@{
        role = $Role
        file = $path
        sha256 = $actualSha
        batch_id = $pointer.batch_id
    }
}

$roundPath = (Resolve-Path -LiteralPath $RoundRoot -ErrorAction Stop).Path
$workflowPath = (Resolve-Path -LiteralPath $WorkflowLedger -ErrorAction Stop).Path
$workflow = Get-Content -LiteralPath $workflowPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace([string]$workflow.revision_round_id)) {
    throw "Workflow Ledger has no revision_round_id."
}
if (-not $workflow.active_documents) {
    throw "Workflow Ledger has no active_documents map."
}

$reviewers = Get-ActiveDocument -ActiveDocuments $workflow.active_documents -Role "Reviewers" -Required $true
$manuscript = Get-ActiveDocument -ActiveDocuments $workflow.active_documents -Role "Manuscript" -Required $true
$si = Get-ActiveDocument -ActiveDocuments $workflow.active_documents -Role "SI" -Required $false

if ([string]::IsNullOrWhiteSpace($BatchLabel)) {
    $BatchLabel = Get-Date -Format "yyyyMMdd-HHmmss"
}
$label = [regex]::Replace($BatchLabel, "[^A-Za-z0-9._-]+", "_").Trim("._-")
if ([string]::IsNullOrWhiteSpace($label)) {
    throw "BatchLabel must contain at least one safe filename character."
}

$reportsRoot = Join-Path $roundPath "reports"
if (-not (Test-Path -LiteralPath $reportsRoot)) {
    New-Item -ItemType Directory -Path $reportsRoot | Out-Null
}
$iterationDir = Join-Path $reportsRoot ("after-batch-" + $label)
if (Test-Path -LiteralPath $iterationDir) {
    throw "Refusing to overwrite an existing refreshed Audit directory: $iterationDir"
}
New-Item -ItemType Directory -Path $iterationDir | Out-Null

$cacheDir = Join-Path $roundPath ".co-reviewer-cache"
New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null

if ($PythonExe -match '[\\/]' -and (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    $pythonResolved = (Resolve-Path -LiteralPath $PythonExe).Path
}
else {
    $pythonCommand = Get-Command $PythonExe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python executable was not found: $PythonExe"
    }
    $pythonResolved = $pythonCommand.Source
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$cliPath = Join-Path $repoRoot "scripts/co_reviewer_cli.py"
$inventoryPath = Join-Path $iterationDir "workspace_inventory.json"
$digestPath = Join-Path $iterationDir "workspace_digest.md"
$auditJsonPath = Join-Path $iterationDir "review_audit_report.json"
$auditMarkdownPath = Join-Path $iterationDir "review_audit_report.md"
$decisionQueuePath = Join-Path $iterationDir "review_decision_queue.json"

$auditArgs = @(
    $cliPath,
    "audit",
    "--rev", $reviewers.file,
    "--man", $manuscript.file,
    "--out", $digestPath,
    "--json-out", $inventoryPath,
    "--audit-md-out", $auditMarkdownPath,
    "--audit-out", $auditJsonPath,
    "--decision-queue-out", $decisionQueuePath,
    "--cache-dir", $cacheDir
)
if ($si) {
    $auditArgs += @("--si", $si.file)
}

Write-Host "[REFRESH AUDIT] Round=$($workflow.revision_round_id)"
Write-Host "[WORKFLOW LEDGER] $workflowPath"
Write-Host "[ACTIVE MANUSCRIPT] $($manuscript.file) | $($manuscript.sha256)"
Write-Host "[ACTIVE SI] $(if ($si) { $si.file } else { 'not configured' })"
Write-Host "[OUTPUT] $iterationDir"

& $pythonResolved @auditArgs
if ($LASTEXITCODE -ne 0) {
    throw "Refreshed Co_Reviewer Audit failed with exit code $LASTEXITCODE. Preserve this directory: $iterationDir"
}

$sourceRows = @($reviewers, $manuscript)
if ($si) {
    $sourceRows += $si
}
$manifest = [ordered]@{
    schema_version = "1.0"
    review_round_id = $workflow.revision_round_id
    batch_label = $label
    created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    workflow_ledger = $workflowPath
    workflow_ledger_sha256 = Get-Sha256 $workflowPath
    active_documents = $sourceRows
    audit_artifacts = [ordered]@{
        workspace_inventory = $inventoryPath
        workspace_digest = $digestPath
        review_audit_report = $auditJsonPath
        review_audit_markdown = $auditMarkdownPath
        decision_queue = $decisionQueuePath
    }
}
$manifestPath = Join-Path $iterationDir "refresh_manifest.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $manifestPath,
    (($manifest | ConvertTo-Json -Depth 10) + [Environment]::NewLine),
    $utf8NoBom
)

Write-Host ""
Write-Host "[REFRESH READY] $iterationDir"
Write-Host "[INVENTORY] $inventoryPath"
Write-Host "[DECISION QUEUE] $decisionQueuePath"
Write-Host "[MANIFEST] $manifestPath"
Write-Host "[NEXT] Prepare refreshed evidence/targets in workflow_execution_scope.json and run prepare-workflow-execution against the current Workflow Ledger."
