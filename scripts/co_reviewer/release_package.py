# -*- coding: utf-8 -*-
"""Immutable, content-verified Release Package assembly and commit marker.

The source Release Manifest remains the semantic release gate. This module
packages every managed source/output/evidence artifact into one immutable
directory and publishes that directory only after its package manifest has
been validated and an external commit marker is written last.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

from lxml import etree as ET


class ReleasePackageTransactionError(ValueError):
    """Raised when a Release Package cannot be safely staged, committed or read."""


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_symlink_path(path: Path, label: str, *, allow_missing: bool = True) -> Path:
    requested = Path(os.path.abspath(str(path.expanduser())))
    probe = requested
    while True:
        if probe.is_symlink():
            raise ReleasePackageTransactionError(
                f"{label} path must not traverse a symbolic link: {probe}"
            )
        if probe.parent == probe:
            break
        probe = probe.parent
    if not allow_missing and not requested.exists():
        raise ReleasePackageTransactionError(f"{label} does not exist: {requested}")
    return requested.resolve()


def _safe_component(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleasePackageTransactionError(f"{label} must be non-empty.")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    if not normalized or normalized in {".", ".."}:
        raise ReleasePackageTransactionError(f"{label} is not a safe package path component.")
    return normalized[:96]


def _validate_docx_bytes(payload: bytes, label: str) -> None:
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(payload), "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ReleasePackageTransactionError(
                    f"{label} contains duplicate DOCX ZIP member names."
                )
            bad_member = archive.testzip()
            if bad_member:
                raise ReleasePackageTransactionError(
                    f"{label} contains a corrupt DOCX ZIP member: {bad_member}"
                )
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ReleasePackageTransactionError(
                    f"{label} is missing required DOCX OPC parts."
                )
            parser = ET.XMLParser(
                resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
            )
            members = {}
            for name in names:
                if name.endswith("/"):
                    continue
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts or "\\" in name:
                    raise ReleasePackageTransactionError(
                        f"{label} contains an unsafe DOCX member path: {name}"
                    )
                data = archive.read(name)
                members[name] = data
                if name.endswith((".xml", ".rels")):
                    ET.fromstring(data, parser=parser)
            from .structure_classifier import classify_docx_package_members
            structure_report = classify_docx_package_members(members, source_name=label)
            if structure_report.get("package_status") != "READY":
                raise ReleasePackageTransactionError(
                    f"{label} failed DOCX structure validation: {structure_report.get('summary', {})}"
                )
    except ReleasePackageTransactionError:
        raise
    except Exception as exc:
        raise ReleasePackageTransactionError(f"{label} is not a valid readable DOCX package: {exc}") from exc


def _record(
    records: dict[tuple[str, str], dict[str, Any]],
    *,
    file_value: Any,
    sha_value: Any,
    category: str,
    role: str | None = None,
    logical_name: str | None = None,
) -> None:
    if not isinstance(file_value, str) or not file_value.strip():
        raise ReleasePackageTransactionError(
            f"Release artifact {logical_name or category} has no non-empty source file path."
        )
    if not isinstance(sha_value, str) or not _SHA256_RE.fullmatch(sha_value):
        raise ReleasePackageTransactionError(
            f"Release artifact {logical_name or category} has an invalid SHA-256."
        )
    source = _reject_symlink_path(Path(file_value), f"Release artifact {logical_name or category}", allow_missing=False)
    if not source.is_file():
        raise ReleasePackageTransactionError(f"Release artifact is not a regular file: {source}")
    actual_sha = _sha256_file(source)
    if actual_sha != sha_value:
        raise ReleasePackageTransactionError(
            f"Release artifact changed since Manifest generation: {source}"
        )
    key = (str(source), actual_sha)
    existing = records.get(key)
    if existing is None:
        digest_path = hashlib.sha256((str(source) + "\0" + actual_sha).encode("utf-8")).hexdigest()[:20]
        basename = _safe_component(source.name, "Release artifact basename")
        role_component = _safe_component(role, "Release artifact role") if role else "package"
        category_component = _safe_component(category, "Release artifact category")
        package_path = f"artifacts/{category_component}/{role_component}/{digest_path}-{basename}"
        existing = {
            "source_file": str(source),
            "package_path": package_path,
            "sha256": actual_sha,
            "bytes": source.stat().st_size,
            "categories": [],
            "roles": [],
            "logical_names": [],
        }
        records[key] = existing
    if category not in existing["categories"]:
        existing["categories"].append(category)
    if role and role not in existing["roles"]:
        existing["roles"].append(role)
    if logical_name and logical_name not in existing["logical_names"]:
        existing["logical_names"].append(logical_name)


def _add_file_record(
    records: dict[tuple[str, str], dict[str, Any]],
    value: Any,
    *,
    category: str,
    role: str | None = None,
    logical_name: str,
) -> None:
    if not isinstance(value, Mapping):
        raise ReleasePackageTransactionError(
            f"Release Manifest record {logical_name} must be an object."
        )
    _record(
        records,
        file_value=value.get("file"),
        sha_value=value.get("sha256"),
        category=category,
        role=role,
        logical_name=logical_name,
    )


def collect_release_package_artifacts(
    release_manifest: Mapping[str, Any],
    *,
    release_manifest_path: str | Path,
    release_intent_path: str | Path,
    workflow_ledger: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Collect and hash-check every document, PDF, ledger, report and batch artifact."""
    if not isinstance(release_manifest, Mapping) or release_manifest.get("release_status") != "READY":
        raise ReleasePackageTransactionError("A READY Release Manifest is required for package assembly.")
    records: dict[tuple[str, str], dict[str, Any]] = {}

    manifest_file = _reject_symlink_path(Path(release_manifest_path), "Final Release Manifest", allow_missing=False)
    intent_file = _reject_symlink_path(Path(release_intent_path), "Release Package intent", allow_missing=False)
    _record(
        records, file_value=str(manifest_file), sha_value=_sha256_file(manifest_file),
        category="release-manifest", logical_name="final_release_manifest.json",
    )
    _record(
        records, file_value=str(intent_file), sha_value=_sha256_file(intent_file),
        category="release-control", logical_name="release_package_intent.json",
    )

    source_ledgers = release_manifest.get("source_ledgers")
    if not isinstance(source_ledgers, Mapping):
        raise ReleasePackageTransactionError("Release Manifest has no source_ledgers object.")
    for name, row in source_ledgers.items():
        _add_file_record(
            records, row, category="source-ledger",
            logical_name=f"source_ledgers/{name}",
        )

    for role, row in (release_manifest.get("active_documents") or {}).items():
        if not isinstance(row, Mapping):
            raise ReleasePackageTransactionError(f"Active document record for {role} is malformed.")
        _record(
            records, file_value=row.get("file"), sha_value=row.get("sha256"),
            category="marked-active", role=str(role), logical_name=f"active_documents/{role}",
        )

    for role, row in (release_manifest.get("marked_documents") or {}).items():
        if not isinstance(row, Mapping):
            raise ReleasePackageTransactionError(f"Marked document record for {role} is malformed.")
        _record(
            records, file_value=row.get("active_file"), sha_value=row.get("active_sha256"),
            category="marked-active", role=str(role), logical_name=f"marked_documents/{role}/active",
        )
        _record(
            records, file_value=row.get("working_clean_file"), sha_value=row.get("working_clean_sha256"),
            category="working-clean", role=str(role), logical_name=f"marked_documents/{role}/working_clean",
        )

    for role, row in (release_manifest.get("final_documents") or {}).items():
        if not isinstance(row, Mapping):
            raise ReleasePackageTransactionError(f"Final document record for {role} is malformed.")
        _record(
            records, file_value=row.get("final_clean_file"), sha_value=row.get("final_clean_sha256"),
            category="final-clean", role=str(role), logical_name=f"final_documents/{role}/clean",
        )
        _record(
            records, file_value=row.get("final_pdf_file"), sha_value=row.get("final_pdf_sha256"),
            category="final-pdf", role=str(role), logical_name=f"final_documents/{role}/pdf",
        )
        quality = row.get("quality_report")
        _add_file_record(
            records, quality, category="quality-report", role=str(role),
            logical_name=f"final_documents/{role}/quality",
        )

    for index, row in enumerate(release_manifest.get("location_maps", [])):
        _add_file_record(
            records, row, category="location-map",
            logical_name=f"location_maps/{index}",
        )

    for key, row in (release_manifest.get("artifacts") or {}).items():
        _add_file_record(
            records, row, category="response-artifact",
            logical_name=f"artifacts/{key}",
        )

    qa = release_manifest.get("qa_remediation")
    if isinstance(qa, Mapping):
        for key in (
            "pre_remediation_qa_report",
            "post_remediation_qa_report",
            "marked",
            "clean",
        ):
            row = qa.get(key)
            if isinstance(row, Mapping):
                _add_file_record(
                    records, row, category="qa-remediation",
                    logical_name=f"qa_remediation/{key}",
                )

    # Initial inputs are distinct from published versions, but the full release
    # retains both the exact original files and every active/final DOCX.
    initial_sources = workflow_ledger.get("source_files", [])
    if not isinstance(initial_sources, list):
        raise ReleasePackageTransactionError("Workflow Ledger source_files must be an array.")
    for index, row in enumerate(initial_sources):
        if not isinstance(row, Mapping):
            raise ReleasePackageTransactionError(f"Workflow initial source_files[{index}] is malformed.")
        _record(
            records,
            file_value=row.get("file"),
            sha_value=row.get("sha256"),
            category="initial-source",
            role=str(row.get("role") or "source"),
            logical_name=f"workflow.source_files[{index}]",
        )

    # Preserve complete batch and recovery directories, including intermediate
    # plans, Patch snapshots, native Marked outputs, receipts and rollback evidence.
    for collection_name in ("batches", "recovered_batches"):
        rows = release_manifest.get(collection_name, [])
        if not isinstance(rows, list):
            raise ReleasePackageTransactionError(f"Release Manifest {collection_name} must be an array.")
        for row in rows:
            if not isinstance(row, Mapping):
                raise ReleasePackageTransactionError(f"Release Manifest {collection_name} contains a malformed record.")
            batch_id = _safe_component(str(row.get("batch_id") or ""), f"{collection_name} batch ID")
            directory_value = row.get("directory")
            if not isinstance(directory_value, str) or not directory_value.strip():
                raise ReleasePackageTransactionError(
                    f"Release Manifest {collection_name} {batch_id} has no directory."
                )
            batch_dir = _reject_symlink_path(Path(directory_value), f"{collection_name} directory", allow_missing=False)
            if not batch_dir.is_dir():
                raise ReleasePackageTransactionError(f"Release batch directory is missing: {batch_dir}")
            for candidate in sorted(batch_dir.rglob("*")):
                if candidate.is_symlink():
                    raise ReleasePackageTransactionError(
                        f"Release batch history contains a symbolic link: {candidate}"
                    )
                if not candidate.is_file():
                    continue
                relative = candidate.relative_to(batch_dir).as_posix()
                _record(
                    records,
                    file_value=str(candidate),
                    sha_value=_sha256_file(candidate),
                    category=f"{collection_name}-history",
                    role=batch_id,
                    logical_name=f"{collection_name}/{batch_id}/{relative}",
                )

    for value in records.values():
        value["categories"].sort()
        value["roles"].sort()
        value["logical_names"].sort()
    return sorted(records.values(), key=lambda row: row["package_path"])


def _validate_package_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise ReleasePackageTransactionError("Package artifact path must be a non-empty string.")
    pure = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        pure.is_absolute() or windows.is_absolute() or windows.drive
        or "\\" in value or ".." in pure.parts or not pure.parts
        or pure.as_posix() != value
    ):
        raise ReleasePackageTransactionError(f"Unsafe Release Package artifact path: {value}")
    return pure


def _package_manifest_payload(
    records: Sequence[Mapping[str, Any]],
    *,
    release_id: str,
    source_release_manifest_sha256: str,
) -> dict[str, Any]:
    if not isinstance(release_id, str) or not release_id.strip():
        raise ReleasePackageTransactionError("Release ID must be non-empty.")
    if not _SHA256_RE.fullmatch(source_release_manifest_sha256):
        raise ReleasePackageTransactionError("Source Release Manifest SHA-256 is invalid.")
    rows = []
    seen_paths: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ReleasePackageTransactionError(f"Package artifact[{index}] must be an object.")
        pure = _validate_package_path(record.get("package_path"))
        path = pure.as_posix()
        folded = path.casefold()
        if folded in seen_paths:
            raise ReleasePackageTransactionError(f"Duplicate/case-colliding Release Package path: {path}")
        seen_paths.add(folded)
        digest = record.get("sha256")
        size = record.get("bytes")
        source_file = record.get("source_file")
        if (
            not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)
            or not isinstance(size, int) or isinstance(size, bool) or size < 0
            or not isinstance(source_file, str) or not source_file.strip()
        ):
            raise ReleasePackageTransactionError(f"Package artifact[{index}] has invalid source/hash/size data.")
        rows.append({
            "path": path,
            "source_file": source_file,
            "sha256": digest,
            "bytes": size,
            "categories": sorted(set(record.get("categories", []))),
            "roles": sorted(set(record.get("roles", []))),
            "logical_names": sorted(set(record.get("logical_names", []))),
        })
    if not rows:
        raise ReleasePackageTransactionError("A Release Package cannot be committed without artifacts.")
    payload = {
        "schema_version": "1.0",
        "release_id": release_id,
        "source_release_manifest_sha256": source_release_manifest_sha256,
        "artifact_count": len(rows),
        "artifacts": rows,
    }
    package_id = _sha256_bytes(_canonical_json(payload))
    return {"package_id": package_id, **payload}


def _write_json_new(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise ReleasePackageTransactionError(f"Refusing to overwrite immutable Release Package control file: {path}") from exc


def verify_release_package(
    release_dir: str | Path,
    *,
    expected_release_id: str | None = None,
) -> dict[str, Any]:
    """Verify the external commit marker, immutable manifest and every packaged byte."""
    root = _reject_symlink_path(Path(release_dir), "Release directory", allow_missing=False)
    marker_path = root / "release_package_commit.json"
    if marker_path.is_symlink() or not marker_path.is_file():
        raise ReleasePackageTransactionError("Release Package has no final commit marker.")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleasePackageTransactionError(f"Release Package commit marker is unreadable: {exc}") from exc
    if not isinstance(marker, Mapping) or marker.get("status") != "COMMITTED":
        raise ReleasePackageTransactionError("Release Package commit marker has an invalid status.")
    release_id = marker.get("release_id")
    package_id = marker.get("package_id")
    if (
        not isinstance(release_id, str) or not release_id.strip()
        or (expected_release_id is not None and release_id != expected_release_id)
        or not isinstance(package_id, str) or not _SHA256_RE.fullmatch(package_id)
    ):
        raise ReleasePackageTransactionError("Release Package commit marker identity is invalid.")

    package_dir_value = marker.get("package_directory")
    if not isinstance(package_dir_value, str) or not package_dir_value.strip():
        raise ReleasePackageTransactionError("Release Package commit marker has no package directory.")
    package_dir = _reject_symlink_path(Path(package_dir_value), "Committed package directory", allow_missing=False)
    if package_dir.parent != root or package_dir.name != f"package-{package_id[:20]}" or not package_dir.is_dir():
        raise ReleasePackageTransactionError("Committed package directory is misplaced or has the wrong package ID.")
    manifest_path = package_dir / "package_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ReleasePackageTransactionError("Committed Release Package manifest is missing or is a symbolic link.")
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = _sha256_bytes(manifest_bytes)
    if (
        marker.get("package_manifest_file") != str(manifest_path)
        or marker.get("package_manifest_sha256") != manifest_sha
    ):
        raise ReleasePackageTransactionError("Release Package manifest does not match the final commit marker.")
    try:
        package_manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleasePackageTransactionError(f"Release Package manifest is unreadable: {exc}") from exc
    if (
        not isinstance(package_manifest, Mapping)
        or package_manifest.get("schema_version") != "1.0"
        or package_manifest.get("release_id") != release_id
        or package_manifest.get("package_id") != package_id
        or not isinstance(package_manifest.get("artifacts"), list)
    ):
        raise ReleasePackageTransactionError("Release Package manifest identity or schema is invalid.")
    manifest_core = {key: value for key, value in package_manifest.items() if key != "package_id"}
    if _sha256_bytes(_canonical_json(manifest_core)) != package_id:
        raise ReleasePackageTransactionError("Release Package ID is not the hash of its immutable manifest contents.")
    rows = package_manifest["artifacts"]
    if package_manifest.get("artifact_count") != len(rows) or len(rows) != marker.get("artifact_count"):
        raise ReleasePackageTransactionError("Release Package manifest artifact count does not match its commit marker.")
    declared: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ReleasePackageTransactionError(f"Release Package manifest artifacts[{index}] is malformed.")
        pure = _validate_package_path(row.get("path"))
        relative = pure.as_posix()
        if relative in declared:
            raise ReleasePackageTransactionError(f"Release Package manifest repeats artifact path: {relative}")
        declared.add(relative)
        digest = row.get("sha256")
        size = row.get("bytes")
        if (
            not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)
            or not isinstance(size, int) or isinstance(size, bool) or size < 0
        ):
            raise ReleasePackageTransactionError(f"Release Package artifact[{index}] hash/size metadata is invalid.")
        target = package_dir.joinpath(*pure.parts)
        probe = package_dir
        for part in pure.parts:
            probe = probe / part
            if probe.is_symlink():
                raise ReleasePackageTransactionError(f"Committed package artifact traverses a symlink: {probe}")
        if not target.is_file():
            raise ReleasePackageTransactionError(f"Committed package artifact is missing: {relative}")
        payload = target.read_bytes()
        if len(payload) != size or _sha256_bytes(payload) != digest:
            raise ReleasePackageTransactionError(f"Committed package artifact hash/size mismatch: {relative}")
        if target.suffix.casefold() == ".docx":
            _validate_docx_bytes(payload, relative)

    actual = set()
    for candidate in package_dir.rglob("*"):
        if candidate.is_symlink():
            raise ReleasePackageTransactionError(f"Committed package contains a symlink: {candidate}")
        if candidate.is_file() and candidate != manifest_path:
            actual.add(candidate.relative_to(package_dir).as_posix())
    if actual != declared:
        raise ReleasePackageTransactionError(
            "Committed package inventory differs from its Manifest "
            f"(unlisted={sorted(actual - declared)}, missing={sorted(declared - actual)})."
        )
    return dict(package_manifest)


def commit_release_package(
    release_dir: str | Path,
    records: Sequence[Mapping[str, Any]],
    *,
    release_id: str,
    source_release_manifest_sha256: str,
) -> dict[str, Any]:
    """Stage every Release artifact, publish immutable directory, then write marker last."""
    root = _reject_symlink_path(Path(release_dir), "Release directory", allow_missing=False)
    marker_path = root / "release_package_commit.json"
    manifest = _package_manifest_payload(
        records,
        release_id=release_id,
        source_release_manifest_sha256=source_release_manifest_sha256,
    )
    package_id = manifest["package_id"]
    package_dir = root / f"package-{package_id[:20]}"
    marker_exists = marker_path.exists()
    if marker_exists:
        existing = verify_release_package(root, expected_release_id=release_id)
        if existing != manifest:
            raise ReleasePackageTransactionError(
                "Existing committed Release Package belongs to a different artifact/source snapshot; use a new release directory."
            )
        return {
            "status": "COMMITTED",
            "package_id": package_id,
            "package_directory": str(package_dir),
            "package_manifest_file": str(package_dir / "package_manifest.json"),
            "package_manifest_sha256": _sha256_file(package_dir / "package_manifest.json"),
            "commit_marker_file": str(marker_path),
            "commit_marker_sha256": _sha256_file(marker_path),
            "artifact_count": manifest["artifact_count"],
        }
    if package_dir.exists():
        # Crash after the immutable directory rename but before the external
        # marker: only the exact, fully verified candidate may be finalized.
        manifest_path = package_dir / "package_manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise ReleasePackageTransactionError(
                "An unmarked Release Package directory exists without a valid manifest; preserve it and use a new release directory."
            )
        current_sha = _sha256_file(manifest_path)
        if current_sha != _sha256_bytes(_canonical_json(manifest)):
            # package_manifest is written with pretty JSON; compare parsed content below.
            try:
                existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReleasePackageTransactionError(
                    "An unmarked Release Package directory contains an unreadable manifest."
                ) from exc
            if existing_manifest != manifest:
                raise ReleasePackageTransactionError(
                    "An unmarked Release Package directory conflicts with the current artifact snapshot."
                )
        else:
            existing_manifest = manifest
        # Reuse verifier logic through a temporary marker payload; do not expose
        # an unverified directory as committed.
        _verify_package_directory_against_manifest(package_dir, existing_manifest)
    else:
        staging = root / (".release-package-staging-" + os.urandom(8).hex())
        staging.mkdir(parents=True, exist_ok=False)
        try:
            seen: set[str] = set()
            for row in manifest["artifacts"]:
                relative = _validate_package_path(row["path"])
                if relative.as_posix() in seen:
                    raise ReleasePackageTransactionError("Release Package artifact paths are duplicated.")
                seen.add(relative.as_posix())
                source = _reject_symlink_path(Path(row["source_file"]), "Release source artifact", allow_missing=False)
                payload = source.read_bytes()
                if len(payload) != row["bytes"] or _sha256_bytes(payload) != row["sha256"]:
                    raise ReleasePackageTransactionError(
                        f"Release source artifact changed during package staging: {source}"
                    )
                if relative.suffix.casefold() == ".docx":
                    _validate_docx_bytes(payload, relative.as_posix())
                destination = staging.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
                copied = destination.read_bytes()
                if len(copied) != row["bytes"] or _sha256_bytes(copied) != row["sha256"]:
                    raise ReleasePackageTransactionError(
                        f"Staged Release Package artifact failed hash verification: {relative.as_posix()}"
                    )
            package_manifest = {key: value for key, value in manifest.items() if key != "package_id"}
            package_manifest["package_id"] = package_id
            (staging / "package_manifest.json").write_text(
                json.dumps(package_manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            _verify_package_directory_against_manifest(staging, package_manifest)
            os.replace(staging, package_dir)
            existing_manifest = package_manifest
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise

    manifest_path = package_dir / "package_manifest.json"
    marker = {
        "schema_version": "1.0",
        "status": "COMMITTED",
        "release_id": release_id,
        "package_id": package_id,
        "package_directory": str(package_dir),
        "package_manifest_file": str(manifest_path),
        "package_manifest_sha256": _sha256_file(manifest_path),
        "artifact_count": manifest["artifact_count"],
        "committed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    temporary_marker = root / (".release_package_commit." + os.urandom(8).hex() + ".tmp")
    try:
        with temporary_marker.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(marker, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary_marker, marker_path)
    except Exception:
        temporary_marker.unlink(missing_ok=True)
        raise
    return {
        "status": "COMMITTED",
        "package_id": package_id,
        "package_directory": str(package_dir),
        "package_manifest_file": str(manifest_path),
        "package_manifest_sha256": marker["package_manifest_sha256"],
        "commit_marker_file": str(marker_path),
        "commit_marker_sha256": _sha256_file(marker_path),
        "artifact_count": manifest["artifact_count"],
    }


def _verify_package_directory_against_manifest(
    package_dir: Path,
    manifest: Mapping[str, Any],
) -> None:
    manifest_core = {key: value for key, value in manifest.items() if key != "package_id"}
    if (
        not isinstance(manifest.get("package_id"), str)
        or _sha256_bytes(_canonical_json(manifest_core)) != manifest.get("package_id")
    ):
        raise ReleasePackageTransactionError("Staged Release Package ID is not bound to its manifest contents.")
    rows = manifest.get("artifacts")
    if not isinstance(rows, list) or not rows:
        raise ReleasePackageTransactionError("Release Package manifest must list at least one artifact.")
    declared = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ReleasePackageTransactionError(f"Release Package manifest artifacts[{index}] is malformed.")
        pure = _validate_package_path(row.get("path"))
        relative = pure.as_posix()
        if relative in declared:
            raise ReleasePackageTransactionError(f"Release Package manifest repeats artifact path: {relative}")
        declared.add(relative)
        target = package_dir.joinpath(*pure.parts)
        if not target.is_file() or target.is_symlink():
            raise ReleasePackageTransactionError(f"Staged Release Package artifact is missing or a symlink: {relative}")
        payload = target.read_bytes()
        if (
            len(payload) != row.get("bytes")
            or _sha256_bytes(payload) != row.get("sha256")
        ):
            raise ReleasePackageTransactionError(f"Staged Release Package artifact hash/size mismatch: {relative}")
        if target.suffix.casefold() == ".docx":
            _validate_docx_bytes(payload, relative)
    manifest_path = package_dir / "package_manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ReleasePackageTransactionError("Staged Release Package manifest is missing or a symlink.")
    actual = {
        candidate.relative_to(package_dir).as_posix()
        for candidate in package_dir.rglob("*")
        if candidate.is_file() and candidate != manifest_path
    }
    if actual != declared:
        raise ReleasePackageTransactionError(
            "Staged Release Package inventory differs from its Manifest "
            f"(unlisted={sorted(actual - declared)}, missing={sorted(declared - actual)})."
        )
