import os
import stat
import tarfile
import zipfile

import pytest

from chesswhisper.engine_provision import (
    asset_name_for_platform,
    get_stockfish_path,
    resolve_installed_stockfish,
    StockfishProvisionError,
    _find_binary,
    _extract,
    STOCKFISH_TAG_DIRNAME,
)


@pytest.mark.parametrize("system,machine,expected", [
    ("Windows", "AMD64", "stockfish-windows-x86-64-universal.zip"),
    ("Windows", "ARM64", "stockfish-windows-arm64-universal.zip"),
    ("Linux", "x86_64", "stockfish-linux-x86-64-universal.tar.gz"),
    ("Linux", "aarch64", "stockfish-linux-arm64-universal.tar.gz"),
    ("Darwin", "x86_64", "stockfish-macos-universal.tar.gz"),
    ("Darwin", "arm64", "stockfish-macos-universal.tar.gz"),
])
def test_asset_name_for_platform(system, machine, expected):
    assert asset_name_for_platform(system, machine) == expected


def test_asset_name_for_unsupported_platform_raises():
    with pytest.raises(StockfishProvisionError):
        asset_name_for_platform("Plan9", "mips")


def test_find_binary_locates_nested_executable(tmp_path):
    nested = tmp_path / "stockfish-windows-x86-64-universal" / "stockfish"
    nested.parent.mkdir(parents=True)
    exe = nested.with_suffix(".exe")
    exe.write_bytes(b"fake binary")

    found = _find_binary(tmp_path)

    assert found == exe


def test_find_binary_raises_when_missing(tmp_path):
    with pytest.raises(StockfishProvisionError):
        _find_binary(tmp_path)


def test_extract_zip(tmp_path):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("inner/stockfish.exe", "fake")
    dest = tmp_path / "out"

    _extract(archive, dest)

    assert (dest / "inner" / "stockfish.exe").exists()


def test_extract_tar_gz(tmp_path):
    inner_file = tmp_path / "stockfish"
    inner_file.write_bytes(b"fake")
    archive = tmp_path / "a.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(inner_file, arcname="inner/stockfish")
    dest = tmp_path / "out"

    _extract(archive, dest)

    assert (dest / "inner" / "stockfish").exists()


def test_get_stockfish_path_reuses_cached_binary(tmp_path, monkeypatch):
    # Pre-place a "binary" where get_stockfish_path expects to find one already installed.
    version_dir = tmp_path / STOCKFISH_TAG_DIRNAME
    version_dir.mkdir(parents=True)
    fake_binary = version_dir / "stockfish.exe"
    fake_binary.write_bytes(b"fake")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("should not attempt a download when cached binary exists")

    monkeypatch.setattr("chesswhisper.engine_provision._download", fail_if_called)

    result = get_stockfish_path(app_data_dir=tmp_path)

    assert result == fake_binary


def test_get_stockfish_path_full_cache_miss_flow(tmp_path, mocker):
    # No cached binary exists yet, so get_stockfish_path must go through the
    # full download -> extract -> find -> (chmod) -> verify -> return pipeline.
    version_dir = tmp_path / STOCKFISH_TAG_DIRNAME

    def fake_extract(archive_path, dest_dir):
        # Stand in for a real archive extraction by dropping a binary where
        # _find_binary will discover it, so the real _find_binary logic runs.
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "stockfish").write_bytes(b"fake binary")

    mock_download = mocker.patch("chesswhisper.engine_provision._download")
    mock_extract = mocker.patch(
        "chesswhisper.engine_provision._extract", side_effect=fake_extract
    )
    mock_verify = mocker.patch(
        "chesswhisper.engine_provision._verify_binary", return_value=True
    )

    result = get_stockfish_path(app_data_dir=tmp_path)

    expected_binary = version_dir / "stockfish"
    assert result == expected_binary
    mock_download.assert_called_once()
    mock_extract.assert_called_once()
    mock_verify.assert_called_once()

    if os.name != "nt":
        mode = expected_binary.stat().st_mode
        assert mode & stat.S_IXUSR


def test_resolve_installed_stockfish_explicit_path_wins(monkeypatch):
    monkeypatch.setenv("CHESSWHISPER_STOCKFISH_PATH", "/env/stockfish")
    monkeypatch.setattr("shutil.which", lambda name: "/path/stockfish")

    result = resolve_installed_stockfish("/explicit/stockfish")

    assert result == "/explicit/stockfish"


def test_resolve_installed_stockfish_env_var_wins_when_no_explicit(monkeypatch):
    monkeypatch.setenv("CHESSWHISPER_STOCKFISH_PATH", "/env/stockfish")
    monkeypatch.setattr("shutil.which", lambda name: "/path/stockfish")

    result = resolve_installed_stockfish(None)

    assert result == "/env/stockfish"


def test_resolve_installed_stockfish_uses_which_when_nothing_else_set(monkeypatch):
    monkeypatch.delenv("CHESSWHISPER_STOCKFISH_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/fake/path")

    result = resolve_installed_stockfish(None)

    assert result == "/fake/path"


def test_resolve_installed_stockfish_returns_none_when_not_found(monkeypatch):
    monkeypatch.delenv("CHESSWHISPER_STOCKFISH_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)

    result = resolve_installed_stockfish(None)

    assert result is None
