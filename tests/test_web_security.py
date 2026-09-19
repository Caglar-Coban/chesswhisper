import chesswhisper.web.security as security


def test_password_hash_and_verify_roundtrip():
    hashed = security.hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"
    assert security.verify_password("correct horse battery staple", hashed) is True
    assert security.verify_password("wrong password", hashed) is False


def test_get_session_secret_persists_across_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "_app_data_dir", lambda: tmp_path)

    first = security.get_session_secret()
    second = security.get_session_secret()

    assert first == second
    assert len(first) == 32


def test_get_encryption_key_persists_across_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "_app_data_dir", lambda: tmp_path)

    first = security.get_encryption_key()
    second = security.get_encryption_key()

    assert first == second


def test_encrypt_decrypt_api_key_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "_app_data_dir", lambda: tmp_path)

    ciphertext = security.encrypt_api_key("sk-ant-super-secret")

    assert ciphertext != "sk-ant-super-secret"
    assert security.decrypt_api_key(ciphertext) == "sk-ant-super-secret"


def test_session_secret_and_encryption_key_are_different_files(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "_app_data_dir", lambda: tmp_path)

    security.get_session_secret()
    security.get_encryption_key()

    assert (tmp_path / "session_secret.key").exists()
    assert (tmp_path / "api_key_encryption.key").exists()
