from pathlib import Path

from platformdirs import user_data_dir

from chesswhisper.paths import data_dir


def test_data_dir_defaults_to_the_platform_user_data_dir(monkeypatch):
    monkeypatch.delenv("CHESSWHISPER_DATA_DIR", raising=False)

    assert data_dir() == Path(user_data_dir("chesswhisper"))


def test_data_dir_honours_the_environment_override(tmp_path, monkeypatch):
    target = tmp_path / "custom" / "data"
    monkeypatch.setenv("CHESSWHISPER_DATA_DIR", str(target))

    assert data_dir() == target
    assert target.is_dir()


def test_web_database_and_secrets_live_in_the_overridden_dir(tmp_path, monkeypatch):
    from chesswhisper.web import db as web_db
    from chesswhisper.web import security

    monkeypatch.setenv("CHESSWHISPER_DATA_DIR", str(tmp_path))

    assert web_db._default_db_path() == tmp_path / "chesswhisper.db"
    security.get_encryption_key()
    assert (tmp_path / "api_key_encryption.key").is_file()
