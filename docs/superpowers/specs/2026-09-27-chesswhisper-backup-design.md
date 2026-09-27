# Backup and restore (0.5.0)

## Goal

Everything the web UI keeps lives in one data directory, and two of its
files cannot be recreated: the database and `api_key_encryption.key`
(without it every saved API key is unreadable). Today the deployment guide
tells people to copy the directory by hand, which is easy to get wrong
while the server is writing. Two commands make it one step each way:

```
chesswhisper backup [-o FILE] [--include-cache]
chesswhisper restore FILE [--force]
```

## Decisions

- **A zip archive** with `chesswhisper.db`, `session_secret.key`,
  `api_key_encryption.key` (each only if present), optionally
  `eval_cache.sqlite3`, and `manifest.json` (`format: 1`, chesswhisper
  version, schema revision, creation time, file list). The Stockfish binary
  is left out: it is downloaded again.
- **Safe while the server runs.** SQLite files are copied with SQLite's
  online backup API, which gives a consistent snapshot even mid-write.
- **The evaluation cache is opt-in** (`--include-cache`): it can be large
  and is only a speed-up.
- **Restore refuses to overwrite** a data directory that already has a
  database unless `--force`. With `--force` the files it replaces are moved
  to `pre-restore-<timestamp>/` in the data directory, not deleted.
- **Restore validates before touching anything**: the manifest exists and
  has format 1; members are only the known file names (no paths, so no
  traversal); the database passes `PRAGMA integrity_check`; its schema
  revision is one this version knows. A backup from a newer chesswhisper is
  refused. An older one is fine: migrations run on the next start.
- **No web extras needed.** The module uses only the standard library, so
  a plain install can back up a server's data directory.
- The server should be stopped for a restore; the command says so. It
  cannot reliably detect a running server, so it does not try.

## Components

- `src/chesswhisper/backup.py`: `create_backup(output, data, include_cache)
  -> BackupInfo` and `restore_backup(archive, data, force) -> BackupInfo`,
  raising `BackupError` with a user-facing message.
- `cli.py`: the two commands, printing what was written or restored.
- Docs: README command list, deployment guide's backup section.

## Testing

Round trip into an empty directory (users and keys intact), cache
included only on request, backup while another connection holds a write
transaction, refusal without `--force`, `--force` keeps the old files,
missing manifest, unknown or path-like member names, newer schema revision,
corrupt database, no database to back up, and the CLI wrappers.
