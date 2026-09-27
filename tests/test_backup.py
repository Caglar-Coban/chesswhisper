import json
import sqlite3
import zipfile

import pytest
from click.testing import CliRunner

from chesswhisper.backup import (
    CACHE_FILE,
    DB_FILE,
    KEY_FILES,
    MANIFEST_FILE,
    BackupError,
    create_backup,
    restore_backup,
)
from chesswhisper.cli import cli


def _make_data_dir(path, users=("alice",), with_cache=True):
    """A data directory like the web UI leaves behind, at the latest schema."""
    from chesswhisper.web import db as db_module
    from chesswhisper.web.db import init_db, session_scope
    from chesswhisper.web.models import User

    path.mkdir(parents=True, exist_ok=True)
    init_db(path / DB_FILE)
    with session_scope() as db:
        for name in users:
            db.add(User(username=name, password_hash="h"))
    db_module._engine.dispose()  # a stopped server holds no connection (Windows locks open files)
    (path / "session_secret.key").write_bytes(b"session-secret")
    (path / "api_key_encryption.key").write_bytes(b"fernet-key")
    if with_cache:
        with sqlite3.connect(path / CACHE_FILE) as conn:
            conn.execute("CREATE TABLE evals (k TEXT PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO evals VALUES ('a', 'b')")
        conn.close()
    return path


def _latest_revision():
    from chesswhisper.backup import _known_revisions

    return max(_known_revisions())  # revisions are numbered 0001_, 0002_, ...


def _usernames(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return sorted(row[0] for row in conn.execute("SELECT username FROM users"))
    finally:
        conn.close()


def _members(archive):
    with zipfile.ZipFile(archive) as zf:
        return sorted(zf.namelist())


def _rewrite(archive, changes):
    """Copy *archive* with members replaced (bytes) or removed (None)."""
    with zipfile.ZipFile(archive) as zf:
        contents = {name: zf.read(name) for name in zf.namelist()}
    for name, value in changes.items():
        if value is None:
            contents.pop(name, None)
        else:
            contents[name] = value
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in contents.items():
            zf.writestr(name, data)


def test_round_trip_into_an_empty_directory(tmp_path):
    source = _make_data_dir(tmp_path / "source", users=("alice", "bob"))
    archive = tmp_path / "backup.zip"

    info = create_backup(archive, source)
    restored = restore_backup(archive, tmp_path / "target")

    target = tmp_path / "target"
    assert _usernames(target / DB_FILE) == ["alice", "bob"]
    for name in KEY_FILES:
        assert (target / name).read_bytes() == (source / name).read_bytes()
    assert not (target / CACHE_FILE).exists()
    assert info.schema_revision == restored.schema_revision
    assert info.schema_revision == _latest_revision()


def test_manifest_describes_the_backup(tmp_path):
    source = _make_data_dir(tmp_path / "source")
    archive = tmp_path / "backup.zip"

    create_backup(archive, source)

    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read(MANIFEST_FILE))
    assert manifest["format"] == 1
    assert manifest["schema_revision"] == _latest_revision()
    assert sorted(manifest["files"]) == sorted([DB_FILE, *KEY_FILES])
    assert manifest["chesswhisper_version"]


def test_cache_is_included_only_on_request(tmp_path):
    source = _make_data_dir(tmp_path / "source")

    create_backup(tmp_path / "plain.zip", source)
    create_backup(tmp_path / "full.zip", source, include_cache=True)

    assert CACHE_FILE not in _members(tmp_path / "plain.zip")
    assert CACHE_FILE in _members(tmp_path / "full.zip")
    restore_backup(tmp_path / "full.zip", tmp_path / "target")
    with sqlite3.connect(tmp_path / "target" / CACHE_FILE) as conn:
        assert conn.execute("SELECT v FROM evals").fetchall() == [("b",)]
    conn.close()


def test_missing_key_files_are_skipped(tmp_path):
    source = _make_data_dir(tmp_path / "source")
    (source / "session_secret.key").unlink()

    create_backup(tmp_path / "backup.zip", source)

    assert "session_secret.key" not in _members(tmp_path / "backup.zip")


def test_backup_is_consistent_while_another_connection_writes(tmp_path):
    source = _make_data_dir(tmp_path / "source")
    writer = sqlite3.connect(source / DB_FILE)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO users (username, password_hash, is_admin, created_at, failed_login_attempts, session_version) VALUES ('uncommitted', 'h', 0, '2026-01-01', 0, 0)")
    try:
        create_backup(tmp_path / "backup.zip", source)
    finally:
        writer.rollback()
        writer.close()

    restore_backup(tmp_path / "backup.zip", tmp_path / "target")
    assert _usernames(tmp_path / "target" / DB_FILE) == ["alice"]


def test_backup_without_a_database_fails(tmp_path):
    (tmp_path / "empty").mkdir()

    with pytest.raises(BackupError, match="No database"):
        create_backup(tmp_path / "backup.zip", tmp_path / "empty")


def test_restore_refuses_to_overwrite_without_force(tmp_path):
    archive = tmp_path / "backup.zip"
    create_backup(archive, _make_data_dir(tmp_path / "source", users=("alice",)))
    target = _make_data_dir(tmp_path / "target", users=("carol",))

    with pytest.raises(BackupError, match="--force"):
        restore_backup(archive, target)

    assert _usernames(target / DB_FILE) == ["carol"]


def test_forced_restore_keeps_the_replaced_files(tmp_path):
    archive = tmp_path / "backup.zip"
    create_backup(archive, _make_data_dir(tmp_path / "source", users=("alice",)))
    target = _make_data_dir(tmp_path / "target", users=("carol",))

    restore_backup(archive, target, force=True)

    assert _usernames(target / DB_FILE) == ["alice"]
    [kept] = list(target.glob("pre-restore-*"))
    assert _usernames(kept / DB_FILE) == ["carol"]
    assert (kept / "api_key_encryption.key").exists()


def test_restore_undoes_its_moves_if_a_file_is_in_use(tmp_path, monkeypatch):
    archive = tmp_path / "backup.zip"
    create_backup(archive, _make_data_dir(tmp_path / "source", users=("alice",)))
    target = _make_data_dir(tmp_path / "target", users=("carol",))
    real_replace = type(target).replace

    def replace(self, destination):
        if self.name == "api_key_encryption.key" and "pre-restore" not in str(destination) and ".restore-" in str(self):
            raise PermissionError("in use")
        return real_replace(self, destination)

    monkeypatch.setattr(type(target), "replace", replace)

    with pytest.raises(BackupError, match="Stop the server"):
        restore_backup(archive, target, force=True)

    monkeypatch.undo()
    assert _usernames(target / DB_FILE) == ["carol"]
    assert (target / "api_key_encryption.key").read_bytes() == b"fernet-key"


def test_restore_rejects_an_archive_without_a_manifest(tmp_path):
    archive = tmp_path / "backup.zip"
    create_backup(archive, _make_data_dir(tmp_path / "source"))
    _rewrite(archive, {MANIFEST_FILE: None})

    with pytest.raises(BackupError, match="not a chesswhisper backup"):
        restore_backup(archive, tmp_path / "target")


def test_restore_rejects_something_that_is_not_a_zip(tmp_path):
    archive = tmp_path / "backup.zip"
    archive.write_bytes(b"hello")

    with pytest.raises(BackupError, match="not a chesswhisper backup"):
        restore_backup(archive, tmp_path / "target")


def test_restore_rejects_an_unknown_format(tmp_path):
    archive = tmp_path / "backup.zip"
    create_backup(archive, _make_data_dir(tmp_path / "source"))
    _rewrite(archive, {MANIFEST_FILE: json.dumps({"format": 99}).encode()})

    with pytest.raises(BackupError, match="format"):
        restore_backup(archive, tmp_path / "target")


@pytest.mark.parametrize("name", ["../evil.key", "sub/chesswhisper.db", "/abs.key", "unexpected.txt"])
def test_restore_rejects_unexpected_members(tmp_path, name):
    archive = tmp_path / "backup.zip"
    create_backup(archive, _make_data_dir(tmp_path / "source"))
    _rewrite(archive, {name: b"x"})

    with pytest.raises(BackupError, match="unexpected file"):
        restore_backup(archive, tmp_path / "target")
    assert not (tmp_path / "evil.key").exists()
    assert not (tmp_path / "target" / DB_FILE).exists()


def test_restore_rejects_a_backup_from_a_newer_version(tmp_path):
    source = _make_data_dir(tmp_path / "source")
    conn = sqlite3.connect(source / DB_FILE)
    conn.execute("UPDATE alembic_version SET version_num = '0999_from_the_future'")
    conn.commit()
    conn.close()
    archive = tmp_path / "backup.zip"
    create_backup(archive, source)

    with pytest.raises(BackupError, match="newer version"):
        restore_backup(archive, tmp_path / "target")


def test_restore_accepts_a_backup_from_an_older_schema(tmp_path):
    source = _make_data_dir(tmp_path / "source")
    conn = sqlite3.connect(source / DB_FILE)
    conn.execute("UPDATE alembic_version SET version_num = '0006_puzzle_attempts'")
    conn.commit()
    conn.close()
    archive = tmp_path / "backup.zip"
    create_backup(archive, source)

    info = restore_backup(archive, tmp_path / "target")

    assert info.schema_revision == "0006_puzzle_attempts"


def test_restore_rejects_a_corrupt_database(tmp_path):
    archive = tmp_path / "backup.zip"
    create_backup(archive, _make_data_dir(tmp_path / "source"))
    _rewrite(archive, {DB_FILE: b"SQLite format 3\x00" + b"\x00" * 200})

    with pytest.raises(BackupError, match="database"):
        restore_backup(archive, tmp_path / "target")
    assert not (tmp_path / "target" / DB_FILE).exists()


def test_cli_backup_and_restore(tmp_path, monkeypatch):
    source = _make_data_dir(tmp_path / "source")
    monkeypatch.setenv("CHESSWHISPER_DATA_DIR", str(source))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["backup"])
    assert result.exit_code == 0, result.output
    [archive] = list(tmp_path.glob("chesswhisper-backup-*.zip"))
    assert str(archive.name) in result.output
    assert "Keep it private" in result.output

    monkeypatch.setenv("CHESSWHISPER_DATA_DIR", str(tmp_path / "target"))
    result = runner.invoke(cli, ["restore", str(archive)])
    assert result.exit_code == 0, result.output
    assert "Restored" in result.output
    assert _usernames(tmp_path / "target" / DB_FILE) == ["alice"]

    result = runner.invoke(cli, ["restore", str(archive)])
    assert result.exit_code == 1
    assert "--force" in result.output


def test_cli_backup_to_a_chosen_file_with_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CHESSWHISPER_DATA_DIR", str(_make_data_dir(tmp_path / "source")))

    result = CliRunner().invoke(cli, ["backup", "-o", str(tmp_path / "mine.zip"), "--include-cache"])

    assert result.exit_code == 0, result.output
    assert CACHE_FILE in _members(tmp_path / "mine.zip")


def test_cli_backup_reports_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("CHESSWHISPER_DATA_DIR", str(tmp_path / "empty"))

    result = CliRunner().invoke(cli, ["backup", "-o", str(tmp_path / "x.zip")])

    assert result.exit_code == 1
    assert "No database" in result.output
