"""Back up and restore the web UI's data directory as one zip file.

The archive holds the database, the two key files and, on request, the
evaluation cache, plus a manifest.json. SQLite files are copied with
SQLite's online backup API, so a backup taken while the server is writing
is still a consistent snapshot. Only the standard library is used, so this
works without the web extras installed.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from importlib.resources import files
from pathlib import Path

DB_FILE = "chesswhisper.db"
CACHE_FILE = "eval_cache.sqlite3"
KEY_FILES = ("session_secret.key", "api_key_encryption.key")
MANIFEST_FILE = "manifest.json"
FORMAT = 1

_SQLITE_FILES = (DB_FILE, CACHE_FILE)
_ALLOWED_MEMBERS = {DB_FILE, CACHE_FILE, MANIFEST_FILE, *KEY_FILES}
_REVISION_RE = re.compile(r'^revision: str = "([^"]+)"', re.MULTILINE)


class BackupError(Exception):
    """A backup or restore that cannot go ahead; the message is for the user."""


@dataclass(frozen=True)
class BackupInfo:
    files: list[str]
    schema_revision: str | None


def _known_revisions() -> set[str]:
    versions = files("chesswhisper.web").joinpath("migrations", "versions")
    found = set()
    for entry in versions.iterdir():
        if entry.name.endswith(".py"):
            match = _REVISION_RE.search(entry.read_text(encoding="utf-8"))
            if match:
                found.add(match.group(1))
    return found


def _schema_revision(db_path: Path) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    return row[0] if row else None


def _copy_sqlite(source: Path, target: Path) -> None:
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def create_backup(output: Path, data: Path, include_cache: bool = False) -> BackupInfo:
    """Write a backup of *data* to *output*."""
    if not (data / DB_FILE).is_file():
        raise BackupError(f"No database to back up in {data}.")

    names = [DB_FILE, *(name for name in KEY_FILES if (data / name).is_file())]
    if include_cache and (data / CACHE_FILE).is_file():
        names.append(CACHE_FILE)

    with tempfile.TemporaryDirectory() as staging_dir:
        staging = Path(staging_dir)
        for name in names:
            if name in _SQLITE_FILES:
                _copy_sqlite(data / name, staging / name)
            else:
                shutil.copyfile(data / name, staging / name)
        revision = _schema_revision(staging / DB_FILE)
        manifest = {
            "format": FORMAT,
            "chesswhisper_version": package_version("chesswhisper"),
            "schema_revision": revision,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "files": names,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST_FILE, json.dumps(manifest, indent=2))
            for name in names:
                zf.write(staging / name, name)
    return BackupInfo(files=names, schema_revision=revision)


def _read_archive(archive: Path, staging: Path) -> list[str]:
    """Check *archive* and extract its files into *staging*; returns their names."""
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BackupError(f"{archive} is not a chesswhisper backup.") from exc
    with zf:
        members = zf.namelist()
        for name in members:
            if name not in _ALLOWED_MEMBERS:
                raise BackupError(f"The backup contains an unexpected file: {name!r}.")
        if MANIFEST_FILE not in members:
            raise BackupError(f"{archive} is not a chesswhisper backup (no {MANIFEST_FILE}).")
        try:
            manifest = json.loads(zf.read(MANIFEST_FILE))
        except ValueError as exc:
            raise BackupError(f"{archive} is not a chesswhisper backup (unreadable manifest).") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise BackupError("This backup uses a format this version of chesswhisper does not understand.")
        if DB_FILE not in members:
            raise BackupError("The backup has no database in it.")
        names = [name for name in members if name != MANIFEST_FILE]
        for name in names:
            # Member names were checked against a fixed list, so they are plain file names.
            (staging / name).write_bytes(zf.read(name))
    return names


def _check_database(db_path: Path) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"The database in the backup is damaged: {exc}.") from exc
    finally:
        conn.close()
    if not result or result[0] != "ok":
        raise BackupError("The database in the backup is damaged.")
    revision = _schema_revision(db_path)
    if revision is not None and revision not in _known_revisions():
        raise BackupError(
            f"This backup was made by a newer version of chesswhisper (schema {revision}). "
            "Upgrade chesswhisper, then restore it."
        )
    return revision


def _move_all(moves: list[tuple[Path, Path]]) -> None:
    """Make every move or, if one fails (a file in use), undo the ones already made."""
    done: list[tuple[Path, Path]] = []
    try:
        for source, target in moves:
            source.replace(target)
            done.append((source, target))
    except OSError as exc:
        for source, target in reversed(done):
            target.replace(source)
        raise BackupError(f"Could not replace the data files ({exc}). Stop the server and try again.") from exc


def restore_backup(archive: Path, data: Path, force: bool = False) -> BackupInfo:
    """Replace the contents of *data* with the backup in *archive*.

    Everything is checked before anything in *data* changes. With *force*,
    files that would be overwritten are moved to a pre-restore-* folder.
    """
    data.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=data))
    try:
        names = _read_archive(archive, staging)
        revision = _check_database(staging / DB_FILE)

        if (data / DB_FILE).exists() and not force:
            raise BackupError(
                f"{data} already has a database. Stop the server and run again with --force to replace it "
                "(the current files are kept in a pre-restore folder)."
            )
        # A leftover journal next to the old database would be applied to the new one.
        sidecars = [f"{DB_FILE}{suffix}" for suffix in ("-journal", "-wal", "-shm")]
        replaced = [name for name in [*names, *sidecars] if (data / name).exists()]
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        kept = data / f"pre-restore-{stamp}"
        moves = [(data / name, kept / name) for name in replaced]
        moves += [(staging / name, data / name) for name in names]
        if replaced:
            kept.mkdir()
        _move_all(moves)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return BackupInfo(files=names, schema_revision=revision)
