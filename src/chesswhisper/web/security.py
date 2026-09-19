from __future__ import annotations

import os
import secrets
from pathlib import Path

import bcrypt
from cryptography.fernet import Fernet
from platformdirs import user_data_dir


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def _app_data_dir() -> Path:
    path = Path(user_data_dir("chesswhisper"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_or_create_secret_bytes(filename: str, generator) -> bytes:
    path = _app_data_dir() / filename
    if path.exists():
        return path.read_bytes()
    value = generator()
    path.write_bytes(value)
    os.chmod(path, 0o600)
    return value


def get_session_secret() -> bytes:
    return _get_or_create_secret_bytes("session_secret.key", lambda: secrets.token_bytes(32))


def get_encryption_key() -> bytes:
    return _get_or_create_secret_bytes("api_key_encryption.key", Fernet.generate_key)


def encrypt_api_key(plaintext: str) -> str:
    return Fernet(get_encryption_key()).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_api_key(ciphertext: str) -> str:
    return Fernet(get_encryption_key()).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
