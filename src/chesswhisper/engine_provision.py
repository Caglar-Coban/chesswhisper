from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

import requests
from platformdirs import user_data_dir

STOCKFISH_RELEASE_TAG = "sf_19"
STOCKFISH_TAG_DIRNAME = STOCKFISH_RELEASE_TAG
_RELEASE_BASE_URL = (
    f"https://github.com/official-stockfish/Stockfish/releases/download/{STOCKFISH_RELEASE_TAG}"
)


class StockfishProvisionError(Exception):
    pass


def asset_name_for_platform(system: str, machine: str) -> str:
    system = system.lower()
    machine = machine.lower()

    if system == "windows":
        if machine in ("amd64", "x86_64"):
            return "stockfish-windows-x86-64-universal.zip"
        if machine in ("arm64", "aarch64"):
            return "stockfish-windows-arm64-universal.zip"
    elif system == "linux":
        if machine in ("x86_64", "amd64"):
            return "stockfish-linux-x86-64-universal.tar.gz"
        if machine in ("aarch64", "arm64"):
            return "stockfish-linux-arm64-universal.tar.gz"
    elif system == "darwin":
        return "stockfish-macos-universal.tar.gz"

    raise StockfishProvisionError(
        f"No known Stockfish build for platform system={system!r} machine={machine!r}. "
        "Install Stockfish manually and point chesswhisper at it via --engine-path or the "
        "CHESSWHISPER_STOCKFISH_PATH environment variable."
    )


def resolve_installed_stockfish(explicit_path: str | None = None) -> str | None:
    """Returns a path to an already-installed Stockfish, or None if not found.

    Checks, in order: explicit_path, CHESSWHISPER_STOCKFISH_PATH env var, PATH via shutil.which.
    """
    if explicit_path:
        return explicit_path
    env_path = os.environ.get("CHESSWHISPER_STOCKFISH_PATH")
    if env_path:
        return env_path
    return shutil.which("stockfish")


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                fh.write(chunk)


def _extract(archive_path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dest_dir)
    else:
        with tarfile.open(archive_path) as tf:
            tf.extractall(dest_dir)


def _find_binary(root: Path) -> Path:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith("stockfish") and (name.endswith(".exe") or "." not in name):
            return path
    raise StockfishProvisionError(f"No Stockfish binary found under {root}")


def _verify_binary(path: Path, timeout: float = 10.0) -> bool:
    try:
        proc = subprocess.Popen(
            [str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
        )
    except OSError:
        return False

    try:
        stdout, _ = proc.communicate(input="uci\n", timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return False

    return "uciok" in stdout


def get_stockfish_path(app_data_dir: Path | None = None) -> Path:
    import platform

    base_dir = Path(app_data_dir) if app_data_dir else Path(user_data_dir("chesswhisper"))
    version_dir = base_dir / STOCKFISH_TAG_DIRNAME

    if version_dir.is_dir():
        try:
            cached_binary = _find_binary(version_dir)
            if _verify_binary(cached_binary):
                return cached_binary
        except StockfishProvisionError:
            pass  # fall through and (re-)download

    asset_name = asset_name_for_platform(platform.system(), platform.machine())
    archive_path = base_dir / asset_name
    try:
        _download(f"{_RELEASE_BASE_URL}/{asset_name}", archive_path)
        _extract(archive_path, version_dir)
    except (OSError, requests.RequestException, zipfile.BadZipFile, tarfile.TarError) as exc:
        raise StockfishProvisionError(
            f"Failed to download or extract Stockfish: {exc}"
        ) from exc
    finally:
        archive_path.unlink(missing_ok=True)

    binary = _find_binary(version_dir)
    if os.name != "nt":
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    if not _verify_binary(binary):
        raise StockfishProvisionError(
            f"Downloaded Stockfish binary at {binary} did not respond to a UCI handshake."
        )

    return binary
