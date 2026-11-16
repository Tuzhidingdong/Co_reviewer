[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RoundRoot,
    [string]$WorkflowLedger,
    [string]$RevisionLedger,
    [string]$RunsDirectory,
    [switch]$ResumeReleasePackage,
    [string]$PythonExe = "python.exe"
)
$ErrorActionPreference = "Stop"

function Get-FullPath {
    param([string]$Path, [string]$BasePath)
    if ([System.IO.Path]::IsPathRooted($Path)) { return [System.IO.Path]::GetFullPath($Path) }
    return [System.IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}
function Assert-PathInRoundRoot {
    param([string]$Path, [string]$Label)
    $full = [System.IO.Path]::GetFullPath($Path)
    $prefix = [System.IO.Path]::GetFullPath($script:RoundPath).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must remain inside the review-round workspace '$script:RoundPath': $full"
    }
    return $full
}
function Assert-OrdinaryPath {
    param([string]$Path, [string]$Label)
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label must not be a symbolic link or reparse point: $Path"
        }
    }
}
function Recover-WorkflowSync {
    param([string]$WorkflowFile, [string]$FallbackRevisionFile, [string]$Label)
    $workflowFull = [System.IO.Path]::GetFullPath($WorkflowFile)
    $leaf = Split-Path -Leaf $workflowFull
    $pendingPath = Join-Path (Split-Path -Parent $workflowFull) ("." + $leaf + ".sync-pending.json")
    if (Test-Path -LiteralPath $pendingPath -PathType Leaf) {
        [void](Assert-OrdinaryPath $pendingPath "Pending Workflow sync marker")
        $pending = Read-JsonFile $pendingPath "Pending Workflow Sync Marker"
        if ([System.IO.Path]::GetFullPath([string]$pending.workflow_ledger_file) -ne $workflowFull) {
            throw "Pending sync marker is bound to a different Workflow Ledger: $pendingPath"
        }
        $revisionValue = [string]$pending.revision_ledger_file
        if ([string]::IsNullOrWhiteSpace($revisionValue)) {
            throw "Pending sync marker has no RevisionLedger path: $pendingPath"
        }
        $pendingRevision = Get-FullPath $revisionValue $script:RoundPath
        [void](Assert-PathInRoundRoot $pendingRevision "Pending sync RevisionLedger")
        [void](Assert-OrdinaryPath $pendingRevision "Pending sync RevisionLedger")
        if (-not (Test-Path -LiteralPath $pendingRevision -PathType Leaf)) {
            throw "Pending Workflow sync RevisionLedger is missing: $pendingRevision"
        }
        Invoke-CoReviewerCli @("recover-workflow-sync","--workflow-ledger",$workflowFull,"--revision-ledger",$pendingRevision) $Label
        return
    }
    if (Test-Path -LiteralPath $FallbackRevisionFile -PathType Leaf) {
        Invoke-CoReviewerCli @("recover-workflow-sync","--workflow-ledger",$workflowFull,"--revision-ledger",$FallbackRevisionFile) $Label
    }
}
function Read-JsonFile {
    param([string]$Path, [string]$Label)
    try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) }
    catch { throw "Cannot read $Label JSON '$Path': $($_.Exception.Message)" }
}
function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
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
    param([string[]]$Arguments, [string]$Label)
    Write-Host ""
    Write-Host ("[RECONCILE] " + $Label)
    $captured = @(& $script:PythonResolved $script:CliPath @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    foreach ($line in $captured) { Write-Host ([string]$line) }
    if ($exitCode -ne 0) {
        throw "Round reconciliation failed during '$Label' (exit $exitCode). Preserve all transaction artifacts and correct the underlying state before retrying."
    }
}
function Write-RoundManifestAtomic {
    param([object]$Manifest, [string]$Path)
    $temporaryPath = $Path + "." + [Guid]::NewGuid().ToString("N") + ".tmp"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporaryPath,(($Manifest | ConvertTo-Json -Depth 60) + [Environment]::NewLine),$encoding)
    try {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [System.IO.File]::Replace($temporaryPath,$Path,$null)
        } else {
            [System.IO.File]::Move($temporaryPath,$Path)
        }
    } finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

$script:RoundPath = (Resolve-Path -LiteralPath $RoundRoot -ErrorAction Stop).Path
$manifestPath = Join-Path $script:RoundPath "round_manifest.json"
[void](Assert-OrdinaryPath $manifestPath "Round manifest")
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "RoundRoot does not contain round_manifest.json: $script:RoundPath"
}
$manifest = Read-JsonFile $manifestPath "round_manifest"
$roundId = [string]$manifest.review_round_id
if ([string]::IsNullOrWhiteSpace($roundId)) { throw "round_manifest.json has no review_round_id." }

$workflowValue = if ($WorkflowLedger) {
    $WorkflowLedger
} elseif ($manifest.current_workflow_ledger) {
    [string]$manifest.current_workflow_ledger
} elseif ($manifest.prepared_workflow_ledger) {
    [string]$manifest.prepared_workflow_ledger
} else {
    [string]$manifest.workflow_ledger
}
$revisionValue = if ($RevisionLedger) {
    $RevisionLedger
} elseif ($manifest.current_revision_ledger) {
    [string]$manifest.current_revision_ledger
} else {
    [string]$manifest.revision_ledger
}
if ([string]::IsNullOrWhiteSpace($workflowValue) -or [string]::IsNullOrWhiteSpace($revisionValue)) {
    throw "Round manifest does not identify both the current Workflow Ledger and RevisionLedger."
}
$script:WorkflowPath = Get-FullPath $workflowValue $script:RoundPath
$script:RevisionPath = Get-FullPath $revisionValue $script:RoundPath
[void](Assert-PathInRoundRoot $script:WorkflowPath "Workflow Ledger")
[void](Assert-PathInRoundRoot $script:RevisionPath "RevisionLedger")
if (-not (Test-Path -LiteralPath $script:WorkflowPath -PathType Leaf)) {
    throw "Workflow Ledger is missing: $script:WorkflowPath"
}
if (-not (Test-Path -LiteralPath $script:RevisionPath -PathType Leaf)) {
    # A fresh round has no RevisionLedger yet. Check the same hidden pending-marker name used by the CLI.
    $currentPendingPath = Join-Path (Split-Path -Parent $script:WorkflowPath) ("." + (Split-Path -Leaf $script:WorkflowPath) + ".sync-pending.json")
    if (Test-Path -LiteralPath $currentPendingPath -PathType Leaf) {
        throw "A pending Workflow sync exists but its RevisionLedger is missing: $script:RevisionPath"
    }
}
$runsValue = if ($RunsDirectory) {
    $RunsDirectory
} elseif ($manifest.runs_directory) {
    [string]$manifest.runs_directory
} elseif ($manifest.directories.runs) {
    [string]$manifest.directories.runs
} else {
    "runs"
}
$runsPath = Get-FullPath $runsValue $script:RoundPath
[void](Assert-PathInRoundRoot $runsPath "RunsDirectory")
[void](Assert-OrdinaryPath $runsPath "RunsDirectory")
$recordedRunsValue = if ($manifest.runs_directory) { [string]$manifest.runs_directory } elseif ($manifest.directories.runs) { [string]$manifest.directories.runs } else { $null }
if ($RunsDirectory -and $recordedRunsValue) {
    $recordedRunsPath = Get-FullPath $recordedRunsValue $script:RoundPath
    if ([System.IO.Path]::GetFullPath($recordedRunsPath) -ne [System.IO.Path]::GetFullPath($runsPath)) {
        throw "RunsDirectory is fixed for a review round. Existing=$recordedRunsPath; requested=$runsPath"
    }
}

$script:PythonResolved = Resolve-Python $PythonExe
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:CliPath = Join-Path $repoRoot "scripts\co_reviewer_cli.py"
if (-not (Test-Path -LiteralPath $script:CliPath -PathType Leaf)) { throw "CLI not found: $script:CliPath" }

[void](Assert-OrdinaryPath $script:WorkflowPath "Current Workflow Ledger")
if (Test-Path -LiteralPath $script:RevisionPath -PathType Leaf) {
    [void](Assert-OrdinaryPath $script:RevisionPath "Current RevisionLedger")
    Recover-WorkflowSync -WorkflowFile $script:WorkflowPath -FallbackRevisionFile $script:RevisionPath -Label "recover current Workflow sync"
}
else {
    $currentPendingPath = Join-Path (Split-Path -Parent $script:WorkflowPath) ("." + (Split-Path -Leaf $script:WorkflowPath) + ".sync-pending.json")
    if (Test-Path -LiteralPath $currentPendingPath -PathType Leaf) {
        throw "A pending Workflow sync exists but no RevisionLedger is available: $script:RevisionPath"
    }
    Write-Host "[NO REVISION LEDGER] This is a fresh round; no two-ledger sync can be pending."
}

$batchTransactions = @()
$workflowPathsForSync = [System.Collections.ArrayList]::new()
[void]$workflowPathsForSync.Add($script:WorkflowPath)
if (Test-Path -LiteralPath $runsPath -PathType Container) {
    $batchDirs = @(Get-ChildItem -LiteralPath $runsPath -Directory -Filter "batch-*")
    foreach ($batchDir in ($batchDirs | Sort-Object Name)) {
        [void](Assert-OrdinaryPath $batchDir.FullName "Batch directory")
        $intentPath = Join-Path $batchDir.FullName "batch_intent.json"
        [void](Assert-OrdinaryPath $intentPath "Batch intent")
        if (-not (Test-Path -LiteralPath $intentPath -PathType Leaf)) {
            $controls = @(
                "batch_published.json","batch_publish_intent.json",
                "batch_recovered.json","batch_recovery_intent.json",
                "batch_rollback.json","batch_rollback_intent.json",
                "batch_superseded.json"
            ) | Where-Object { Test-Path -LiteralPath (Join-Path $batchDir.FullName $_) -PathType Leaf }
            if ($controls.Count -gt 0) {
                throw "Batch transaction controls exist without batch_intent.json: $($batchDir.FullName)"
            }
            continue
        }
        $intent = Read-JsonFile $intentPath "Batch Intent"
        $batchSelectedTaskIds = @($intent.all_selected_task_ids)
        if ($batchSelectedTaskIds.Count -eq 0) { $batchSelectedTaskIds = @($intent.selected_task_ids) }
        $batchPatchIds = @()
        $scopePath = Join-Path $batchDir.FullName "batch_scope.json"
        $supersededReceiptPath = Join-Path $batchDir.FullName "batch_superseded.json"
        [void](Assert-OrdinaryPath $scopePath "Batch scope")
        [void](Assert-OrdinaryPath $supersededReceiptPath "Batch supersession receipt")
        $boundWorkflow = Get-FullPath ([string]$intent.workflow_ledger_file) $script:RoundPath
        [void](Assert-PathInRoundRoot $boundWorkflow "Batch-bound Workflow Ledger")
        if (-not (Test-Path -LiteralPath $scopePath -PathType Leaf)) {
            if (Test-Path -LiteralPath $supersededReceiptPath -PathType Leaf) {
                $supersededReceipt = Read-JsonFile $supersededReceiptPath "Prepared Batch Supersession Receipt"
                $successorDirectory = Join-Path $runsPath ("batch-" + [string]$supersededReceipt.successor_batch_id)
                [void](Assert-OrdinaryPath $successorDirectory "Successor batch directory")
                if (-not (Test-Path -LiteralPath $successorDirectory -PathType Container)) {
                    throw "Superseded prepared batch points to a missing successor: $successorDirectory"
                }
                $successorScopePath = Join-Path $successorDirectory "batch_scope.json"
                [void](Assert-OrdinaryPath $successorScopePath "Successor batch scope")
                if (-not (Test-Path -LiteralPath $successorScopePath -PathType Leaf)) {
                    throw "Superseded prepared batch successor has no immutable scope: $successorScopePath"
                }
                $successorScope = Read-JsonFile $successorScopePath "Successor Batch Scope"
                $successorRevision = Get-FullPath ([string]$successorScope.revision_ledger_file) $script:RoundPath
                [void](Assert-PathInRoundRoot $successorRevision "Successor RevisionLedger")
                [void](Assert-OrdinaryPath $successorRevision "Successor RevisionLedger")
                if (-not (Test-Path -LiteralPath $successorRevision -PathType Leaf)) {
                    throw "Superseded prepared batch successor RevisionLedger is missing: $successorRevision"
                }
                Invoke-CoReviewerCli @(
                    "retire-superseded-prepared-batch",
                    "--workflow-ledger",$boundWorkflow,
                    "--revision-ledger",$successorRevision,
                    "--batch-dir",$batchDir.FullName,
                    "--successor-batch-dir",$successorDirectory,
                    "--actor","Co_Reviewer Round Reconciler",
                    "--reason","Verify the already-recorded exact published successor and preserve intent-only retirement evidence."
                ) "verify superseded intent-only batch $($intent.batch_id)"
                $batchTransactions += [pscustomobject]@{
                    batch_id = [string]$intent.batch_id
                    batch_directory = $batchDir.FullName
                    selected_task_ids = $batchSelectedTaskIds
                    patch_ids = @()
                    workflow_ledger = $boundWorkflow
                    revision_ledger = $successorRevision
                    published_receipt = $null
                    rolled_back_receipt = $null
                    recovered_receipt = $null
                    superseded_receipt = $supersededReceiptPath
                    unresolved_intent_only = $false
                }
                if (-not $workflowPathsForSync.Contains($boundWorkflow)) { [void]$workflowPathsForSync.Add($boundWorkflow) }
                continue
            }
            $remaining = @(Get-ChildItem -LiteralPath $batchDir.FullName -Force)
            if ($remaining.Count -le 2 -and
                -not (Test-Path -LiteralPath (Join-Path $batchDir.FullName "batch_published.json")) -and
                -not (Test-Path -LiteralPath (Join-Path $batchDir.FullName "batch_publish_intent.json")) -and
                -not (Test-Path -LiteralPath (Join-Path $batchDir.FullName "batch_recovery_intent.json"))) {
                Write-Warning "Intent-only prepared batch has no scope or execution artifacts and is retained without changing it: $($batchDir.FullName). The same selected task set may be re-prepared; after an exact successor is published, use retire-superseded-prepared-batch."
                $batchTransactions += [pscustomobject]@{
                    batch_id = [string]$intent.batch_id
                    batch_directory = $batchDir.FullName
                    selected_task_ids = $batchSelectedTaskIds
                    patch_ids = @()
                    workflow_ledger = $boundWorkflow
                    revision_ledger = $script:RevisionPath
                    published_receipt = $null
                    rolled_back_receipt = $null
                    recovered_receipt = $null
                    superseded_receipt = $null
                    unresolved_intent_only = $true
                }
                if (-not $workflowPathsForSync.Contains($boundWorkflow)) { [void]$workflowPathsForSync.Add($boundWorkflow) }
                continue
            }
            throw "Batch has transaction or execution artifacts but no batch_scope.json; manual recovery is required: $($batchDir.FullName)"
        }
        $scope = Read-JsonFile $scopePath "Batch Scope"
        $batchPatchIds = @($scope.patch_ids)
        if (@($scope.selected_task_ids).Count -gt 0) { $batchSelectedTaskIds = @($scope.selected_task_ids) }
        $boundRevision = Get-FullPath ([string]$scope.revision_ledger_file) $script:RoundPath
        [void](Assert-PathInRoundRoot $boundWorkflow "Batch-bound Workflow Ledger")
        [void](Assert-PathInRoundRoot $boundRevision "Batch-bound RevisionLedger")
        [void](Assert-OrdinaryPath $boundWorkflow "Batch-bound Workflow Ledger")
        [void](Assert-OrdinaryPath $boundRevision "Batch-bound RevisionLedger")
        if (-not (Test-Path -LiteralPath $boundWorkflow -PathType Leaf) -or
            -not (Test-Path -LiteralPath $boundRevision -PathType Leaf)) {
            throw "Batch-bound Workflow/RevisionLedger is missing for $($batchDir.FullName)."
        }
        Recover-WorkflowSync -WorkflowFile $boundWorkflow -FallbackRevisionFile $boundRevision -Label "recover sync for batch $($intent.batch_id)"
        if (-not $workflowPathsForSync.Contains($boundWorkflow)) { [void]$workflowPathsForSync.Add($boundWorkflow) }

        $recoveryIntentPath = Join-Path $batchDir.FullName "batch_recovery_intent.json"
        $recoveryReceiptPath = Join-Path $batchDir.FullName "batch_recovered.json"
        $publishIntentPath = Join-Path $batchDir.FullName "batch_publish_intent.json"
        $publishReceiptPath = Join-Path $batchDir.FullName "batch_published.json"
        $rollbackIntentPath = Join-Path $batchDir.FullName "batch_rollback_intent.json"
        $rollbackReceiptPath = Join-Path $batchDir.FullName "batch_rollback.json"
        foreach ($controlPath in @($recoveryIntentPath,$recoveryReceiptPath,$publishIntentPath,$publishReceiptPath,$rollbackIntentPath,$rollbackReceiptPath)) {
            [void](Assert-OrdinaryPath $controlPath "Batch transaction control")
        }
        $hasRecoveryControl = (Test-Path -LiteralPath $recoveryIntentPath -PathType Leaf) -or (Test-Path -LiteralPath $recoveryReceiptPath -PathType Leaf)
        $hasPublishControl = (Test-Path -LiteralPath $publishIntentPath -PathType Leaf) -or (Test-Path -LiteralPath $publishReceiptPath -PathType Leaf)
        $hasRollbackControl = (Test-Path -LiteralPath $rollbackIntentPath -PathType Leaf) -or (Test-Path -LiteralPath $rollbackReceiptPath -PathType Leaf)
        if ($hasRecoveryControl -and ($hasPublishControl -or $hasRollbackControl)) {
            throw "Conflicting failed-batch recovery and publication/rollback controls exist for batch $($intent.batch_id); manual investigation is required."
        }

        if ($hasRecoveryControl) {
            $reason = "Recovered from persisted batch recovery intent."
            if (Test-Path -LiteralPath $recoveryIntentPath -PathType Leaf) {
                $reason = [string](Read-JsonFile $recoveryIntentPath "Batch Recovery Intent").reason
            }
            Invoke-CoReviewerCli @("recover-failed-batch","--workflow-ledger",$boundWorkflow,"--revision-ledger",$boundRevision,"--batch-dir",$batchDir.FullName,"--reason",$reason,"--actor","Co_Reviewer Round Reconciler") "reconcile failed batch $($intent.batch_id)"
        }

        if ($hasPublishControl) {
            Invoke-CoReviewerCli @("publish-batch","--workflow-ledger",$boundWorkflow,"--revision-ledger",$boundRevision,"--batch-dir",$batchDir.FullName) "reconcile publication $($intent.batch_id)"
        }

        if ($hasRollbackControl) {
            $reason = "Resume persisted batch rollback."
            if (Test-Path -LiteralPath $rollbackIntentPath -PathType Leaf) {
                $reason = [string](Read-JsonFile $rollbackIntentPath "Batch Rollback Intent").reason
            }
            Invoke-CoReviewerCli @("rollback-batch","--workflow-ledger",$boundWorkflow,"--batch-dir",$batchDir.FullName,"--reason",$reason) "reconcile rollback $($intent.batch_id)"
        }

        $publishedReceiptValue = $null
        $rolledBackReceiptValue = $null
        $recoveredReceiptValue = $null
        $supersededReceiptValue = $null
        $unpublishedStage = "prepared_or_unpublished"
        if (Test-Path -LiteralPath (Join-Path $batchDir.FullName "verification_report.json") -PathType Leaf) {
            $unpublishedStage = "verified_unpublished"
        } elseif (Test-Path -LiteralPath (Join-Path $batchDir.FullName "apply_report.json") -PathType Leaf) {
            $unpublishedStage = "applied_unpublished"
        } elseif (Test-Path -LiteralPath (Join-Path $batchDir.FullName "revision_plan.approved.json") -PathType Leaf) {
            $unpublishedStage = "approved_unapplied"
        }
        if (Test-Path -LiteralPath $publishReceiptPath -PathType Leaf) { $publishedReceiptValue = $publishReceiptPath }
        if (Test-Path -LiteralPath $rollbackReceiptPath -PathType Leaf) { $rolledBackReceiptValue = $rollbackReceiptPath }
        if (Test-Path -LiteralPath $recoveryReceiptPath -PathType Leaf) { $recoveredReceiptValue = $recoveryReceiptPath }
        if (Test-Path -LiteralPath $supersededReceiptPath -PathType Leaf) { $supersededReceiptValue = $supersededReceiptPath }
        $batchTransactions += [pscustomobject]@{
            batch_id = [string]$intent.batch_id
            batch_directory = $batchDir.FullName
            selected_task_ids = $batchSelectedTaskIds
            patch_ids = $batchPatchIds
            workflow_ledger = $boundWorkflow
            revision_ledger = $boundRevision
            published_receipt = $publishedReceiptValue
            rolled_back_receipt = $rolledBackReceiptValue
            recovered_receipt = $recoveredReceiptValue
            superseded_receipt = $supersededReceiptValue
            unpublished_stage = $unpublishedStage
            unresolved_intent_only = $false
        }
    }
}

$workflow = Read-JsonFile $script:WorkflowPath "Current Workflow Ledger"
if ([string]$workflow.revision_round_id -ne $roundId) {
    throw "Current Workflow Ledger round ID differs from round_manifest.json."
}
if ($workflow.active_documents) {
    foreach ($pointerProperty in $workflow.active_documents.PSObject.Properties) {
        $pointer = $pointerProperty.Value
        if (-not $pointer -or [string]::IsNullOrWhiteSpace([string]$pointer.file)) {
            throw "Current Workflow active-document pointer '$($pointerProperty.Name)' has no file path."
        }
        $activeFile = Get-FullPath ([string]$pointer.file) $script:RoundPath
        [void](Assert-PathInRoundRoot $activeFile "Active document $($pointerProperty.Name)")
        if (-not (Test-Path -LiteralPath $activeFile -PathType Leaf) -or
            (Get-Sha256 $activeFile) -ne [string]$pointer.sha256) {
            throw "Current Workflow active-document pointer '$($pointerProperty.Name)' is missing or hash-stale: $activeFile"
        }
    }
}
$revisionSha = $null
if (Test-Path -LiteralPath $script:RevisionPath -PathType Leaf) {
    $currentRevision = Read-JsonFile $script:RevisionPath "Current RevisionLedger"
    if ([string]$currentRevision.revision_round_id -ne $roundId) {
        throw "Current RevisionLedger round ID differs from round_manifest.json."
    }
    foreach ($document in @($currentRevision.documents)) {
        [void](Assert-PathInRoundRoot ([string]$document.input_file) "RevisionDocument input $($document.role)")
        [void](Assert-PathInRoundRoot ([string]$document.output_file) "RevisionDocument output $($document.role)")
        if ($document.working_clean_file) {
            [void](Assert-PathInRoundRoot ([string]$document.working_clean_file) "Working Clean $($document.role)")
        }
    }
    $revisionSha = Get-Sha256 $script:RevisionPath
}
$workflowSyncHistory = @()
foreach ($workflowCandidate in $workflowPathsForSync) {
    $workflowFile = [System.IO.Path]::GetFullPath([string]$workflowCandidate)
    [void](Assert-PathInRoundRoot $workflowFile "Workflow sync source")
    $workflowLeaf = Split-Path -Leaf $workflowFile
    $pendingPath = Join-Path (Split-Path -Parent $workflowFile) ("." + $workflowLeaf + ".sync-pending.json")
    if (Test-Path -LiteralPath $pendingPath -PathType Leaf) {
        [void](Assert-OrdinaryPath $pendingPath "Pending Workflow Sync Marker")
        $pending = Read-JsonFile $pendingPath "Pending Workflow Sync Marker"
        $pendingRevision = Get-FullPath ([string]$pending.revision_ledger_file) $script:RoundPath
        [void](Assert-PathInRoundRoot $pendingRevision "Pending Workflow sync RevisionLedger")
        if (-not (Test-Path -LiteralPath $pendingRevision -PathType Leaf)) {
            throw "Pending Workflow sync RevisionLedger is missing: $pendingRevision"
        }
        Invoke-CoReviewerCli @("recover-workflow-sync","--workflow-ledger",$workflowFile,"--revision-ledger",$pendingRevision) "recover pending sync transaction for $workflowLeaf"
    }
    $syncRoot = Join-Path (Split-Path -Parent $workflowFile) ".workflow_sync_transactions"
    if (-not (Test-Path -LiteralPath $syncRoot -PathType Container)) { continue }
    [void](Assert-OrdinaryPath $syncRoot "Workflow sync transaction root")
    foreach ($stage in (Get-ChildItem -LiteralPath $syncRoot -Directory -Force | Sort-Object Name)) {
        [void](Assert-OrdinaryPath $stage.FullName "Workflow sync transaction directory")
        $intentPath = Join-Path $stage.FullName "workflow_sync_intent.json"
        [void](Assert-OrdinaryPath $intentPath "Workflow sync intent")
        $receiptPath = Join-Path $stage.FullName "workflow_sync_receipt.json"
        if (-not (Test-Path -LiteralPath $intentPath -PathType Leaf)) {
            $workflowSyncHistory += [pscustomobject]@{
                sync_id = $stage.Name
                workflow_ledger = $workflowFile
                transaction_status = "orphaned_stage_without_intent"
                intent_file = $null
                receipt_file = $null
                receipt_sha256 = $null
            }
            continue
        }
        $syncIntent = Read-JsonFile $intentPath "Workflow Sync Intent"
        if ([string]$syncIntent.sync_id -ne $stage.Name -or
            [string]$syncIntent.schema_version -ne "1.0" -or
            [System.IO.Path]::GetFullPath([string]$syncIntent.workflow_ledger_file) -ne $workflowFile) {
            throw "Workflow sync intent identity/path mismatch in $($stage.FullName)"
        }
        $workflowAfter = Get-FullPath ([string]$syncIntent.workflow_after_file) $script:RoundPath
        $revisionAfter = Get-FullPath ([string]$syncIntent.revision_after_file) $script:RoundPath
        $syncRevisionFile = Get-FullPath ([string]$syncIntent.revision_ledger_file) $script:RoundPath
        [void](Assert-PathInRoundRoot $workflowAfter "Workflow sync after-image")
        [void](Assert-PathInRoundRoot $revisionAfter "RevisionLedger sync after-image")
        [void](Assert-PathInRoundRoot $syncRevisionFile "Workflow sync RevisionLedger")
        foreach ($syncPath in @($workflowAfter,$revisionAfter,$syncRevisionFile)) {
            [void](Assert-OrdinaryPath $syncPath "Workflow sync source/after-image")
        }
        if ([System.IO.Path]::GetDirectoryName($workflowAfter) -ne [System.IO.Path]::GetFullPath($stage.FullName) -or
            [System.IO.Path]::GetDirectoryName($revisionAfter) -ne [System.IO.Path]::GetFullPath($stage.FullName) -or
            -not (Test-Path -LiteralPath $workflowAfter -PathType Leaf) -or
            -not (Test-Path -LiteralPath $revisionAfter -PathType Leaf) -or
            (Get-Sha256 $workflowAfter) -ne [string]$syncIntent.workflow_after_sha256 -or
            (Get-Sha256 $revisionAfter) -ne [string]$syncIntent.revision_after_sha256) {
            throw "Workflow sync staged after-images are missing or hash-invalid: $($stage.FullName)"
        }
        $syncStatus = "prepared_uncommitted"
        $receiptSha = $null
        if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
            [void](Assert-OrdinaryPath $receiptPath "Workflow Sync Receipt")
            $syncReceipt = Read-JsonFile $receiptPath "Workflow Sync Receipt"
            if ([string]$syncReceipt.status -ne "committed" -or
                [string]$syncReceipt.sync_id -ne [string]$syncIntent.sync_id -or
                [string]$syncReceipt.intent_sha256 -ne (Get-Sha256 $intentPath) -or
                [System.IO.Path]::GetFullPath([string]$syncReceipt.intent_file) -ne [System.IO.Path]::GetFullPath($intentPath) -or
                [string]$syncReceipt.workflow_ledger_sha256 -ne [string]$syncIntent.workflow_after_sha256 -or
                [string]$syncReceipt.revision_ledger_sha256 -ne [string]$syncIntent.revision_after_sha256 -or
                [System.IO.Path]::GetFullPath([string]$syncReceipt.workflow_ledger_file) -ne $workflowFile -or
                [System.IO.Path]::GetFullPath([string]$syncReceipt.revision_ledger_file) -ne [System.IO.Path]::GetFullPath([string]$syncIntent.revision_ledger_file)) {
                throw "Workflow sync receipt does not match its immutable intent: $receiptPath"
            }
            $syncStatus = "committed"
            $receiptSha = Get-Sha256 $receiptPath
        }
        elseif (Test-Path -LiteralPath $pendingPath -PathType Leaf) {
            $syncStatus = "pending_recovery"
        }
        $workflowSyncHistory += [pscustomobject]@{
            sync_id = [string]$syncIntent.sync_id
            workflow_ledger = $workflowFile
            revision_ledger = [string]$syncIntent.revision_ledger_file
            intent_file = $intentPath
            intent_sha256 = Get-Sha256 $intentPath
            receipt_file = if ($syncStatus -eq "committed") { $receiptPath } else { $null }
            receipt_sha256 = $receiptSha
            workflow_after_sha256 = [string]$syncIntent.workflow_after_sha256
            revision_after_sha256 = [string]$syncIntent.revision_after_sha256
            transaction_status = $syncStatus
        }
    }
}
$manifestWorkflowSha = Get-Sha256 $script:WorkflowPath
$historyById = @{}
foreach ($row in @($manifest.batch_transaction_history)) {
    if ($row -and $row.batch_id) { $historyById[[string]$row.batch_id] = $row }
}
foreach ($transaction in $batchTransactions) {
    $entry = $historyById[[string]$transaction.batch_id]
    if (-not $entry) {
        $entry = [pscustomobject]@{ batch_id = $transaction.batch_id }
    }
    $publishedReceiptSha = $null
    $rolledBackReceiptSha = $null
    $recoveredReceiptSha = $null
    $supersededReceiptSha = $null
    if ($transaction.published_receipt) { $publishedReceiptSha = Get-Sha256 $transaction.published_receipt }
    if ($transaction.rolled_back_receipt) { $rolledBackReceiptSha = Get-Sha256 $transaction.rolled_back_receipt }
    if ($transaction.recovered_receipt) { $recoveredReceiptSha = Get-Sha256 $transaction.recovered_receipt }
    if ($transaction.superseded_receipt) { $supersededReceiptSha = Get-Sha256 $transaction.superseded_receipt }
    $transactionStatus = "prepared_or_unpublished"
    if ($transaction.recovered_receipt) { $transactionStatus = "recovered" }
    if ($transaction.published_receipt) { $transactionStatus = "published" }
    if ($transaction.rolled_back_receipt) { $transactionStatus = "rolled_back" }
    if ($transaction.superseded_receipt) { $transactionStatus = "superseded_prepared" }
    if ($transaction.unresolved_intent_only) { $transactionStatus = "intent_only_recovery_required" }
    $hasTerminalReceipt = $transaction.published_receipt -or
        $transaction.rolled_back_receipt -or
        $transaction.recovered_receipt -or
        $transaction.superseded_receipt
    if (-not $hasTerminalReceipt -and -not $transaction.unresolved_intent_only) {
        if ($transaction.unpublished_stage -eq "verified_unpublished") { $transactionStatus = "verified_unpublished" }
        elseif ($transaction.unpublished_stage -eq "applied_unpublished") { $transactionStatus = "applied_unpublished" }
        elseif ($transaction.unpublished_stage -eq "approved_unapplied") { $transactionStatus = "approved_unapplied" }
    }
    $entry | Add-Member -Force -NotePropertyMembers @{
        batch_directory = $transaction.batch_directory
        selected_task_ids = @($transaction.selected_task_ids)
        patch_ids = @($transaction.patch_ids)
        workflow_ledger = $transaction.workflow_ledger
        revision_ledger = $transaction.revision_ledger
        published_receipt = $transaction.published_receipt
        published_receipt_sha256 = $publishedReceiptSha
        rolled_back_receipt = $transaction.rolled_back_receipt
        rolled_back_receipt_sha256 = $rolledBackReceiptSha
        recovered_receipt = $transaction.recovered_receipt
        recovered_receipt_sha256 = $recoveredReceiptSha
        superseded_receipt = $transaction.superseded_receipt
        superseded_receipt_sha256 = $supersededReceiptSha
        unresolved_intent_only = $transaction.unresolved_intent_only
        unpublished_stage = $transaction.unpublished_stage
        transaction_status = $transactionStatus
    }
    $historyById[[string]$transaction.batch_id] = $entry
}
$history = @($historyById.Values | Sort-Object batch_id)
$manifest | Add-Member -Force -NotePropertyMembers @{
    current_workflow_ledger = $script:WorkflowPath
    current_revision_ledger = $script:RevisionPath
    revision_ledger = $script:RevisionPath
    workflow_ledger_sha256 = $manifestWorkflowSha
    revision_ledger_sha256 = $revisionSha
    runs_directory = $runsPath
    active_documents = $workflow.active_documents
    batch_transaction_history = $history
    workflow_sync_history = $workflowSyncHistory
    reconciled_at_utc = (Get-Date).ToUniversalTime().ToString("o")
}

# A recorded release path is also a resumable transaction. If an intent exists,
# the CLI either completes its missing artifacts or verifies the immutable receipt.
$releaseDirValue = [string]$manifest.release_directory
if ($ResumeReleasePackage -and -not [string]::IsNullOrWhiteSpace($releaseDirValue)) {
    $releaseDir = Get-FullPath $releaseDirValue $script:RoundPath
    [void](Assert-PathInRoundRoot $releaseDir "Release Package")
    [void](Assert-OrdinaryPath $releaseDir "Release Package directory")
    $releaseIntentPath = Join-Path $releaseDir "release_package_intent.json"
    [void](Assert-OrdinaryPath $releaseIntentPath "Release Package intent")
    if (Test-Path -LiteralPath $releaseIntentPath -PathType Leaf) {
        $releaseIntent = Read-JsonFile $releaseIntentPath "Release Package Intent"
        Invoke-CoReviewerCli @(
            "create-release-package",
            "--workflow-ledger",$script:WorkflowPath,
            "--revision-ledger",$script:RevisionPath,
            "--out-dir",$releaseDir,
            "--manuscript-title",[string]$releaseIntent.manuscript_title,
            "--author",[string]$releaseIntent.author
        ) "resume or verify Release Package"
        $releaseReceiptPath = Join-Path $releaseDir "release_package_receipt.json"
        $releaseReceiptSha = $null
        if (Test-Path -LiteralPath $releaseReceiptPath -PathType Leaf) {
            $releaseReceiptSha = Get-Sha256 $releaseReceiptPath
        }
        $manifest | Add-Member -Force -NotePropertyMembers @{
            release_package_intent = $releaseIntentPath
            release_package_receipt = $releaseReceiptPath
            release_package_receipt_sha256 = $releaseReceiptSha
        }
    }
}

$reconcileLog = [pscustomobject]@{
    timestamp_utc = (Get-Date).ToUniversalTime().ToString("o")
    workflow_ledger = $script:WorkflowPath
    workflow_ledger_sha256 = Get-Sha256 $script:WorkflowPath
    revision_ledger = $script:RevisionPath
    revision_ledger_sha256 = $revisionSha
    batch_transaction_count = $batchTransactions.Count
}
$logs = @($manifest.reconciliation_history)
$logs += $reconcileLog
$manifest | Add-Member -Force -NotePropertyName reconciliation_history -NotePropertyValue $logs
Write-RoundManifestAtomic -Manifest $manifest -Path $manifestPath

Write-Host "[ROUND RECONCILED] $roundId"
Write-Host "[WORKFLOW LEDGER] $script:WorkflowPath"
Write-Host "[REVISION LEDGER] $script:RevisionPath"
Write-Host "[BATCH TRANSACTIONS] $($batchTransactions.Count)"
Write-Host "[WORKFLOW SYNC TRANSACTIONS] $($workflowSyncHistory.Count)"
Write-Host "[ROUND MANIFEST] $manifestPath"
