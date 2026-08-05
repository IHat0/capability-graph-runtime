"""Deterministic offline recovery contracts and operations."""

from __future__ import annotations

import json
import multiprocessing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cgr.molecular import MolecularArtifactRepository, MolecularProjectRepository
from cgr.pulsate_api.production_security import SQLiteAuditSink, SQLiteGrantProvider
from cgr.pulsate_api.recovery import (
    RecoveryError,
    RecoveryLock,
    RecoveryManifest,
    create_backup,
    production_persistence_inventory,
    restore_backup,
    verify_backup,
)


CONFIGURATION_FINGERPRINT = "a" * 64


def _hold_recovery_lock(root: str, ready: object, release: object) -> None:
    with RecoveryLock(Path(root), timeout_seconds=1):
        ready.set()  # type: ignore[attr-defined]
        release.wait(10)  # type: ignore[attr-defined]


def _state(root: Path) -> Path:
    root.mkdir()
    for name in ("experiments", "interpretations", "runs", "workflows"):
        directory = root / name
        directory.mkdir()
        (directory / "state.json").write_text(
            json.dumps({"schema_version": 1, "component": name}, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
    artifacts = MolecularArtifactRepository(root / "molecular-artifacts")
    artifacts.start()
    artifacts.close()
    projects = MolecularProjectRepository(root / "molecular-projects")
    projects.start()
    projects.close()
    security = root / "security"
    security.mkdir()
    grants = SQLiteGrantProvider(security / "grants.sqlite3", trusted_root=root)
    grants.start()
    grants.close()
    audit = SQLiteAuditSink(security / "audit.sqlite3", trusted_root=root)
    audit.start()
    audit.close()
    return root


def test_inventory_matches_actual_production_data_layout() -> None:
    inventory = production_persistence_inventory()
    assert tuple(item.component_identifier for item in inventory) == tuple(
        sorted(
            (
                "authorization-grants",
                "experiments",
                "interpretations",
                "molecular-artifacts",
                "molecular-projects",
                "runs",
                "security-audit",
                "workflows",
            )
        )
    )
    assert all(item.required for item in inventory)
    assert all("jwks" not in item.relative_storage_identity for item in inventory)
    assert all("catalogue" not in item.relative_storage_identity for item in inventory)


def test_backup_manifest_is_deterministic_and_verifies_real_contents(tmp_path: Path) -> None:
    data = _state(tmp_path / "data")
    backups = tmp_path / "backups"
    backups.mkdir()
    manifest = create_backup(
        data_root=data,
        backup_root=backups,
        backup_identifier="backup-deterministic",
        safe_configuration_fingerprint=CONFIGURATION_FINGERPRINT,
        application_revision="0" * 40,
        created_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    verified = verify_backup(
        backup_root=backups,
        backup_identifier="backup-deterministic",
    )
    persisted = RecoveryManifest.model_validate_json(
        (backups / "backup-deterministic" / "manifest.json").read_bytes()
    )
    assert persisted == manifest
    assert verified.verified
    assert verified.manifest_fingerprint == manifest.manifest_fingerprint
    assert manifest.total_file_count == len(manifest.files)
    assert tuple(item.relative_path for item in manifest.files) == tuple(
        sorted(item.relative_path for item in manifest.files)
    )


@pytest.mark.parametrize("corruption", ("missing", "extra", "modified"))
def test_backup_verification_rejects_corruption(tmp_path: Path, corruption: str) -> None:
    data = _state(tmp_path / "data")
    backups = tmp_path / "backups"
    backups.mkdir()
    manifest = create_backup(
        data_root=data,
        backup_root=backups,
        backup_identifier="backup-corrupt",
        safe_configuration_fingerprint=CONFIGURATION_FINGERPRINT,
    )
    bundle = backups / "backup-corrupt"
    victim = bundle / manifest.files[0].relative_path
    if corruption == "missing":
        victim.unlink()
    elif corruption == "extra":
        (bundle / "components" / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    else:
        victim.write_bytes(victim.read_bytes() + b"changed")
    with pytest.raises(RecoveryError):
        verify_backup(backup_root=backups, backup_identifier="backup-corrupt")


def test_backup_rejects_overlapping_roots_and_unsafe_links(tmp_path: Path) -> None:
    data = _state(tmp_path / "data")
    with pytest.raises(RecoveryError):
        create_backup(
            data_root=data,
            backup_root=data,
            backup_identifier="backup-overlap",
            safe_configuration_fingerprint=CONFIGURATION_FINGERPRINT,
        )
    unsafe = data / "runs" / "unsafe"
    try:
        unsafe.symlink_to(tmp_path)
    except OSError:
        pytest.skip("This platform cannot create test symlinks.")
    backups = tmp_path / "backups"
    backups.mkdir()
    with pytest.raises(RecoveryError):
        create_backup(
            data_root=data,
            backup_root=backups,
            backup_identifier="backup-link",
            safe_configuration_fingerprint=CONFIGURATION_FINGERPRINT,
        )


def test_recovery_lock_excludes_concurrent_owner_and_releases(tmp_path: Path) -> None:
    root = tmp_path / "recovery"
    root.mkdir()
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_recovery_lock,
        args=(str(root), ready, release),
    )
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(RecoveryError):
            RecoveryLock(root, timeout_seconds=0).acquire()
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    with RecoveryLock(root, timeout_seconds=0):
        pass


def test_recovery_lock_releases_after_controlled_exception(tmp_path: Path) -> None:
    root = tmp_path / "recovery"
    root.mkdir()
    with pytest.raises(RuntimeError):
        with RecoveryLock(root, timeout_seconds=0):
            raise RuntimeError("test-only failure")
    with RecoveryLock(root, timeout_seconds=0):
        pass


def test_restore_requires_empty_target_and_initializes_restored_providers(tmp_path: Path) -> None:
    data = _state(tmp_path / "data")
    backups = tmp_path / "backups"
    receipts = tmp_path / "receipts"
    backups.mkdir()
    receipts.mkdir()
    create_backup(
        data_root=data,
        backup_root=backups,
        backup_identifier="backup-restore",
        safe_configuration_fingerprint=CONFIGURATION_FINGERPRINT,
    )
    target = tmp_path / "restored"
    receipt = restore_backup(
        backup_root=backups,
        backup_identifier="backup-restore",
        target_data_root=target,
        receipt_root=receipts,
        restored_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    assert target.is_dir()
    assert receipt.backup_identifier == "backup-restore"
    assert not hasattr(receipt, "target_path")
    assert (receipts / f"{receipt.receipt_identifier}.json").is_file()
    grants = SQLiteGrantProvider(target / "security" / "grants.sqlite3", trusted_root=target)
    grants.start()
    assert grants.ready()
    grants.close()
    audit = SQLiteAuditSink(target / "security" / "audit.sqlite3", trusted_root=target)
    audit.start()
    assert audit.ready() and audit.verify_integrity()
    audit.close()

    second_receipt = restore_backup(
        backup_root=backups,
        backup_identifier="backup-restore",
        target_data_root=tmp_path / "restored-second",
        receipt_root=receipts,
        restored_at=datetime(2026, 8, 4, 0, 0, 1, tzinfo=UTC),
    )
    assert second_receipt.receipt_identifier != receipt.receipt_identifier

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(RecoveryError):
        restore_backup(
            backup_root=backups,
            backup_identifier="backup-restore",
            target_data_root=nonempty,
            receipt_root=receipts,
        )
    assert (nonempty / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_failed_publication_cleans_only_task_owned_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _state(tmp_path / "data")
    backups = tmp_path / "backups"
    backups.mkdir()

    def fail_publication(_source: Path, _destination: Path) -> None:
        raise OSError("test-only publication failure")

    monkeypatch.setattr(
        "cgr.pulsate_api.recovery._rename_directory_no_replace",
        fail_publication,
    )
    with pytest.raises(RecoveryError):
        create_backup(
            data_root=data,
            backup_root=backups,
            backup_identifier="backup-publication-failure",
            safe_configuration_fingerprint=CONFIGURATION_FINGERPRINT,
        )
    assert not (backups / "backup-publication-failure").exists()
    assert not tuple(backups.glob(".backup-tmp-*"))


def test_recovery_cli_errors_are_path_free(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from cgr.pulsate_api.recovery import verify_main

    root = tmp_path / "backups"
    root.mkdir()
    assert verify_main(["--backup-root", str(root), "--backup-identifier", "backup-missing"]) == 1
    output = capsys.readouterr().out
    assert str(tmp_path) not in output
    assert '"status":"failed"' in output
