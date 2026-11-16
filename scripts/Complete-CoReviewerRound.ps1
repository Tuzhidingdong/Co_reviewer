[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RoundRoot,
    [Parameter(Mandatory = $true)][string]$WorkflowLedger,
    [Parameter(Mandatory = $true)][string]$RevisionLedger,
    [string]$WordPdfOutputDirectory,
    [string]$ManualLocationsJson,
    [string]$ManualLocationMapOut,
    [string]$ReleaseOutputDirectory,
    [string]$ManuscriptTitle = "",
    [string]$Author = "",
    [string]$Actor = "Human Author",
    [string]$PythonExe = "python.exe"
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
    $candidate = if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $roundPath $Path }
    $full = [System.IO.Path]::GetFullPath($candidate)
    $prefix = [System.IO.Path]::GetFullPath($roundPath).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must remain inside the review-round workspace '$roundPath': $full"
    }
    return $full
}
function Update-RoundManifest {
    param([string]$EventType, [hashtable]$Event)
    $manifestPath = Join-Path $roundPath "round_manifest.json"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($entry in $Event.GetEnumerator()) {
        $manifest | Add-Member -Force -NotePropertyName $entry.Key -NotePropertyValue $entry.Value
    }
    $events = @($manifest.finalization_history)
    $events += [pscustomobject]@{
        event_type = $EventType
        timestamp_utc = (Get-Date).ToUniversalTime().ToString("o")
        workflow_ledger = $workflowPath
        revision_ledger = $revisionPath
        word_pdf_directory = $script:WordPdfPath
        release_directory = $script:ReleasePath
        details = $Event
    }
    if ($manifest.PSObject.Properties["finalization_history"]) {
        $manifest.finalization_history = $events
    }
    else {
        $manifest | Add-Member -NotePropertyName finalization_history -NotePropertyValue $events
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
function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
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
        throw "Co_Reviewer CLI failed with exit code $exitCode. Preserve the round and partial outputs; do not overwrite or retry those artifacts."
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

$roundPath = (Resolve-Path -LiteralPath $RoundRoot -ErrorAction Stop).Path
$roundManifestPath = Join-Path $roundPath "round_manifest.json"
if (-not (Test-Path -LiteralPath $roundManifestPath -PathType Leaf)) {
    throw "RoundRoot does not contain round_manifest.json: $roundPath"
}
$roundManifest = Read-JsonFile $roundManifestPath "round_manifest"
$roundId = [string]$roundManifest.review_round_id
if ([string]::IsNullOrWhiteSpace($roundId)) {
    throw "round_manifest.json has no review_round_id."
}
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:CliPath = Join-Path $repoRoot "scripts\co_reviewer_cli.py"
if (-not (Test-Path -LiteralPath $script:CliPath -PathType Leaf)) { throw "CLI not found: $script:CliPath" }
$script:PythonResolved = Resolve-Python $PythonExe
$script:WordPdfPath = $null
$script:ReleasePath = $null

$workflowPath = Resolve-ExistingFile $WorkflowLedger "Workflow Ledger" $roundPath
$revisionPath = Resolve-ExistingFile $RevisionLedger "RevisionLedger" $roundPath
[void](Assert-PathInRoundRoot $workflowPath "Workflow Ledger")
[void](Assert-PathInRoundRoot $revisionPath "RevisionLedger")
if ($workflowPath -eq $revisionPath) { throw "Workflow Ledger and RevisionLedger must be different files." }

$workflow = Read-JsonFile $workflowPath "Workflow Ledger"
$ledger = Read-JsonFile $revisionPath "RevisionLedger"
if ([string]$workflow.revision_round_id -ne [string]$ledger.revision_round_id -or
    [string]$workflow.revision_round_id -ne $roundId) {
    throw "Workflow Ledger, RevisionLedger and round_manifest.json do not belong to the same review round."
}
foreach ($pointerProperty in $workflow.active_documents.PSObject.Properties) {
    $pointer = $pointerProperty.Value
    if (-not $pointer -or [string]::IsNullOrWhiteSpace([string]$pointer.file)) {
        throw "Workflow active-document pointer '$($pointerProperty.Name)' is missing its file."
    }
    $activePath = Assert-PathInRoundRoot ([string]$pointer.file) "Active document $($pointerProperty.Name)"
    if (-not (Test-Path -LiteralPath $activePath -PathType Leaf) -or (Get-Sha256 $activePath) -ne [string]$pointer.sha256) {
        throw "Workflow active-document pointer '$($pointerProperty.Name)' is missing or hash-stale: $activePath"
    }
}
foreach ($document in $ledger.documents) {
    [void](Assert-PathInRoundRoot ([string]$document.input_file) "RevisionDocument input $($document.role)")
    [void](Assert-PathInRoundRoot ([string]$document.output_file) "RevisionDocument output $($document.role)")
    if ($document.working_clean_file) {
        [void](Assert-PathInRoundRoot ([string]$document.working_clean_file) "Working Clean $($document.role)")
    }
}
foreach ($patch in $ledger.patches) {
    foreach ($operation in $patch.operations) {
        [void](Assert-PathInRoundRoot ([string]$operation.input_file) "PatchOperation input")
        [void](Assert-PathInRoundRoot ([string]$operation.output_file) "PatchOperation output")
    }
}
foreach ($unit in $ledger.response_units) {
    if ($unit.location_map_file) {
        [void](Assert-PathInRoundRoot ([string]$unit.location_map_file) "ResponseUnit location map")
    }
    foreach ($location in $unit.final_locations) {
        [void](Assert-PathInRoundRoot ([string]$location.output_file) "Final Word DOCX for $($unit.response_unit_id)")
        if ($location.pdf_file) {
            [void](Assert-PathInRoundRoot ([string]$location.pdf_file) "Final PDF for $($unit.response_unit_id)")
        }
    }
}
$pendingPatches = @($ledger.patches | Where-Object { $_.status -notin @("verified", "superseded") })
if ($pendingPatches.Count -gt 0) {
    throw "Finalization is blocked by non-verified Patch(es): $((@($pendingPatches | ForEach-Object { $_.patch_id })) -join ', ')"
}
if (@($ledger.response_units).Count -eq 0) { throw "RevisionLedger contains no ResponseUnits." }
Update-RoundManifest -EventType "finalization_started" -Event @{
    current_workflow_ledger = $workflowPath
    revision_ledger = $revisionPath
    workflow_ledger_sha256 = (Get-Sha256 $workflowPath)
    revision_ledger_sha256 = (Get-Sha256 $revisionPath)
}
$blockedUnits = @($ledger.response_units | Where-Object { $_.disposition -eq "clarification_needed" -or $_.status -eq "blocked" })
if ($blockedUnits.Count -gt 0) {
    throw "Finalization is blocked by clarification-needed ResponseUnit(s): $((@($blockedUnits | ForEach-Object { $_.response_unit_id })) -join ', '). Resolve them through the Workflow before release."
}

# Reuse a full, current Working Clean set. Create only when none exists; never overwrite
# partial or stale projections left by an interrupted operation.
$cleanExists = 0
$cleanCurrent = 0
foreach ($document in $ledger.documents) {
    $markedPath = [string]$document.output_file
    if (-not (Test-Path -LiteralPath $markedPath -PathType Leaf)) {
        throw "Final Marked DOCX is missing for $($document.role): $markedPath"
    }
    $markedSha = Get-Sha256 $markedPath
    if ($document.output_sha256 -and $markedSha -ne [string]$document.output_sha256) {
        throw "Final Marked DOCX hash differs from RevisionLedger for $($document.role): $markedPath"
    }
    $cleanPath = [string]$document.working_clean_file
    if ($cleanPath -and (Test-Path -LiteralPath $cleanPath -PathType Leaf)) {
        $cleanExists++
        if ($document.working_clean_sha256 -and
            (Get-Sha256 $cleanPath) -eq [string]$document.working_clean_sha256 -and
            -not [string]::IsNullOrWhiteSpace([string]$document.working_view_fingerprint)) {
            $cleanCurrent++
        }
    }
}
$totalDocuments = @($ledger.documents).Count
$locationRequiredUnits = @($ledger.response_units | Where-Object { $_.location_requirement -ne "not_required" })
if ($totalDocuments -eq 0) {
    if ($locationRequiredUnits.Count -gt 0) {
        throw "Location-required ResponseUnits exist, but RevisionLedger has no registered document roles. The Workflow must bind each no-change anchor to its current active document before finalization."
    }
    Write-Host "[RESPONSE-ONLY ROUND] No document role or final page/line map is required; every ResponseUnit uses location_requirement=not_required."
}
else {
    if ($cleanExists -eq 0) {
        [void](Invoke-CoReviewerCli @("create-working-clean-views", "--ledger", $revisionPath, "--actor", "Co_Reviewer Finalization"))
        $ledger = Read-JsonFile $revisionPath "RevisionLedger after Working Clean"
    }
    elseif ($cleanExists -eq $totalDocuments -and $cleanCurrent -eq $totalDocuments) {
        Write-Host "[WORKING CLEAN] Existing complete projection set is hash-current; reusing it."
    }
    else {
        throw "Working Clean projections are partial or stale ($cleanCurrent current / $totalDocuments roles). Preserve them and inspect the RevisionLedger; no overwrite was attempted."
    }
}

$ledger = Read-JsonFile $revisionPath "RevisionLedger"
Write-Host ""
Write-Host "========== FINAL MARKED / WORKING CLEAN FILES =========="
foreach ($document in $ledger.documents) {
    Write-Host ("ROLE: " + $document.role)
    Write-Host ("MARKED: " + $document.output_file + " | SHA256 " + (Get-Sha256 $document.output_file))
    Write-Host ("CLEAN:  " + $document.working_clean_file + " | SHA256 " + (Get-Sha256 $document.working_clean_file))
}
Write-Host "========================================================"
$semanticPending = @($ledger.response_units | Where-Object { $_.semantic_review_confirmed -ne $true })
if ($semanticPending.Count -gt 0) {
    $semanticIds = @($semanticPending | ForEach-Object { [string]$_.response_unit_id })
    Confirm-Exact "Inspect the final accepted Manuscript/SI text and corresponding Marked files. Confirm scientific meaning, numerical values, units, sample identities, citations, figures/tables and cross-document consistency for these ResponseUnits." ("SEMANTIC REVIEW " + ($semanticIds -join ","))
    [void](Invoke-CoReviewerCli ((@("confirm-response-units", "--ledger", $revisionPath, "--ids") + $semanticIds) + @("--semantic-review-confirmed", "--actor", $Actor)))
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$wordPdfPath = $null
$locationMapPath = $null
if ($totalDocuments -gt 0) {
$manifestWordPdfPath = [string]$roundManifest.word_pdf_output_directory
$wordPdfPath = if ($WordPdfOutputDirectory) {
    Get-FullPath $WordPdfOutputDirectory $roundPath
} elseif (-not [string]::IsNullOrWhiteSpace($manifestWordPdfPath)) {
    Get-FullPath $manifestWordPdfPath $roundPath
} else {
    Join-Path $roundPath "output\word_pdf_final-$stamp"
}
[void](Assert-PathInRoundRoot $wordPdfPath "Word/PDF output directory")
$script:WordPdfPath = $wordPdfPath
$locationMapPath = Join-Path $wordPdfPath "response_location_map.json"
Update-RoundManifest -EventType "word_pdf_mapping_started" -Event @{
    current_workflow_ledger = $workflowPath
    revision_ledger = $revisionPath
    word_pdf_output_directory = $wordPdfPath
}
if (Test-Path -LiteralPath $locationMapPath -PathType Leaf) {
    $ledger = Read-JsonFile $revisionPath "RevisionLedger before location-map resume"
    $map = Read-JsonFile $locationMapPath "Response Location Map"
    $mapSha = Get-Sha256 $locationMapPath
    $mapRows = @($map.response_units)
    $mapRowsRegistered = $true
    foreach ($mapRow in $mapRows) {
        $unit = @($ledger.response_units | Where-Object { $_.response_unit_id -eq $mapRow.response_unit_id }) | Select-Object -First 1
        if (-not $unit -or
            -not $unit.location_map_file -or
            [System.IO.Path]::GetFullPath([string]$unit.location_map_file) -ne [System.IO.Path]::GetFullPath($locationMapPath) -or
            $unit.location_map_file_sha256 -ne $mapSha -or
            $unit.location_map_fingerprint -ne $mapRow.location_map_fingerprint) {
            $mapRowsRegistered = $false
            break
        }
    }
    if ($mapRowsRegistered) {
        Write-Host "[WORD/PDF MAP] Every automatic locator row in the existing map is already hash-bound in the RevisionLedger; reusing it."
    }
    else {
        $ledgerSha = Get-Sha256 $revisionPath
        if ([string]$map.revision_ledger_sha256 -ne $ledgerSha) {
            throw "A partial Word/PDF map exists but is not bound to the current RevisionLedger. Preserve it and choose a new WordPdfOutputDirectory."
        }
        if ($mapRows.Count -gt 0) {
            [void](Invoke-CoReviewerCli @("record-response-locations", "--ledger", $revisionPath, "--location-map", $locationMapPath, "--actor", "Co_Reviewer Location Recovery"))
        }
    }
}
else {
    if (Test-Path -LiteralPath $wordPdfPath -PathType Container) {
        if (@(Get-ChildItem -LiteralPath $wordPdfPath -Force).Count -gt 0) {
            throw "Word/PDF directory contains partial files but no response_location_map.json. Preserve it and choose a new directory: $wordPdfPath"
        }
    }
    [void](Invoke-CoReviewerCli @("map-response-locations", "--ledger", $revisionPath, "--out-dir", $wordPdfPath, "--actor", "Microsoft Word COM/PDF Mapper"))
    if (-not (Test-Path -LiteralPath $locationMapPath -PathType Leaf)) {
        throw "Word/PDF mapping returned success but response_location_map.json is missing: $locationMapPath"
    }
}
$ledger = Read-JsonFile $revisionPath "RevisionLedger after Word/PDF mapping"

$manualPending = @($ledger.response_units | Where-Object {
    $_.location_requirement -eq "manual_required" -and $_.status -notin @("location_mapped", "completed")
})
if ($manualPending.Count -gt 0) {
    if (-not $ManualLocationsJson) {
        Write-Host ""
        Write-Host "[MANUAL LOCATION REVIEW REQUIRED]"
        foreach ($unit in $manualPending) {
            Write-Host ("UNIT: " + $unit.response_unit_id + " | disposition=" + $unit.disposition + " | reviewer=" + $unit.reviewer)
            Write-Host ("COMMENT: " + $unit.comment_text)
            if ($unit.disposition -eq "revised") {
                foreach ($operationId in $unit.linked_operation_ids) {
                    Write-Host ('EXPECTED JSON: { "response_unit_id": "' + $unit.response_unit_id + '", "operation_id": "' + $operationId + '", "locator_text": "<author-confirmed exact table/non-body locator>" }')
                }
            } else {
                $anchorLocatorId = "anchor:" + [string]$unit.anchor_evidence_id
                Write-Host ("ANCHOR EVIDENCE ID: " + $unit.anchor_evidence_id)
                Write-Host ('EXPECTED JSON: { "response_unit_id": "' + $unit.response_unit_id + '", "locator_id": "' + $anchorLocatorId + '", "locator_text": "<author-confirmed exact table/non-body locator>" }')
            }
        }
        Write-Host ("WORD MAP: " + $locationMapPath)
        Update-RoundManifest -EventType "manual_locations_required" -Event @{
            current_workflow_ledger = $workflowPath
            revision_ledger = $revisionPath
            word_pdf_output_directory = $wordPdfPath
            response_location_map = $locationMapPath
            response_location_map_sha256 = (Get-Sha256 $locationMapPath)
            manual_response_unit_ids = @($manualPending | ForEach-Object { [string]$_.response_unit_id })
        }
        Write-Host "Prepare a manual locator JSON with one response_units entry per required locator, then rerun this script with -WordPdfOutputDirectory set to the same directory and -ManualLocationsJson set to that JSON. Do not guess page/line values."
        throw "[PAUSED: MANUAL_LOCATIONS_REQUIRED] The Word/PDF map is preserved. Supply author-confirmed locators and rerun the finalizer."
    }
    $manualInput = Resolve-ExistingFile $ManualLocationsJson "Manual Locations JSON" $roundPath
    [void](Assert-PathInRoundRoot $manualInput "Manual Locations JSON")
    $manualOut = if ($ManualLocationMapOut) {
        Get-FullPath $ManualLocationMapOut $roundPath
    } else {
        Join-Path $wordPdfPath "manual_location_map.$stamp.json"
    }
    [void](Assert-PathInRoundRoot $manualOut "Manual location map output")
    if (Test-Path -LiteralPath $manualOut) { throw "Refusing to overwrite existing manual location map: $manualOut" }
    [void](Invoke-CoReviewerCli @("record-manual-response-locations", "--ledger", $revisionPath, "--word-map", $locationMapPath, "--input", $manualInput, "--out", $manualOut, "--actor", $Actor))
    $ledger = Read-JsonFile $revisionPath "RevisionLedger after manual location recording"
}

}
elseif ($locationRequiredUnits.Count -gt 0) {
    throw "Location-required ResponseUnits cannot be released without registered document roles."
}
$manualStillPending = @($ledger.response_units | Where-Object {
    $_.location_requirement -eq "manual_required" -and $_.status -notin @("location_mapped", "completed")
})
if (($ManualLocationsJson -or $ManualLocationMapOut) -and $manualStillPending.Count -eq 0) {
    Write-Host "[MANUAL LOCATIONS] Every manual_required ResponseUnit is already recorded; supplied manual input is not reapplied."
}

$unmapped = @($ledger.response_units | Where-Object {
    $_.location_requirement -ne "not_required" -and $_.status -notin @("location_mapped", "completed")
})
if ($unmapped.Count -gt 0) {
    throw "Required final locations remain missing for ResponseUnit(s): $((@($unmapped | ForEach-Object { $_.response_unit_id })) -join ', '). Preserve all outputs."
}
$semanticPending = @($ledger.response_units | Where-Object { $_.semantic_review_confirmed -ne $true })
if ($semanticPending.Count -gt 0) { throw "One or more ResponseUnits still lack semantic-review confirmation." }

$toComplete = @($ledger.response_units | Where-Object { $_.status -ne "completed" } | ForEach-Object { [string]$_.response_unit_id })
if ($toComplete.Count -gt 0) {
    Confirm-Exact "All required locations and semantic reviews are recorded. This records release readiness only; it does not submit to a journal." ("READY FOR SUBMISSION " + ($toComplete -join ","))
    [void](Invoke-CoReviewerCli ((@("complete-response-units", "--ledger", $revisionPath, "--ids") + $toComplete) + @("--ready-for-submission-confirmed", "--actor", $Actor)))
}

# Response-only/no-change Workflow tasks are completed only after their ResponseUnits
# have been semantically reviewed and, when required, finally located. Synchronize
# after completion so the Release Manifest sees the actual completed task states.
[void](Invoke-CoReviewerCli @("sync-workflow-ledger", "--workflow-ledger", $workflowPath, "--revision-ledger", $revisionPath))

$releasePath = if ($ReleaseOutputDirectory) {
    Get-FullPath $ReleaseOutputDirectory $roundPath
} else {
    Join-Path $roundPath "runs\release-final-$stamp"
}
[void](Assert-PathInRoundRoot $releasePath "Release output directory")
$script:ReleasePath = $releasePath
if (Test-Path -LiteralPath $releasePath) { throw "Release directory already exists; choose a new path: $releasePath" }
[void](Invoke-CoReviewerCli @(
    "create-release-package",
    "--workflow-ledger", $workflowPath,
    "--revision-ledger", $revisionPath,
    "--out-dir", $releasePath,
    "--manuscript-title", $ManuscriptTitle,
    "--author", $Author
))

$required = @("Response_to_Reviewers.md", "Response_to_Reviewers.docx", "Response_to_Reviewers.quality.json", "final_release_manifest.json")
foreach ($name in $required) {
    $path = Join-Path $releasePath $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Release package is missing required artifact: $path" }
}
$manifestPath = Join-Path $releasePath "final_release_manifest.json"
$manifest = Read-JsonFile $manifestPath "Final Release Manifest"
if ($manifest.release_status -ne "READY" -or
    $manifest.deterministic_consistency_audit.status -ne "PASS" -or
    @($manifest.deterministic_consistency_audit.blocking_findings).Count -ne 0) {
    throw "Final manifest does not declare READY with a passing deterministic consistency audit. Preserve package: $releasePath"
}
$responseQuality = Read-JsonFile (Join-Path $releasePath "Response_to_Reviewers.quality.json") "Response Letter Quality Report"
if ($responseQuality.status -notin @("PASS", "PASS_WITH_WARNINGS")) {
    throw "Response Letter quality report is not passing: $($responseQuality.status)"
}

Write-Host ""
Write-Host "[RELEASE COMPLETE] $releasePath"
foreach ($name in $required) {
    $path = Join-Path $releasePath $name
    Write-Host ("[ARTIFACT] " + $path + " | " + (Get-Item -LiteralPath $path).Length + " bytes | SHA256 " + (Get-Sha256 $path))
}
if ($wordPdfPath) {
    Write-Host ("[WORD/PDF OUTPUT] " + $wordPdfPath)
    Write-Host ("[LOCATION MAP] " + $locationMapPath)
}
else {
    Write-Host "[WORD/PDF OUTPUT] Not required for a fully resolved reply-only round."
}
Write-Host ("[FINAL MANIFEST] " + $manifestPath)
Update-RoundManifest -EventType "release_package_created" -Event @{
    current_workflow_ledger = $workflowPath
    revision_ledger = $revisionPath
    revision_ledger_sha256 = (Get-Sha256 $revisionPath)
    word_pdf_output_directory = $wordPdfPath
    response_location_map = $locationMapPath
    release_directory = $releasePath
    final_release_manifest = $manifestPath
    final_release_manifest_sha256 = (Get-Sha256 $manifestPath)
    response_letter_markdown = Join-Path $releasePath "Response_to_Reviewers.md"
    response_letter_docx = Join-Path $releasePath "Response_to_Reviewers.docx"
}
Write-Host "[BOUNDARY] The package is locally generated and not submitted to a journal. Final Word GUI/PDF visual inspection and author approval remain separate."
