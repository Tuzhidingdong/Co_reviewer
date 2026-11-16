[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RoundRoot,
    [Parameter(Mandatory = $true)][string]$WorkflowLedger,
    [string]$ReviewerMap,
    [string]$ExecutionScope,
    [string]$Inventory,
    [string]$PreparedWorkflowLedgerOut,
    [string]$ReviewerMapOut,
    [string]$PreparationReportOut,
    [Parameter(Mandatory = $true)][string[]]$TaskIds,
    [string]$RevisionLedger,
    [string]$RunsDirectory,
    [string]$PythonExe = "python.exe",
    [string]$Actor = "Human Author",
    [string]$Author = "Co_Reviewer"
)
$ErrorActionPreference = "Stop"

function Get-FullPath {
    param([string]$Path, [string]$BasePath)
    if ([System.IO.Path]::IsPathRooted($Path)) { return [System.IO.Path]::GetFullPath($Path) }
    return [System.IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}
function Resolve-ExistingFile {
    param([string]$Path, [string]$Label, [string]$BasePath)
    $full = Get-FullPath $Path $BasePath
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "$Label does not exist: $full" }
    return (Resolve-Path -LiteralPath $full).Path
}
function Assert-PathInRoundRoot {
    param([string]$Path, [string]$Label)
    $candidate = if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $script:RoundPath $Path }
    $full = [System.IO.Path]::GetFullPath($candidate)
    $prefix = [System.IO.Path]::GetFullPath($script:RoundPath).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must remain inside the review-round workspace '$script:RoundPath': $full"
    }
    return $full
}
function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Update-RoundManifest {
    param([string]$EventType, [hashtable]$Event)
    $manifestPath = Join-Path $script:RoundPath "round_manifest.json"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($entry in $Event.GetEnumerator()) {
        $manifest | Add-Member -Force -NotePropertyName $entry.Key -NotePropertyValue $entry.Value
    }
    $events = @($manifest.workflow_history)
    $events += [pscustomobject]@{
        event_type = $EventType
        timestamp_utc = (Get-Date).ToUniversalTime().ToString("o")
        workflow_ledger = $script:CurrentWorkflowPath
        reviewer_map = $script:CurrentReviewerMapPath
        revision_ledger = $script:CurrentRevisionLedgerPath
        runs_directory = $script:CurrentRunsPath
        details = $Event
    }
    if ($manifest.PSObject.Properties["workflow_history"]) {
        $manifest.workflow_history = $events
    }
    else {
        $manifest | Add-Member -NotePropertyName workflow_history -NotePropertyValue $events
    }
    $temporaryPath = $manifestPath + "." + [Guid]::NewGuid().ToString("N") + ".tmp"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText(
        $temporaryPath,
        (($manifest | ConvertTo-Json -Depth 50) + [Environment]::NewLine),
        $encoding
    )
    try {
        if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
            [System.IO.File]::Replace($temporaryPath, $manifestPath, $null)
        }
        else {
            [System.IO.File]::Move($temporaryPath, $manifestPath)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}
function Read-JsonFile {
    param([string]$Path, [string]$Label)
    try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) }
    catch { throw "Cannot read $Label JSON '$Path': $($_.Exception.Message)" }
}
function Resolve-Python {
    param([string]$Requested)
    if ($Requested -match '[\\/]' -and (Test-Path -LiteralPath $Requested -PathType Leaf)) {
        return (Resolve-Path -LiteralPath $Requested).Path
    }
    $command = Get-Command $Requested -ErrorAction SilentlyContinue
    if (-not $command) { throw "Python executable was not found: $Requested" }
    return $command.Source
}
function Invoke-CoReviewerCli {
    param([string[]]$Arguments)
    Write-Host ""
    Write-Host ("[CLI] " + $script:PythonResolved + " " + $script:CliPath + " " + ($Arguments -join " "))
    $captured = @(& $script:PythonResolved $script:CliPath @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    foreach ($line in $captured) { Write-Host ([string]$line) }
    if ($exitCode -ne 0) {
        throw "Co_Reviewer CLI failed with exit code $exitCode. Preserve this round and batch; do not retry in place."
    }
    return ,@($captured | ForEach-Object { [string]$_ })
}
function Confirm-Exact {
    param([string]$Prompt, [string]$Expected)
    Write-Host ""
    Write-Host $Prompt
    Write-Host ("Type exactly: " + $Expected)
    if ((Read-Host "Confirmation") -cne $Expected) {
        throw "Confirmation did not match. No later stage was executed; preserve all current artifacts."
    }
}

$script:RoundPath = (Resolve-Path -LiteralPath $RoundRoot -ErrorAction Stop).Path
$script:RoundManifestPath = Join-Path $script:RoundPath "round_manifest.json"
if (-not (Test-Path -LiteralPath $script:RoundManifestPath -PathType Leaf)) {
    throw "RoundRoot does not contain round_manifest.json: $script:RoundPath"
}
$script:RoundManifest = Read-JsonFile $script:RoundManifestPath "round_manifest"
$script:RoundId = [string]$script:RoundManifest.review_round_id
if ([string]::IsNullOrWhiteSpace($script:RoundId)) {
    throw "round_manifest.json has no review_round_id."
}
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:CliPath = Join-Path $repoRoot "scripts\co_reviewer_cli.py"
if (-not (Test-Path -LiteralPath $script:CliPath -PathType Leaf)) { throw "CLI not found: $script:CliPath" }
$script:PythonResolved = Resolve-Python $PythonExe

$workflowInput = Resolve-ExistingFile $WorkflowLedger "Workflow Ledger" $script:RoundPath
[void](Assert-PathInRoundRoot $workflowInput "Workflow Ledger")
$initialWorkflow = Read-JsonFile $workflowInput "initial Workflow Ledger"
if ([string]$initialWorkflow.revision_round_id -ne $script:RoundId) {
    throw "Initial Workflow Ledger belongs to round '$($initialWorkflow.revision_round_id)', but RoundRoot is '$script:RoundId'."
}
if ($initialWorkflow.active_documents) {
    foreach ($pointerProperty in $initialWorkflow.active_documents.PSObject.Properties) {
        $pointer = $pointerProperty.Value
        if (-not $pointer -or [string]::IsNullOrWhiteSpace([string]$pointer.file)) {
            throw "Initial Workflow active-document pointer '$($pointerProperty.Name)' has no file."
        }
        $activeFile = Get-FullPath ([string]$pointer.file) $script:RoundPath
        [void](Assert-PathInRoundRoot $activeFile "Active document $($pointerProperty.Name)")
        if (-not (Test-Path -LiteralPath $activeFile -PathType Leaf) -or (Get-Sha256 $activeFile) -ne [string]$pointer.sha256) {
            throw "Initial Workflow active-document pointer '$($pointerProperty.Name)' is missing or hash-stale: $activeFile"
        }
    }
}
$ids = @($TaskIds | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ } | Select-Object -Unique)
if ($ids.Count -eq 0 -or $ids.Count -ne $TaskIds.Count) { throw "TaskIds must contain unique, non-empty IDs." }

$hasScope = -not [string]::IsNullOrWhiteSpace($ExecutionScope)
$hasInventory = -not [string]::IsNullOrWhiteSpace($Inventory)
if ($hasScope -ne $hasInventory) { throw "ExecutionScope and Inventory must be supplied together." }

$currentWorkflow = $workflowInput
if ($hasScope) {
    $scopePath = Resolve-ExistingFile $ExecutionScope "Execution Scope" $script:RoundPath
    $inventoryPath = Resolve-ExistingFile $Inventory "Workspace Inventory" $script:RoundPath
    [void](Assert-PathInRoundRoot $scopePath "Execution Scope")
    [void](Assert-PathInRoundRoot $inventoryPath "Workspace Inventory")
    if ($ReviewerMap) { throw "Do not supply ReviewerMap with ExecutionScope; preparation generates a reviewer map." }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $prepared = if ($PreparedWorkflowLedgerOut) { Get-FullPath $PreparedWorkflowLedgerOut $script:RoundPath } else { Join-Path $script:RoundPath "plan\workflow_ledger.prepared.$stamp.json" }
    $reviewerMapPath = if ($ReviewerMapOut) { Get-FullPath $ReviewerMapOut $script:RoundPath } else { Join-Path $script:RoundPath "plan\reviewer_map.$stamp.json" }
    $preparationReport = if ($PreparationReportOut) { Get-FullPath $PreparationReportOut $script:RoundPath } else { Join-Path $script:RoundPath "reports\workflow_preparation.$stamp.json" }
    foreach ($path in @($prepared, $reviewerMapPath, $preparationReport)) {
        [void](Assert-PathInRoundRoot $path "Workflow preparation output")
        if (Test-Path -LiteralPath $path) { throw "Refusing to overwrite preparation output: $path" }
    }
    [void](Invoke-CoReviewerCli @("prepare-workflow-execution", "--workflow-ledger", $workflowInput, "--execution-scope", $scopePath, "--inventory", $inventoryPath, "--out", $prepared, "--reviewer-map-out", $reviewerMapPath, "--report", $preparationReport))
    $currentWorkflow = $prepared
}
else {
    if (-not $ReviewerMap) { throw "ReviewerMap is required when ExecutionScope and Inventory are omitted." }
    $reviewerMapPath = Resolve-ExistingFile $ReviewerMap "Reviewer Map" $script:RoundPath
}
$revisionPath = if ($RevisionLedger) { Get-FullPath $RevisionLedger $script:RoundPath } else { Join-Path $script:RoundPath "plan\revision_ledger.json" }
$runsPath = if ($RunsDirectory) { Get-FullPath $RunsDirectory $script:RoundPath } else { Join-Path $script:RoundPath "runs" }
[void](Assert-PathInRoundRoot $revisionPath "RevisionLedger")
[void](Assert-PathInRoundRoot $runsPath "RunsDirectory")
if (-not [string]::IsNullOrWhiteSpace($PreparedWorkflowLedgerOut) -and -not $hasScope) {
    throw "PreparedWorkflowLedgerOut can only be supplied when creating a newly prepared Workflow Ledger from ExecutionScope/Inventory."
}
$parent = Split-Path -Parent $revisionPath
if (-not (Test-Path -LiteralPath $parent -PathType Container)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
if (-not (Test-Path -LiteralPath $runsPath -PathType Container)) { New-Item -ItemType Directory -Path $runsPath -Force | Out-Null }
if ($revisionPath -eq $currentWorkflow -or $revisionPath -eq $reviewerMapPath) { throw "RevisionLedger must be a distinct file." }
[void](Assert-PathInRoundRoot $currentWorkflow "Prepared Workflow Ledger")
[void](Assert-PathInRoundRoot $reviewerMapPath "Reviewer Map")
$script:CurrentWorkflowPath = $currentWorkflow
$script:CurrentReviewerMapPath = $reviewerMapPath
$script:CurrentRevisionLedgerPath = $revisionPath
$script:CurrentRunsPath = $runsPath
if ($hasScope) {
    Update-RoundManifest -EventType "workflow_execution_prepared" -Event @{
        current_workflow_ledger = $currentWorkflow
        current_reviewer_map = $reviewerMapPath
        workflow_preparation_report = $preparationReport
        prepared_workflow_ledger_sha256 = (Get-Sha256 $currentWorkflow)
        reviewer_map_sha256 = (Get-Sha256 $reviewerMapPath)
        revision_ledger = $revisionPath
        runs_directory = $runsPath
    }
}
else {
    Update-RoundManifest -EventType "workflow_execution_resumed" -Event @{
        current_workflow_ledger = $currentWorkflow
        current_reviewer_map = $reviewerMapPath
        revision_ledger = $revisionPath
        runs_directory = $runsPath
    }
}

$workflow = Read-JsonFile $currentWorkflow "Workflow Ledger"
if ([string]$workflow.revision_round_id -ne $script:RoundId) {
    throw "Prepared Workflow Ledger belongs to a different review round."
}
$selectedTasks = @($workflow.tasks | Where-Object { $_ -and $ids -contains [string]$_.task_id })
if ($selectedTasks.Count -ne $ids.Count) { throw "Workflow Ledger does not contain every selected Task ID." }
$documentTaskCount = 0
$resolutionOnlyTaskCount = 0
foreach ($task in $selectedTasks) {
    $taskOperations = @($task.patch_operations | Where-Object { $_ -and $_.document -and $_.operation_id })
    if ($taskOperations.Count -gt 0) {
        $documentTaskCount++
        continue
    }
    $specs = @($task.response_unit_specs)
    $commentCount = @($task.source_comment_ids).Count
    $explicitResolutionOnly = $commentCount -gt 0 -and $specs.Count -eq $commentCount
    if ($explicitResolutionOnly) {
        foreach ($spec in $specs) {
            if ($spec.disposition -notin @("reply_only", "no_change")) {
                $explicitResolutionOnly = $false
                break
            }
        }
    }
    if ([string]$task.task_type -eq "response_only" -or $explicitResolutionOnly) {
        $resolutionOnlyTaskCount++
        continue
    }
    throw "Task $($task.task_id) has no document PatchOperations and is not an explicitly classified response_only/no_change task. Classify every source comment before execution."
}
if ($documentTaskCount -gt 0 -and $resolutionOnlyTaskCount -gt 0) {
    throw "Do not mix document-changing tasks and resolution-only tasks in one batch invocation. Run the document-changing tasks first, then select the response_only/no_change tasks separately."
}


Confirm-Exact ("Authorize Workflow task(s): " + ($ids -join ", ")) ("AUTHORIZE " + ($ids -join ","))
[void](Invoke-CoReviewerCli (@("approve-workflow-tasks", "--workflow-ledger", $currentWorkflow, "--ids") + $ids + @("--plan-approved", "--actor", $Actor)))

$buildOutput = Invoke-CoReviewerCli (@("build-revision-ledger", "--workflow-ledger", $currentWorkflow, "--reviewer-map", $reviewerMapPath, "--task-ids") + $ids + @("--runs-dir", $runsPath, "--out", $revisionPath))
$batchDirectory = $null
foreach ($line in $buildOutput) {
    $m = [regex]::Match([string]$line, '^\[BATCH DIRECTORY\]\s+(.+?)\s*$')
    if ($m.Success) { $batchDirectory = [System.IO.Path]::GetFullPath($m.Groups[1].Value.Trim()); break }
}
if (-not $batchDirectory) {
    if ($documentTaskCount -gt 0) {
        throw "No document-changing batch was created for tasks that declare PatchOperations. RevisionLedger was preserved at $revisionPath."
    }
    $resolutionLedger = Read-JsonFile $revisionPath "RevisionLedger after response-only preparation"
    $resolutionUnits = @($resolutionLedger.response_units | Where-Object {
        $unit = $_
        @($unit.linked_task_ids | Where-Object { $ids -contains [string]$_ }).Count -gt 0
    })
    if ($resolutionUnits.Count -eq 0) {
        throw "Resolution-only tasks produced no ResponseUnits. Preserve the Workflow Ledger and RevisionLedger for diagnosis."
    }
    Write-Host ""
    Write-Host "[RESOLUTION-ONLY] No immutable document batch was created because the selected tasks have no document PatchOperations."
    foreach ($unit in $resolutionUnits) {
        Write-Host ("UNIT: " + $unit.response_unit_id + " | " + $unit.disposition + " | " + $unit.location_requirement)
        Write-Host ("REVIEWER COMMENT: " + $unit.comment_text)
        Write-Host ("PROPOSED RESPONSE: " + $unit.response_text)
        if ($unit.disposition -eq "no_change") {
            Write-Host ("ANCHOR: " + $unit.anchor_document + " | " + $unit.anchor_part + " | p_idx=" + $unit.anchor_p_idx)
        }
    }
    Update-RoundManifest -EventType "resolution_only_tasks_prepared" -Event @{
        resolution_only_task_ids = $ids
        response_unit_ids = @($resolutionUnits | ForEach-Object { [string]$_.response_unit_id })
        workflow_ledger_sha256 = (Get-Sha256 $currentWorkflow)
        revision_ledger_sha256 = (Get-Sha256 $revisionPath)
    }
    Write-Host ""
    Write-Host "[RESOLUTION-ONLY] No immutable document batch was created because the selected tasks have no document PatchOperations."
    Write-Host ("[WORKFLOW LEDGER] " + $currentWorkflow)
    Write-Host ("[REVIEWER MAP] " + $reviewerMapPath)
    Write-Host ("[REVISION LEDGER] " + $revisionPath)
    Write-Host "[NEXT] After every document-changing batch is published, run Complete-CoReviewerRound.ps1. It will perform semantic confirmation, any required final location mapping, ResponseUnit completion and Workflow synchronization."
    return
}
$runsPrefix = [System.IO.Path]::GetFullPath($runsPath).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
if (-not $batchDirectory.StartsWith($runsPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Batch directory escapes the configured runs directory: $batchDirectory" }

$batchScopePath = Join-Path $batchDirectory "batch_scope.json"
if (-not (Test-Path -LiteralPath $batchScopePath -PathType Leaf)) { throw "batch_scope.json is missing. Preserve batch: $batchDirectory" }
$batchScope = Read-JsonFile $batchScopePath "Batch Scope"
$batchId = [string]$batchScope.batch_id
$patchIds = @($batchScope.patch_ids | ForEach-Object { [string]$_ } | Where-Object { $_ } | Select-Object -Unique)
if ($patchIds.Count -eq 0) { throw "Batch Scope contains no Patch IDs: $batchDirectory" }

$revision = Read-JsonFile $revisionPath "RevisionLedger"
$patches = @($revision.patches | Where-Object { $patchIds -contains [string]$_.patch_id })
if ($patches.Count -ne $patchIds.Count) { throw "RevisionLedger does not contain every Patch declared by Batch Scope." }
foreach ($patch in $patches) {
    foreach ($operation in $patch.operations) {
        [void](Assert-PathInRoundRoot ([string]$operation.input_file) "PatchOperation input")
        [void](Assert-PathInRoundRoot ([string]$operation.output_file) "PatchOperation output")
    }
}
Write-Host ""
Write-Host "========== EXACT PATCH SCOPE =========="
foreach ($patch in $patches) {
    Write-Host ("PATCH " + $patch.patch_id + " | TASK " + $patch.task_id + " | REVIEWER " + $patch.reviewer)
    Write-Host ("COMMENT: " + $patch.comment)
    Write-Host ("RESPONSE: " + $patch.proposed_response)
    foreach ($operation in $patch.operations) {
        Write-Host ("OP " + $operation.operation_id + " | " + $operation.document + " | " + $operation.part + " | p_idx=" + $operation.p_idx)
        Write-Host ("  FIND: " + $operation.find)
        Write-Host ("  REPLACE: " + $operation.replace)
        Write-Host ("  INPUT: " + $operation.input_file)
        Write-Host ("  OUTPUT: " + $operation.output_file)
    }
}
Write-Host "======================================="

$plan = Join-Path $batchDirectory "revision_plan.json"
[void](Invoke-CoReviewerCli @("export-revision-plan", "--ledger", $revisionPath, "--batch-dir", $batchDirectory))
if (-not (Test-Path -LiteralPath $plan -PathType Leaf)) { throw "Revision plan export is missing: $plan" }

$preflightedPlan = Join-Path $batchDirectory "revision_plan.preflighted.json"
$preflightReport = Join-Path $batchDirectory "preflight_report.json"
[void](Invoke-CoReviewerCli @("preflight-plan", "--plan", $plan, "--revision-ledger", $revisionPath, "--author", $Actor))
if (-not (Test-Path -LiteralPath $preflightedPlan -PathType Leaf) -or -not (Test-Path -LiteralPath $preflightReport -PathType Leaf)) { throw "Preflight artifacts are missing. Preserve batch: $batchDirectory" }
$preflight = Read-JsonFile $preflightReport "Preflight Report"
if ([int]$preflight.targets_total -le 0 -or [int]$preflight.targets_preflighted -ne [int]$preflight.targets_total -or @($preflight.approval_scope_changed_items).Count -gt 0) {
    throw "Preflight did not fully pass. No approval or Apply was attempted. Preserve batch: $batchDirectory"
}
$preflightPlanData = Read-JsonFile $preflightedPlan "Preflighted Plan"
$exportedIds = @($preflightPlanData.revision_items | ForEach-Object { [string]$_.id })
if ($exportedIds.Count -ne $patchIds.Count -or @($patchIds | Where-Object { $exportedIds -notcontains $_ }).Count -gt 0) { throw "Plan IDs differ from Batch Scope. Preserve batch: $batchDirectory" }

Confirm-Exact "Review the exact PatchOperations and preflight report. No DOCX has been edited yet." ("APPROVE " + ($patchIds -join ","))
$approvedPlan = Join-Path $batchDirectory "revision_plan.approved.json"
[void](Invoke-CoReviewerCli ((@("approve-plan", "--plan", $preflightedPlan, "--ids") + $patchIds) + @("--revision-ledger", $revisionPath, "--actor", $Actor)))

[void](Invoke-CoReviewerCli @("apply", "--plan", $approvedPlan, "--revision-ledger", $revisionPath, "--author", $Author))
$appliedPlan = Join-Path $batchDirectory "revision_plan.applied.json"
$applyReport = Join-Path $batchDirectory "apply_report.json"
if (-not (Test-Path -LiteralPath $appliedPlan -PathType Leaf) -or -not (Test-Path -LiteralPath $applyReport -PathType Leaf)) { throw "Apply artifacts are incomplete. Preserve batch: $batchDirectory" }

[void](Invoke-CoReviewerCli @("verify", "--plan", $appliedPlan))
$verificationPath = Join-Path $batchDirectory "verification_report.json"
if (-not (Test-Path -LiteralPath $verificationPath -PathType Leaf)) { throw "Verification report is missing. Preserve batch: $batchDirectory" }
$verification = Read-JsonFile $verificationPath "Verification Report"
if ($verification.passed -ne $true -or [int]$verification.targets_total -le 0 -or [int]$verification.targets_verified -ne [int]$verification.targets_total -or -not $verification.untracked_text_audit.passed) {
    throw "Mechanical verification or untracked-text audit failed. No semantic confirmation or publication was attempted. Preserve batch: $batchDirectory"
}

$ledgerBeforeSemanticReview = Read-JsonFile $revisionPath "RevisionLedger before semantic review"
Write-Host ""
Write-Host "========== VERIFIED MARKED DOCUMENTS =========="
foreach ($document in $ledgerBeforeSemanticReview.documents) {
    $selectedRole = $false
    foreach ($patch in $patches) {
        foreach ($operation in $patch.operations) {
            if ($operation.document -eq $document.role) { $selectedRole = $true }
        }
    }
    if ($selectedRole) {
        Write-Host ("ROLE: " + $document.role)
        Write-Host ("FILE: " + $document.output_file)
        if (Test-Path -LiteralPath $document.output_file -PathType Leaf) {
            Write-Host ("SHA256: " + (Get-FileHash -LiteralPath $document.output_file -Algorithm SHA256).Hash.ToLowerInvariant())
        }
    }
}
Write-Host "================================================="
Confirm-Exact "Open and inspect the Marked DOCX outputs listed above. Confirm scientific meaning, units, values, sample IDs, citations, figures/tables and cross-document consistency." ("SEMANTIC REVIEW CONFIRMED " + ($patchIds -join ","))
[void](Invoke-CoReviewerCli ((@("confirm-verified", "--plan", $appliedPlan, "--ids") + $patchIds) + @("--revision-ledger", $revisionPath, "--semantic-review-confirmed")))
[void](Invoke-CoReviewerCli @("sync-workflow-ledger", "--workflow-ledger", $currentWorkflow, "--revision-ledger", $revisionPath))

Confirm-Exact "Publication changes the active-document pointers for this review round. Confirm only after reviewing the verified outputs." ("PUBLISH " + $batchId)
[void](Invoke-CoReviewerCli @("publish-batch", "--workflow-ledger", $currentWorkflow, "--revision-ledger", $revisionPath, "--batch-dir", $batchDirectory))

$receiptPath = Join-Path $batchDirectory "batch_published.json"
if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw "Publication receipt is missing after publish. Preserve batch: $batchDirectory" }
$receipt = Read-JsonFile $receiptPath "Batch Publication Receipt"
$publishedWorkflow = Read-JsonFile $currentWorkflow "Published Workflow Ledger"
foreach ($property in $receipt.active_documents.PSObject.Properties) {
    $role = $property.Name
    $expected = $property.Value
    $actual = $publishedWorkflow.active_documents.$role
    if (-not $actual -or $actual.file -ne $expected.file -or $actual.sha256 -ne $expected.sha256 -or $actual.batch_id -ne $expected.batch_id) {
        throw "Workflow active pointer for $role does not match publication receipt. Preserve batch: $batchDirectory"
    }
    if (-not (Test-Path -LiteralPath $expected.file -PathType Leaf) -or (Get-FileHash -LiteralPath $expected.file -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$expected.sha256) {
        throw "Published active file for $role is missing or has a hash mismatch: $($expected.file)"
    }
}
Write-Host ""
Write-Host "[BATCH COMPLETE] $batchId"
Write-Host "[BATCH DIRECTORY] $batchDirectory"
Write-Host "[WORKFLOW LEDGER] $currentWorkflow"
Write-Host "[REVIEWER MAP] $reviewerMapPath"
Write-Host "[REVISION LEDGER] $revisionPath"
Write-Host "[PUBLICATION RECEIPT] $receiptPath"
Update-RoundManifest -EventType "batch_published" -Event @{
    last_published_batch_id = $batchId
    last_published_batch_directory = $batchDirectory
    last_publication_receipt = $receiptPath
    last_publication_receipt_sha256 = (Get-Sha256 $receiptPath)
    workflow_ledger_sha256 = (Get-Sha256 $currentWorkflow)
    revision_ledger_sha256 = (Get-Sha256 $revisionPath)
    active_documents = $receipt.active_documents
}
Write-Host "[NEXT] If downstream tasks became recheck_required, run Refresh-CoReviewerRound.ps1 and prepare a new evidence-bound scope before the next batch. Never reuse old coordinates."
