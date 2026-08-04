# Pulsate offline backup, restore, and rollback v1

## Consistency boundary

The only supported model is `offline_coordinated`: stop the API and every
writer. Recovery commands open no listener and execute no scientific engine.
Backup and restore use the same bounded cross-process lock under the backup
root, so they cannot overlap.

`production_persistence_inventory()` is authoritative. It includes molecular
artifacts/projects, runs, experiments, interpretations, grants, and audit
records. Configuration, JWKS, credentials, and catalogues are excluded.
The local SQLite audit hash chain is integrity-evident, not externally anchored
or WORM storage; backup retention does not change that limitation.

## Backup and verification

```text
cgr-pulsate-backup --data-root <stopped-data-root> \
  --backup-root <backup-root> --backup-identifier backup-<unique-id> \
  --configuration-fingerprint <safe-sha256> \
  --application-revision <40-character-commit>
cgr-pulsate-backup-verify --backup-root <backup-root> \
  --backup-identifier backup-<unique-id>
```

Backup uses SQLite's backup API, validates schemas and the complete audit hash
chain, copies repositories without links, hashes every byte, writes a canonical
manifest and `COMPLETE` last, atomically publishes without replacement, and
verifies the published bundle. Verification rejects missing, extra, changed,
noncanonical, oversized, linked, special, incomplete, or incompatible evidence.

## Restore

```text
cgr-pulsate-restore --backup-root <backup-root> \
  --backup-identifier backup-<unique-id> \
  --target-data-root <new-data-root> --receipt-root <receipt-root>
```

The target must be absent or explicitly empty. Restore uses task-owned staging,
validates every repository and SQLite provider, then publishes with atomic
no-replace semantics. There is no merge or overwrite mode. Its receipt contains
safe identities and hashes, never a target path or payload.

## Operator-controlled rollback

1. Stop the service and all writers.
2. Verify and restore the chosen backup into a new root.
3. Back up and verify the current live root separately.
4. Explicitly change the deployment mount/reference to the new root.
5. Run configuration check; start; require `/live` and `/ready`.
6. Retain the previous root for the approved rollback window.

Cross-platform atomic mount switching is not claimed. The software never
switches mounts or automatically deletes old roots.

## Retention

Keep at least three verified generations on separate backup media. Keep
receipts and audit backups for the applicable audit-retention period, and keep
the previous rollback root until operator approval. Verify a newer generation
before expiring an older one. Only task-owned failed staging may be cleaned;
there is no automatic destructive retention cleanup.
