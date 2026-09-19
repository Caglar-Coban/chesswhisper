# chesswhisper Multi-User Web App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `chesswhisper serve`'s web UI from a no-login single-shot tool into a local multi-user app: accounts, encrypted per-user LLM API keys, persistent analysis history, and an admin panel that can see every user's data.

**Architecture:** A new SQLite-backed data layer (`chesswhisper.web.db`/`models`) and security helpers (`chesswhisper.web.security`) sit underneath the existing FastAPI app. Session-cookie auth (`chesswhisper.web.auth`) gates every route except register/login. The old no-login `/` and `/analyze` routes are replaced (not kept alongside) by authenticated versions that resolve the logged-in user's own decrypted API key and save an `AnalysisRecord` after each run. `run_analysis`/`_build_llm_provider` (in `cli.py`, unchanged for the CLI itself) gain one new optional parameter to accept an explicit API key instead of only reading the environment.

**Tech Stack:** FastAPI, SQLAlchemy 2.x, bcrypt, `cryptography` (Fernet), Starlette `SessionMiddleware`, Jinja2. All new dependencies live in the existing `web` extra — the CLI's base install is untouched.

**Spec:** `docs/superpowers/specs/2026-09-19-chesswhisper-multiuser-design.md`

## Global Constraints

- The CLI (`chesswhisper analyze`) does not change behavior. `run_analysis`'s new parameter must default to preserving today's env-var-based behavior exactly.
- Deployment model is local-only (binds to `127.0.0.1`), not internet-facing — no TLS, no email verification, no rate limiting, no CSRF tokens (per spec's stated threat model).
- Passwords: bcrypt, never stored or logged in plaintext.
- API keys: encrypted at rest with Fernet; the encryption key and the session-signing key are two separate secrets, both persisted under `platformdirs.user_data_dir("chesswhisper")`.
- Database: a single SQLite file in that same app-data directory, tables created via `SQLAlchemy`'s `Base.metadata.create_all()` — no migrations tooling for v1.
- No CSRF protection, no rate limiting, no account lockout — explicitly out of scope per spec.
- `/history/{id}` for a record that isn't the current user's returns 404, not 403 (don't reveal existence).
- Old no-login web routes are removed entirely, along with their now-obsolete tests — not kept alongside the new flow.
- All new tests use `fastapi.testclient.TestClient` against a temporary SQLite file (`tmp_path`) and a mocked Stockfish engine (`chesswhisper.cli._open_engine`) — no real network, engine, or LLM calls.

---

## File Structure

```
src/chesswhisper/
  cli.py                          # MODIFY: add create-admin command; extend run_analysis/_build_llm_provider
  web/
    db.py                         # NEW: engine, Base, init_db(), get_db() FastAPI dependency
    models.py                     # NEW: User, UserApiKey, AnalysisRecord
    security.py                   # NEW: password hashing, secret-key management, API key encrypt/decrypt
    auth.py                       # NEW: get_current_user, get_optional_current_user, require_admin, RedirectToLogin, Forbidden
    app.py                        # MODIFY: SessionMiddleware, exception handlers, all new routes, replace old / and /analyze
    templates/
      base.html                   # MODIFY: nav bar (History/Settings/Admin/Logout) when current_user is set
      index.html                  # MODIFY: hint text about configuring keys in Settings
      result.html                 # MODIFY: "saved to history" note
      error.html                  # unchanged
      register.html               # NEW
      login.html                  # NEW
      settings.html                # NEW
      history_list.html           # NEW (shared by /history and the admin per-user drill-down)
      history_detail.html         # NEW (shared by /history/{id} and the admin per-user drill-down)
      admin_users.html            # NEW
pyproject.toml                    # MODIFY: add sqlalchemy, bcrypt, cryptography, itsdangerous to the web extra
tests/
  test_web_db.py                  # NEW
  test_web_security.py            # NEW
  test_web_auth.py                # NEW
  test_web_register_login.py      # NEW
  test_web_analyze_authenticated.py  # NEW (replaces the removed parts of test_web_app.py)
  test_web_settings.py            # NEW
  test_web_history.py             # NEW
  test_web_admin.py               # NEW
  test_web_app.py                 # MODIFY: remove tests of the old no-login /, /analyze (superseded above); keep index-page-shape and sanitization tests, adapted to require login
  test_cli.py                     # MODIFY: add create-admin tests
```

---

### Task 1: Data layer — `db.py` and `models.py`

**Files:**
- Create: `src/chesswhisper/web/db.py`
- Create: `src/chesswhisper/web/models.py`
- Modify: `pyproject.toml` (add `sqlalchemy>=2.0` to the `web` extra)
- Test: `tests/test_web_db.py`

**Interfaces:**
- Produces:
  ```python
  # db.py
  class Base(DeclarativeBase): ...
  def get_engine(db_path: "Path | None" = None): ...  # sqlalchemy Engine
  def init_db(db_path: "Path | None" = None) -> None: ...
  def get_db() -> Generator[Session, None, None]: ...  # FastAPI dependency

  # models.py
  class User(Base):
      id: int; username: str; password_hash: str; is_admin: bool; created_at: datetime
  class UserApiKey(Base):
      user_id: int; anthropic_key_encrypted: "str | None"; openai_key_encrypted: "str | None"; updated_at: datetime
  class AnalysisRecord(Base):
      id: int; user_id: int; created_at: datetime; player_name: str; lang: str; llm_used: str
      games_count: int; overall_acpl: float; overall_accuracy: float; report_markdown: str
  ```

- [ ] **Step 1: Add the SQLAlchemy dependency**

Edit `pyproject.toml`'s `web` extra:

```toml
web = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "python-multipart>=0.0.9", "jinja2>=3.1", "markdown>=3.6", "bleach>=6.1", "sqlalchemy>=2.0"]
```

Run: `pip install -e ".[dev,web]"` to install it.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_web_db.py
from chesswhisper.web.db import init_db, get_db
from chesswhisper.web.models import User


def test_init_db_creates_tables_and_supports_crud(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)

    db_gen = get_db()
    db = next(db_gen)
    try:
        db.add(User(username="alice", password_hash="hash", is_admin=False))
        db.commit()

        found = db.query(User).filter_by(username="alice").one()
        assert found.username == "alice"
        assert found.is_admin is False
        assert found.created_at is not None
    finally:
        db_gen.close()

    assert db_path.exists()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_web_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.web.db'`

- [ ] **Step 4: Implement `db.py`**

```python
# src/chesswhisper/web/db.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from platformdirs import user_data_dir
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_db_path() -> Path:
    return Path(user_data_dir("chesswhisper")) / "chesswhisper.db"


def get_engine(db_path: Path | None = None):
    path = db_path or _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


_engine = None
_SessionLocal = None


def init_db(db_path: Path | None = None) -> None:
    global _engine, _SessionLocal
    _engine = get_engine(db_path)
    from chesswhisper.web import models  # noqa: F401 -- registers tables on Base.metadata

    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    if _SessionLocal is None:
        init_db()
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 5: Implement `models.py`**

```python
# src/chesswhisper/web/models.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chesswhisper.web.db import Base, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    api_key: Mapped["UserApiKey | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    analyses: Mapped[list["AnalysisRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserApiKey(Base):
    __tablename__ = "user_api_keys"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    anthropic_key_encrypted: Mapped[str | None] = mapped_column(Text, default=None)
    openai_key_encrypted: Mapped[str | None] = mapped_column(Text, default=None)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped["User"] = relationship(back_populates="api_key")


class AnalysisRecord(Base):
    __tablename__ = "analysis_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    player_name: Mapped[str] = mapped_column(String(200))
    lang: Mapped[str] = mapped_column(String(8))
    llm_used: Mapped[str] = mapped_column(String(20))
    games_count: Mapped[int]
    overall_acpl: Mapped[float]
    overall_accuracy: Mapped[float]
    report_markdown: Mapped[str] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="analyses")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_web_db.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/chesswhisper/web/db.py src/chesswhisper/web/models.py tests/test_web_db.py
git commit -m "feat: add SQLite-backed data layer for multi-user web app"
```

---

### Task 2: Security helpers — `security.py`

**Files:**
- Create: `src/chesswhisper/web/security.py`
- Modify: `pyproject.toml` (add `bcrypt>=4.0`, `cryptography>=42` to the `web` extra)
- Test: `tests/test_web_security.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  ```python
  def hash_password(password: str) -> str: ...
  def verify_password(password: str, password_hash: str) -> bool: ...
  def get_session_secret() -> bytes: ...
  def get_encryption_key() -> bytes: ...
  def encrypt_api_key(plaintext: str) -> str: ...
  def decrypt_api_key(ciphertext: str) -> str: ...
  ```

- [ ] **Step 1: Add dependencies**

```toml
web = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "python-multipart>=0.0.9", "jinja2>=3.1", "markdown>=3.6", "bleach>=6.1", "sqlalchemy>=2.0", "bcrypt>=4.0", "cryptography>=42"]
```

Run: `pip install -e ".[dev,web]"`

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_web_security.py
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_web_security.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.web.security'`

- [ ] **Step 4: Implement `security.py`**

```python
# src/chesswhisper/web/security.py
from __future__ import annotations

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
    return value


def get_session_secret() -> bytes:
    return _get_or_create_secret_bytes("session_secret.key", lambda: secrets.token_bytes(32))


def get_encryption_key() -> bytes:
    return _get_or_create_secret_bytes("api_key_encryption.key", Fernet.generate_key)


def encrypt_api_key(plaintext: str) -> str:
    return Fernet(get_encryption_key()).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_api_key(ciphertext: str) -> str:
    return Fernet(get_encryption_key()).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_web_security.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/chesswhisper/web/security.py tests/test_web_security.py
git commit -m "feat: add password hashing and API-key encryption helpers"
```

---

### Task 3: Auth dependencies — `auth.py`

**Files:**
- Create: `src/chesswhisper/web/auth.py`
- Test: `tests/test_web_auth.py`

**Interfaces:**
- Consumes: `get_db` (Task 1), `User` (Task 1).
- Produces:
  ```python
  class RedirectToLogin(Exception): ...
  class Forbidden(Exception): ...

  def get_current_user(request: Request, db: Session = Depends(get_db)) -> User: ...
      # raises RedirectToLogin if request.session has no valid "user_id"
  def get_optional_current_user(request: Request, db: Session = Depends(get_db)) -> "User | None": ...
      # returns None instead of raising -- for public pages that behave
      # differently when already logged in (register/login)
  def require_admin(user: User = Depends(get_current_user)) -> User: ...
      # raises Forbidden if not user.is_admin
  ```

- [ ] **Step 1: Write the failing tests**

These build a tiny standalone FastAPI app inline, using only the dependencies under test, so this task is verifiable before any real route exists.

```python
# tests/test_web_auth.py
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from chesswhisper.web.auth import Forbidden, RedirectToLogin, get_current_user, require_admin
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import User


@pytest.fixture
def app_and_db(tmp_path):
    init_db(tmp_path / "auth_test.db")
    db_gen = get_db()
    db = next(db_gen)
    plain_user = User(username="plain", password_hash="x", is_admin=False)
    admin_user = User(username="root", password_hash="x", is_admin=True)
    db.add_all([plain_user, admin_user])
    db.commit()
    db.refresh(plain_user)
    db.refresh(admin_user)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.exception_handler(RedirectToLogin)
    async def _redirect(request, exc):
        from starlette.responses import RedirectResponse
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(Forbidden)
    async def _forbidden(request, exc):
        from starlette.responses import PlainTextResponse
        return PlainTextResponse("forbidden", status_code=403)

    @app.get("/whoami")
    def whoami(user: User = Depends(get_current_user)):
        return {"username": user.username}

    @app.get("/admin-only")
    def admin_only(user: User = Depends(require_admin)):
        return {"username": user.username}

    @app.post("/login-as/{user_id}")
    def login_as(user_id: int, request):
        request.session["user_id"] = user_id
        return {"ok": True}

    return app, plain_user.id, admin_user.id


def test_get_current_user_redirects_when_no_session(app_and_db):
    app, _, _ = app_and_db
    client = TestClient(app, follow_redirects=False)

    response = client.get("/whoami")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_get_current_user_returns_user_with_valid_session(app_and_db):
    app, plain_user_id, _ = app_and_db
    client = TestClient(app)
    client.post(f"/login-as/{plain_user_id}")

    response = client.get("/whoami")

    assert response.status_code == 200
    assert response.json() == {"username": "plain"}


def test_require_admin_forbids_non_admin(app_and_db):
    app, plain_user_id, _ = app_and_db
    client = TestClient(app)
    client.post(f"/login-as/{plain_user_id}")

    response = client.get("/admin-only")

    assert response.status_code == 403


def test_require_admin_allows_admin(app_and_db):
    app, _, admin_user_id = app_and_db
    client = TestClient(app)
    client.post(f"/login-as/{admin_user_id}")

    response = client.get("/admin-only")

    assert response.status_code == 200
    assert response.json() == {"username": "root"}
```

Note: the `login_as` test route takes `request` as a plain parameter (FastAPI injects `Request` automatically when a parameter is type-hinted `Request` — add `from starlette.requests import Request` and annotate it: `def login_as(user_id: int, request: Request):`). Use that exact annotation when writing the fixture.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.web.auth'`

- [ ] **Step 3: Implement `auth.py`**

```python
# src/chesswhisper/web/auth.py
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session
from starlette.requests import Request

from chesswhisper.web.db import get_db
from chesswhisper.web.models import User


class RedirectToLogin(Exception):
    pass


class Forbidden(Exception):
    pass


def get_optional_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return db.get(User, user_id)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_optional_current_user(request, db)
    if user is None:
        raise RedirectToLogin()
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise Forbidden()
    return user
```

- [ ] **Step 4: Fix the test fixture's `login_as` route signature**

In `tests/test_web_auth.py`, make sure the import and signature read exactly:

```python
from starlette.requests import Request

# ...

@app.post("/login-as/{user_id}")
def login_as(user_id: int, request: Request):
    request.session["user_id"] = user_id
    return {"ok": True}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_web_auth.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/chesswhisper/web/auth.py tests/test_web_auth.py
git commit -m "feat: add session-based auth dependencies (get_current_user, require_admin)"
```

---

### Task 4: Register / Login / Logout + session middleware wired into the real app

**Files:**
- Modify: `src/chesswhisper/web/app.py`
- Modify: `src/chesswhisper/web/templates/base.html`
- Create: `src/chesswhisper/web/templates/register.html`
- Create: `src/chesswhisper/web/templates/login.html`
- Modify: `pyproject.toml` (add `itsdangerous>=2.1` to the `web` extra — required by `SessionMiddleware`)
- Test: `tests/test_web_register_login.py`

**Interfaces:**
- Consumes: `init_db`/`get_db` (Task 1), `hash_password`/`verify_password`/`get_session_secret` (Task 2), `get_optional_current_user`/`get_current_user` (Task 3), `User` (Task 1).
- Produces: `/register` (GET, POST), `/login` (GET, POST), `/logout` (POST) on the real `app` object. The real `/` and `/analyze` routes from the old no-login flow **still exist unchanged in this task** — Task 5 replaces them. This task only adds the new auth routes and the session middleware alongside what's already there.

- [ ] **Step 1: Add the last new dependency**

```toml
web = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "python-multipart>=0.0.9", "jinja2>=3.1", "markdown>=3.6", "bleach>=6.1", "sqlalchemy>=2.0", "bcrypt>=4.0", "cryptography>=42", "itsdangerous>=2.1"]
```

Run: `pip install -e ".[dev,web]"`

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_web_register_login.py
import pytest
from fastapi.testclient import TestClient

from chesswhisper.web.app import app
from chesswhisper.web.db import init_db


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "register_login_test.db")
    return TestClient(app, follow_redirects=False)


def test_register_creates_user_and_logs_in(client):
    response = client.post("/register", data={"username": "alice", "password": "correct-horse"})

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    # Starlette's SessionMiddleware uses a cookie named "session" by default --
    # its presence means register() wrote request.session["user_id"].
    assert "session" in client.cookies


def test_register_rejects_duplicate_username(client):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})

    response = client.post("/register", data={"username": "alice", "password": "another-pass"})

    assert response.status_code == 200  # re-rendered form, not a redirect
    assert "zaten" in response.text.lower() or "already" in response.text.lower()


def test_register_rejects_short_password(client):
    response = client.post("/register", data={"username": "bob", "password": "short"})

    assert response.status_code == 200
    assert "8" in response.text


def test_login_with_correct_credentials_succeeds(client):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    client.post("/logout")

    response = client.post("/login", data={"username": "alice", "password": "correct-horse"})

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_with_wrong_password_fails(client):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    client.post("/logout")

    response = client.post("/login", data={"username": "alice", "password": "wrong-password"})

    assert response.status_code == 200
    assert "hata" in response.text.lower() or "incorrect" in response.text.lower() or "yanlış" in response.text.lower()


def test_logout_clears_session(client):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    assert "session" in client.cookies

    response = client.post("/logout")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    # Starlette's SessionMiddleware deletes the cookie once the session dict is empty.
    assert "session" not in client.cookies
```

Note: this test deliberately does not check that `/` becomes inaccessible after logout — `/` is still the old unauthenticated route at this point in the plan and isn't touched until Task 5. Checking the session cookie directly keeps this test meaningful without depending on a later task's changes.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_web_register_login.py -v`
Expected: FAIL — `/register`, `/login`, `/logout` don't exist (404s), or `AttributeError` if session middleware isn't installed yet.

- [ ] **Step 4: Add nav-aware markup to `base.html`**

Add this CSS to the existing `<style>` block in `base.html` (insert right after the `.back-link:hover` rule):

```css
  header.site-header { justify-content: space-between; }
  .nav-links { display: flex; align-items: center; gap: 16px; font-size: 14px; }
  .nav-links a { text-decoration: none; }
  .nav-links a:hover { text-decoration: underline; }
  .link-button {
    background: none; border: none; padding: 0; margin: 0;
    color: var(--accent); font-size: 14px; font-weight: 400;
    cursor: pointer; text-decoration: none;
  }
  .link-button:hover { text-decoration: underline; background: none; }
```

Change the `<header class="site-header">` block to:

```html
  <header class="site-header">
    <div style="display:flex; align-items:center; gap:12px;">
      <span class="logo">&#9822;</span>
      <div>
        <h1>chesswhisper</h1>
        <p class="tagline">Stockfish + LLM ile satranç analiz ve koçluk aracı</p>
      </div>
    </div>
    {% if current_user %}
    <nav class="nav-links">
      <a href="/">Analiz</a>
      <form method="post" action="/logout" style="display:inline; margin:0;">
        <button type="submit" class="link-button">Çıkış ({{ current_user.username }})</button>
      </form>
    </nav>
    {% endif %}
  </header>
```

(The `History`/`Settings`/`Admin` links are added to this nav in later tasks, once those routes exist.)

- [ ] **Step 5: Create `register.html` and `login.html`**

```html
{# src/chesswhisper/web/templates/register.html #}
{% extends "base.html" %}
{% block title %}chesswhisper — Kayıt Ol{% endblock %}
{% block content %}
<div class="card">
  {% if error %}<div class="banner error">{{ error }}</div>{% endif %}
  <form method="post" action="/register">
    <label for="username">Kullanıcı adı</label>
    <input type="text" id="username" name="username" required>
    <label for="password">Parola</label>
    <input type="password" id="password" name="password" required minlength="8">
    <p class="hint">En az 8 karakter.</p>
    <button type="submit">Kayıt Ol</button>
  </form>
  <p class="hint" style="margin-top:16px;">Zaten hesabın var mı? <a href="/login">Giriş yap</a></p>
</div>
{% endblock %}
```

```html
{# src/chesswhisper/web/templates/login.html #}
{% extends "base.html" %}
{% block title %}chesswhisper — Giriş{% endblock %}
{% block content %}
<div class="card">
  {% if error %}<div class="banner error">{{ error }}</div>{% endif %}
  <form method="post" action="/login">
    <label for="username">Kullanıcı adı</label>
    <input type="text" id="username" name="username" required>
    <label for="password">Parola</label>
    <input type="password" id="password" name="password" required>
    <button type="submit">Giriş Yap</button>
  </form>
  <p class="hint" style="margin-top:16px;">Hesabın yok mu? <a href="/register">Kayıt ol</a></p>
</div>
{% endblock %}
```

- [ ] **Step 6: Wire session middleware, exception handlers, and the three routes into `app.py`**

`app.py` already imports `FastAPI, File, Form, Request, UploadFile` from `fastapi` and `HTMLResponse` from `fastapi.responses` — do not re-add those. Change the existing `from fastapi import FastAPI, File, Form, Request, UploadFile` line to also pull in `Depends`:

```python
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
```

Then add these genuinely new imports alongside the existing ones:

```python
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from chesswhisper.web.auth import Forbidden, RedirectToLogin, get_current_user, get_optional_current_user
from chesswhisper.web.db import get_db
from chesswhisper.web.models import User
from chesswhisper.web.security import get_session_secret, hash_password, verify_password
```

Right after `app = FastAPI(title="chesswhisper")`, add:

```python
app.add_middleware(SessionMiddleware, secret_key=get_session_secret())


@app.exception_handler(RedirectToLogin)
async def _redirect_to_login(request: Request, exc: RedirectToLogin) -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden_handler(request: Request, exc: Forbidden) -> HTMLResponse:
    return _render_error(request, "Bu sayfaya erişim yetkiniz yok.", 403)
```

Add these three routes (anywhere after the exception handlers, before the existing `/` route):

```python
@app.get("/register", response_class=HTMLResponse)
async def register_form(request: Request, current_user: User | None = Depends(get_optional_current_user)) -> HTMLResponse:
    if current_user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "register.html", {"current_user": None})


@app.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if len(password) < 8:
        return templates.TemplateResponse(
            request, "register.html",
            {"current_user": None, "error": "Parola en az 8 karakter olmalı."},
            status_code=200,
        )
    if db.query(User).filter_by(username=username).first() is not None:
        return templates.TemplateResponse(
            request, "register.html",
            {"current_user": None, "error": "Bu kullanıcı adı zaten kayıtlı."},
            status_code=200,
        )

    user = User(username=username, password_hash=hash_password(password), is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)

    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, current_user: User | None = Depends(get_optional_current_user)) -> HTMLResponse:
    if current_user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"current_user": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = db.query(User).filter_by(username=username).first()
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html",
            {"current_user": None, "error": "Kullanıcı adı veya parola yanlış."},
            status_code=200,
        )

    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
```

You'll also need `from fastapi import Depends` and `from sqlalchemy.orm import Session` already imported near the top if not already present — check the existing imports first and only add what's missing.

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_web_register_login.py -v`
Expected: PASS (6 tests).

Then also run: `pytest -v` (the whole suite). `/` and `/analyze` are untouched in this task, so the existing `test_web_app.py` tests should still pass unchanged — this task is purely additive (new routes, new middleware, no behavior change to existing ones). If anything in `test_web_app.py` breaks, it's almost certainly the `SessionMiddleware` needing a `secret_key`, which Step 6 already supplies via `get_session_secret()` — don't proceed to Task 5 with a red suite.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/chesswhisper/web/app.py src/chesswhisper/web/templates/base.html src/chesswhisper/web/templates/register.html src/chesswhisper/web/templates/login.html tests/test_web_register_login.py
git commit -m "feat: add register/login/logout with session-cookie auth"
```

---

### Task 5: Authenticated `/` and `/analyze`, per-user API keys, saved history

**Files:**
- Modify: `src/chesswhisper/cli.py` (extend `_build_llm_provider` and `run_analysis` with an optional API key)
- Modify: `src/chesswhisper/web/app.py` (replace the old `/` and `/analyze` with authenticated versions)
- Modify: `src/chesswhisper/web/templates/index.html` (hint about Settings)
- Modify: `src/chesswhisper/web/templates/result.html` (saved-to-history note)
- Modify: `tests/test_web_app.py` (remove the now-superseded no-login tests)
- Test: `tests/test_web_analyze_authenticated.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4, plus the existing `run_analysis`, `render_markdown`, `StockfishProvisionError`.
- Produces: `_build_llm_provider(name: str, api_key: "str | None" = None)`, `run_analysis(..., llm_api_key: "str | None" = None)` (both in `cli.py`, both backward compatible — existing callers passing nothing behave exactly as before).

- [ ] **Step 1: Extend `_build_llm_provider` and `run_analysis` in `cli.py`**

Read the current `_build_llm_provider` and `run_analysis` in `src/chesswhisper/cli.py` first — you're modifying, not rewriting, the existing functions. Change the signatures and the two call sites inside `_build_llm_provider` that construct a provider, like this:

```python
def _build_llm_provider(name: str, api_key: str | None = None):
    if name == "none":
        return NoneProvider()
    if name == "claude":
        from chesswhisper.llm.claude_provider import ClaudeProvider
        return ClaudeProvider(api_key=api_key)
    if name == "openai":
        from chesswhisper.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key)
    raise ValueError(f"Unknown LLM provider: {name}")
```

And add one new parameter to `run_analysis`'s signature (`llm_api_key=None`), passed through to `_build_llm_provider`:

```python
def run_analysis(
    pgn_path,
    player,
    lang,
    llm_name,
    movetime=300,
    depth=None,
    engine_path=None,
    llm_api_key=None,
):
    """... (existing docstring, plus:)

    llm_api_key, if given, is passed straight to the chosen LLM provider
    instead of letting it fall back to reading from the environment --
    used by the web UI to supply a specific logged-in user's own key.
    """
    # ... existing body unchanged until the commentary block ...

    commentary = ""
    if llm_name != "none":
        try:
            provider = _build_llm_provider(llm_name, api_key=llm_api_key)
            commentary = provider.generate_commentary(aggregate, lang)
        except LLMProviderError as exc:
            warnings.append(f"LLM commentary unavailable ({exc}); writing stats-only report.")

    return aggregate, commentary, warnings
```

Everything else in `run_analysis` and the `analyze` Click command stays exactly as it is — the CLI's own call to `run_analysis(...)` never passes `llm_api_key`, so `api_key=None` flows to `ClaudeProvider`/`OpenAIProvider`, which already fall back to `os.environ.get(...)` when `api_key` is `None` (see their existing `__init__` methods) — this is a no-op change for the CLI.

- [ ] **Step 2: Add a test proving the API-key passthrough works**

Add to `tests/test_cli.py`:

```python
def test_run_analysis_passes_explicit_api_key_to_provider(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    captured = {}

    class FakeProvider:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key

        def generate_commentary(self, report, lang):
            return "ok"

    monkeypatch.setattr("chesswhisper.llm.claude_provider.ClaudeProvider", FakeProvider)

    run_analysis(str(pgn_path), "Alice", "en", "claude", llm_api_key="sk-explicit-key")

    assert captured["api_key"] == "sk-explicit-key"
```

Run: `pytest tests/test_cli.py -k passes_explicit_api_key -v`
Expected: FAIL first (RED, since `run_analysis` doesn't accept `llm_api_key` yet if you run this before Step 1 -- if you already did Step 1, it should PASS immediately; either order is fine, but confirm you saw it fail at some point before it passed, per TDD).
Then confirm: PASS.

- [ ] **Step 3: Write the failing web tests**

```python
# tests/test_web_analyze_authenticated.py
import textwrap

import chess.engine
import pytest
from fastapi.testclient import TestClient

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord, UserApiKey
from chesswhisper.web.security import encrypt_api_key

SAMPLE_PGN = textwrap.dedent("""
    [Event "Test"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
""").strip() + "\n"


class FakeEngine:
    def __init__(self):
        self._scores = [0, 20, 10, 15]
        self._i = 0

    def analyse(self, board, limit, **kwargs):
        cp = self._scores[self._i % len(self._scores)]
        self._i += 1
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE)}


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "analyze_test.db")
    c = TestClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def test_index_redirects_when_logged_out(tmp_path):
    init_db(tmp_path / "logged_out_test.db")
    client = TestClient(app, follow_redirects=False)

    response = client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_index_shows_form_when_logged_in(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'name="pgn"' in response.text


def test_analyze_saves_history_record_for_current_user(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 200
    assert "chesswhisper Analysis Report" in response.text

    db = next(get_db())
    records = db.query(AnalysisRecord).all()
    assert len(records) == 1
    assert records[0].player_name == "Alice"
    assert records[0].games_count == 1


def test_analyze_without_configured_key_redirects_to_settings(client, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("run_analysis should not be called without a configured key")

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fail_if_called)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "claude"},
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/settings")


def test_analyze_uses_current_users_decrypted_key(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    db = next(get_db())
    from chesswhisper.web.models import User
    user = db.query(User).filter_by(username="alice").one()
    db.add(UserApiKey(user_id=user.id, anthropic_key_encrypted=encrypt_api_key("sk-alices-key")))
    db.commit()

    captured = {}

    def fake_run_analysis(pgn_path, player, lang, llm_name, llm_api_key=None, **kwargs):
        captured["llm_api_key"] = llm_api_key
        from chesswhisper.stats_engine import PHASES, AggregateReport, ErrorCounts, TimeManagementStats
        return (
            AggregateReport(
                player="Alice", games=[], overall_acpl=0.0, overall_accuracy=100.0,
                error_totals={p: ErrorCounts() for p in PHASES}, openings=[],
                time_management=TimeManagementStats(available=False),
            ),
            "",
            [],
        )

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "claude"},
    )

    assert response.status_code == 200
    assert captured["llm_api_key"] == "sk-alices-key"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_web_analyze_authenticated.py -v`
Expected: FAIL — `/` and `/analyze` don't require login yet, `AnalysisRecord` is never created, no key-lookup redirect exists.

- [ ] **Step 5: Update `index.html`'s hint text**

In `src/chesswhisper/web/templates/index.html`, change this line:

```html
    <p class="hint">Claude/OpenAI seçilirse sunucu ortamında ilgili API anahtarının tanımlı olması gerekir.</p>
```

to:

```html
    <p class="hint">Claude/OpenAI seçilirse, önce <a href="/settings">Ayarlar</a> sayfasından kendi API anahtarını girmelisin.</p>
```

- [ ] **Step 6: Add a saved-to-history note to `result.html`**

In `src/chesswhisper/web/templates/result.html`, right after the opening `{% block content %}`, add:

```html
{% if saved %}
<p class="hint">Bu analiz geçmişine kaydedildi.</p>
{% endif %}
```

- [ ] **Step 7: Replace the old `/` and `/analyze` routes in `app.py`**

Find the existing `index` and `analyze` route functions in `src/chesswhisper/web/app.py` and replace both entirely with:

```python
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, current_user: User = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"current_user": current_user})


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(
    request: Request,
    pgn: UploadFile | None = File(default=None),  # noqa: B008 - FastAPI's own idiom
    player: str = Form(default=""),
    lang: str = Form(default="tr"),
    llm: str = Form(default="none"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if pgn is None or not pgn.filename:
        return _render_error(request, "Lütfen analiz edilecek bir PGN dosyası seçin.", 400, current_user)

    llm_api_key = None
    if llm != "none":
        key_row = db.get(UserApiKey, current_user.id)
        encrypted = None
        if key_row is not None:
            encrypted = key_row.anthropic_key_encrypted if llm == "claude" else key_row.openai_key_encrypted
        if not encrypted:
            return RedirectResponse("/settings?message=missing_key", status_code=303)
        llm_api_key = decrypt_api_key(encrypted)

    contents = await pgn.read()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pgn", delete=False) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        try:
            aggregate, commentary, warnings = run_analysis(
                tmp_path, player or None, lang, llm, llm_api_key=llm_api_key
            )
        except (OSError, ValueError) as exc:
            return _render_error(request, str(exc), 400, current_user)
        except StockfishProvisionError as exc:
            return _render_error(request, f"Motor kurulum hatası: {exc}", 500, current_user)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    report_markdown = render_markdown(aggregate, commentary, lang)

    record = AnalysisRecord(
        user_id=current_user.id,
        player_name=aggregate.player,
        lang=lang,
        llm_used=llm,
        games_count=len(aggregate.games),
        overall_acpl=aggregate.overall_acpl,
        overall_accuracy=aggregate.overall_accuracy,
        report_markdown=report_markdown,
    )
    db.add(record)
    db.commit()

    report_html = _report_to_safe_html(report_markdown)

    return templates.TemplateResponse(
        request, "result.html",
        {"report_html": report_html, "warnings": warnings, "current_user": current_user, "saved": True},
    )
```

Update the `_render_error` helper to accept and forward an optional `current_user` (so nav still renders on error pages when the visitor is logged in):

```python
def _render_error(
    request: Request, message: str, status_code: int, current_user: User | None = None
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "error.html", {"message": message, "current_user": current_user}, status_code=status_code
    )
```

Update the module-level `_unhandled_exception_handler` to pass `current_user=None` explicitly (it can't cheaply resolve the user without risking a second failure inside the handler itself):

```python
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> HTMLResponse:
    return _render_error(request, "Beklenmeyen bir hata oluştu.", 500, None)
```

Add the new imports this route needs, alongside the existing ones at the top of `app.py`:

```python
from chesswhisper.cli import run_analysis
from chesswhisper.web.models import AnalysisRecord, UserApiKey
from chesswhisper.web.security import decrypt_api_key
```

(`run_analysis` is likely already imported from a prior task's edits — check before adding a duplicate import.)

- [ ] **Step 8: Remove the now-superseded tests from `test_web_app.py`**

Open `tests/test_web_app.py`. Delete these test functions entirely, since they test the old no-login behavior that no longer exists:
- `test_index_returns_upload_form` (superseded by `test_index_shows_form_when_logged_in` in the new test file)
- `test_analyze_returns_report_html_for_valid_pgn` (superseded by `test_analyze_saves_history_record_for_current_user`)
- `test_analyze_unknown_player_returns_400` (the underlying error path is unchanged, but re-add it under login in the new test file instead of here — do not keep an unauthenticated version)

Keep `test_analyze_without_file_returns_400`, `test_analyze_sanitizes_html_injected_via_pgn_headers`, and `test_analyze_engine_provisioning_failure_returns_500`, but add a `client.post("/register", data={"username": "alice", "password": "correct-horse"})` call at the top of each (using the same `client` fixture pattern as `test_web_analyze_authenticated.py`) so they still exercise a logged-in request — these three test error paths that are still fully in scope for the authenticated route.

Also add a `player` field mismatch test to `tests/test_web_analyze_authenticated.py`:

```python
def test_analyze_unknown_player_returns_400(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Nobody", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 400
    assert "Hata" in response.text
```

- [ ] **Step 9: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass, including the updated `test_web_app.py` and the new `test_web_analyze_authenticated.py`.

- [ ] **Step 10: Commit**

```bash
git add src/chesswhisper/cli.py src/chesswhisper/web/app.py src/chesswhisper/web/templates/index.html src/chesswhisper/web/templates/result.html tests/test_cli.py tests/test_web_app.py tests/test_web_analyze_authenticated.py
git commit -m "feat: require login for analyze, use per-user API keys, save history"
```

---

### Task 6: `/settings` — per-user API key management

**Files:**
- Create: `src/chesswhisper/web/templates/settings.html`
- Modify: `src/chesswhisper/web/app.py` (add `/settings` GET/POST)
- Modify: `src/chesswhisper/web/templates/base.html` (add the Settings nav link)
- Test: `tests/test_web_settings.py`

**Interfaces:**
- Consumes: `get_current_user`, `get_db`, `UserApiKey`, `encrypt_api_key`.
- Produces: `/settings` (GET, POST) on `app`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web_settings.py
import pytest
from fastapi.testclient import TestClient

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import UserApiKey
from chesswhisper.web.security import decrypt_api_key


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "settings_test.db")
    c = TestClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def test_settings_redirects_when_logged_out(tmp_path):
    init_db(tmp_path / "settings_logged_out.db")
    client = TestClient(app, follow_redirects=False)

    response = client.get("/settings")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_settings_shows_no_keys_configured_initially(client):
    response = client.get("/settings")

    assert response.status_code == 200
    assert "kayıtlı değil" in response.text.lower() or "not set" in response.text.lower()


def test_settings_saves_and_masks_anthropic_key(client):
    response = client.post("/settings", data={"anthropic_key": "sk-ant-secret", "openai_key": ""})

    assert response.status_code == 200
    assert "sk-ant-secret" not in response.text  # never echoed back in plaintext

    from chesswhisper.web.models import User
    db = next(get_db())
    user = db.query(User).filter_by(username="alice").one()
    row = db.get(UserApiKey, user.id)
    assert decrypt_api_key(row.anthropic_key_encrypted) == "sk-ant-secret"


def test_settings_leaves_key_unchanged_when_field_left_blank(client):
    client.post("/settings", data={"anthropic_key": "sk-ant-secret", "openai_key": ""})

    client.post("/settings", data={"anthropic_key": "", "openai_key": "sk-openai-secret"})

    from chesswhisper.web.models import User
    db = next(get_db())
    user = db.query(User).filter_by(username="alice").one()
    row = db.get(UserApiKey, user.id)
    assert decrypt_api_key(row.anthropic_key_encrypted) == "sk-ant-secret"
    assert decrypt_api_key(row.openai_key_encrypted) == "sk-openai-secret"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_settings.py -v`
Expected: FAIL — `/settings` doesn't exist (404).

- [ ] **Step 3: Create `settings.html`**

```html
{% extends "base.html" %}
{% block title %}chesswhisper — Ayarlar{% endblock %}
{% block content %}
<div class="card">
  {% if message %}<div class="banner warning">{{ message }}</div>{% endif %}
  <form method="post" action="/settings">
    <label for="anthropic_key">Anthropic (Claude) API anahtarı</label>
    <input type="password" id="anthropic_key" name="anthropic_key"
           placeholder="{% if has_anthropic_key %}•••••••• (kayıtlı, değiştirmek için yeni bir değer gir){% else %}kayıtlı değil{% endif %}">

    <label for="openai_key">OpenAI API anahtarı</label>
    <input type="password" id="openai_key" name="openai_key"
           placeholder="{% if has_openai_key %}•••••••• (kayıtlı, değiştirmek için yeni bir değer gir){% else %}kayıtlı değil{% endif %}">

    <p class="hint">Bir alanı boş bırakırsan mevcut kayıtlı anahtar (varsa) değişmeden kalır.</p>
    <button type="submit">Kaydet</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 4: Add the `/settings` routes to `app.py`**

Add these imports if not already present:

```python
from chesswhisper.web.security import encrypt_api_key
```

Add the routes:

```python
@app.get("/settings", response_class=HTMLResponse)
async def settings_form(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    key_row = db.get(UserApiKey, current_user.id)
    return templates.TemplateResponse(
        request, "settings.html",
        {
            "current_user": current_user,
            "has_anthropic_key": bool(key_row and key_row.anthropic_key_encrypted),
            "has_openai_key": bool(key_row and key_row.openai_key_encrypted),
            "message": "Devam etmeden önce bir API anahtarı gir." if request.query_params.get("message") == "missing_key" else None,
        },
    )


@app.post("/settings", response_class=HTMLResponse)
async def settings_submit(
    request: Request,
    anthropic_key: str = Form(default=""),
    openai_key: str = Form(default=""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    key_row = db.get(UserApiKey, current_user.id)
    if key_row is None:
        key_row = UserApiKey(user_id=current_user.id)
        db.add(key_row)

    if anthropic_key:
        key_row.anthropic_key_encrypted = encrypt_api_key(anthropic_key)
    if openai_key:
        key_row.openai_key_encrypted = encrypt_api_key(openai_key)

    db.commit()

    return templates.TemplateResponse(
        request, "settings.html",
        {
            "current_user": current_user,
            "has_anthropic_key": bool(key_row.anthropic_key_encrypted),
            "has_openai_key": bool(key_row.openai_key_encrypted),
            "message": "Kaydedildi.",
        },
    )
```

- [ ] **Step 5: Add the Settings link to `base.html`'s nav**

In `src/chesswhisper/web/templates/base.html`, change:

```html
    <nav class="nav-links">
      <a href="/">Analiz</a>
      <form method="post" action="/logout" style="display:inline; margin:0;">
```

to:

```html
    <nav class="nav-links">
      <a href="/">Analiz</a>
      <a href="/settings">Ayarlar</a>
      <form method="post" action="/logout" style="display:inline; margin:0;">
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_web_settings.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/chesswhisper/web/templates/settings.html src/chesswhisper/web/app.py src/chesswhisper/web/templates/base.html tests/test_web_settings.py
git commit -m "feat: add /settings for per-user encrypted API key management"
```

---

### Task 7: `/history` and `/history/{id}`

**Files:**
- Create: `src/chesswhisper/web/templates/history_list.html`
- Create: `src/chesswhisper/web/templates/history_detail.html`
- Modify: `src/chesswhisper/web/app.py` (add `/history`, `/history/{id}`)
- Modify: `src/chesswhisper/web/templates/base.html` (add the History nav link)
- Test: `tests/test_web_history.py`

**Interfaces:**
- Consumes: `get_current_user`, `get_db`, `AnalysisRecord`.
- Produces: `/history` (GET), `/history/{record_id}` (GET) on `app`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web_history.py
import pytest
from fastapi.testclient import TestClient

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord, User


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "history_test.db")
    c = TestClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def _seed_record(username: str, player_name: str) -> int:
    db = next(get_db())
    user = db.query(User).filter_by(username=username).one()
    record = AnalysisRecord(
        user_id=user.id, player_name=player_name, lang="en", llm_used="none",
        games_count=1, overall_acpl=10.0, overall_accuracy=95.0,
        report_markdown="# Test Report\n\nHello.",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record.id


def test_history_redirects_when_logged_out(tmp_path):
    init_db(tmp_path / "history_logged_out.db")
    client = TestClient(app, follow_redirects=False)

    response = client.get("/history")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_history_lists_only_current_users_records(client):
    client.post("/register", data={"username": "bob", "password": "correct-horse"})
    _seed_record("alice", "Alice")
    _seed_record("bob", "Bob")

    response = client.get("/history")

    assert response.status_code == 200
    assert "Alice" in response.text
    assert "Bob" not in response.text


def test_history_detail_renders_stored_report(client):
    record_id = _seed_record("alice", "Alice")

    response = client.get(f"/history/{record_id}")

    assert response.status_code == 200
    assert "Test Report" in response.text
    assert "Hello." in response.text


def test_history_detail_404s_for_another_users_record(client):
    client.post("/register", data={"username": "bob", "password": "correct-horse"})
    bob_record_id = _seed_record("bob", "Bob")

    response = client.get(f"/history/{bob_record_id}")

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_history.py -v`
Expected: FAIL — `/history` doesn't exist (404, but the specific 404-for-other-user test would pass "by accident" since the route is entirely missing — the other three fail meaningfully).

- [ ] **Step 3: Create the templates**

```html
{# src/chesswhisper/web/templates/history_list.html #}
{% extends "base.html" %}
{% block title %}chesswhisper — Geçmiş{% endblock %}
{% block content %}
<div class="card">
  {% if viewing_as_admin %}
  <p class="hint">{{ target_username }} kullanıcısının analiz geçmişini görüntülüyorsun.</p>
  {% endif %}
  {% if records %}
  <table>
    <thead><tr><th>Tarih</th><th>Oyuncu</th><th>Doğruluk</th><th>ACPL</th><th>Oyun</th><th></th></tr></thead>
    <tbody>
      {% for r in records %}
      <tr>
        <td>{{ r.created_at.strftime("%Y-%m-%d %H:%M") }}</td>
        <td>{{ r.player_name }}</td>
        <td>{{ "%.1f"|format(r.overall_accuracy) }}%</td>
        <td>{{ "%.1f"|format(r.overall_acpl) }}</td>
        <td>{{ r.games_count }}</td>
        <td><a href="{{ detail_url_prefix }}/{{ r.id }}">Görüntüle</a></td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p>Henüz kaydedilmiş bir analiz yok.</p>
  {% endif %}
</div>
{% endblock %}
```

```html
{# src/chesswhisper/web/templates/history_detail.html #}
{% extends "base.html" %}
{% block title %}chesswhisper — Rapor{% endblock %}
{% block content %}
<div class="card report">
  {{ report_html | safe }}
</div>
<a class="back-link" href="{{ back_url }}">&larr; Geçmişe dön</a>
{% endblock %}
```

- [ ] **Step 4: Add the routes to `app.py`**

```python
@app.get("/history", response_class=HTMLResponse)
async def history_list(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    records = (
        db.query(AnalysisRecord)
        .filter_by(user_id=current_user.id)
        .order_by(AnalysisRecord.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request, "history_list.html",
        {
            "current_user": current_user, "records": records,
            "detail_url_prefix": "/history", "viewing_as_admin": False,
        },
    )


@app.get("/history/{record_id}", response_class=HTMLResponse)
async def history_detail(
    request: Request,
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    record = db.get(AnalysisRecord, record_id)
    if record is None or record.user_id != current_user.id:
        return _render_error(request, "Kayıt bulunamadı.", 404, current_user)

    report_html = _report_to_safe_html(record.report_markdown)
    return templates.TemplateResponse(
        request, "history_detail.html",
        {"current_user": current_user, "report_html": report_html, "back_url": "/history"},
    )
```

Add `from chesswhisper.web.models import AnalysisRecord` to the existing model imports if `AnalysisRecord` isn't already imported from Task 5.

- [ ] **Step 5: Add the History link to `base.html`'s nav**

```html
    <nav class="nav-links">
      <a href="/">Analiz</a>
      <a href="/history">Geçmiş</a>
      <a href="/settings">Ayarlar</a>
      <form method="post" action="/logout" style="display:inline; margin:0;">
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_web_history.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/chesswhisper/web/templates/history_list.html src/chesswhisper/web/templates/history_detail.html src/chesswhisper/web/app.py src/chesswhisper/web/templates/base.html tests/test_web_history.py
git commit -m "feat: add /history and /history/{id} for viewing past analyses"
```

---

### Task 8: Admin panel

**Files:**
- Create: `src/chesswhisper/web/templates/admin_users.html`
- Modify: `src/chesswhisper/web/app.py` (add `/admin`, `/admin/users/{id}`, `/admin/users/{id}/history/{record_id}`, `/admin/users/{id}/delete`, `/admin/users/{id}/reset-password`)
- Modify: `src/chesswhisper/web/templates/base.html` (add the Admin nav link, shown only for admins)
- Test: `tests/test_web_admin.py`

**Interfaces:**
- Consumes: `require_admin` (Task 3), `get_db`, `User`, `AnalysisRecord`, `hash_password`.
- Produces: the five admin routes on `app`, all gated by `Depends(require_admin)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web_admin.py
import pytest
from fastapi.testclient import TestClient

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord, User
from chesswhisper.web.security import verify_password


@pytest.fixture
def clients(tmp_path):
    init_db(tmp_path / "admin_test.db")
    admin_client = TestClient(app, follow_redirects=False)
    admin_client.post("/register", data={"username": "root", "password": "correct-horse"})

    db = next(get_db())
    root = db.query(User).filter_by(username="root").one()
    root.is_admin = True
    db.commit()

    plain_client = TestClient(app, follow_redirects=False)
    plain_client.post("/register", data={"username": "alice", "password": "correct-horse"})

    return admin_client, plain_client


def test_admin_route_forbidden_for_non_admin(clients):
    _, plain_client = clients

    response = plain_client.get("/admin")

    assert response.status_code == 403


def test_admin_route_lists_all_users(clients):
    admin_client, _ = clients

    response = admin_client.get("/admin")

    assert response.status_code == 200
    assert "root" in response.text
    assert "alice" in response.text


def test_admin_can_view_another_users_history(clients):
    admin_client, _ = clients
    db = next(get_db())
    alice = db.query(User).filter_by(username="alice").one()
    db.add(AnalysisRecord(
        user_id=alice.id, player_name="Alice", lang="en", llm_used="none",
        games_count=1, overall_acpl=10.0, overall_accuracy=95.0,
        report_markdown="# Alice's report",
    ))
    db.commit()

    response = admin_client.get(f"/admin/users/{alice.id}")

    assert response.status_code == 200
    assert "Alice" in response.text


def test_admin_cannot_delete_self(clients):
    admin_client, _ = clients
    db = next(get_db())
    root = db.query(User).filter_by(username="root").one()

    response = admin_client.post(f"/admin/users/{root.id}/delete")

    assert response.status_code == 400


def test_admin_can_delete_another_user_cascading_records(clients):
    admin_client, _ = clients
    db = next(get_db())
    alice = db.query(User).filter_by(username="alice").one()
    alice_id = alice.id
    db.add(AnalysisRecord(
        user_id=alice_id, player_name="Alice", lang="en", llm_used="none",
        games_count=1, overall_acpl=10.0, overall_accuracy=95.0, report_markdown="x",
    ))
    db.commit()

    response = admin_client.post(f"/admin/users/{alice_id}/delete")

    assert response.status_code == 303
    db2 = next(get_db())
    assert db2.get(User, alice_id) is None
    assert db2.query(AnalysisRecord).filter_by(user_id=alice_id).count() == 0


def test_admin_can_reset_another_users_password(clients):
    admin_client, _ = clients
    db = next(get_db())
    alice = db.query(User).filter_by(username="alice").one()

    response = admin_client.post(f"/admin/users/{alice.id}/reset-password", data={"new_password": "brand-new-pass"})

    assert response.status_code == 303
    db2 = next(get_db())
    alice2 = db2.get(User, alice.id)
    assert verify_password("brand-new-pass", alice2.password_hash)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_admin.py -v`
Expected: FAIL — none of the `/admin*` routes exist yet (404s).

- [ ] **Step 3: Create `admin_users.html`**

```html
{% extends "base.html" %}
{% block title %}chesswhisper — Admin{% endblock %}
{% block content %}
<div class="card">
  <table>
    <thead><tr><th>Kullanıcı</th><th>Admin mi</th><th>Analiz sayısı</th><th>Kayıt tarihi</th><th></th></tr></thead>
    <tbody>
      {% for u in users %}
      <tr>
        <td>{{ u.username }}</td>
        <td>{{ "Evet" if u.is_admin else "Hayır" }}</td>
        <td>{{ u.analyses|length }}</td>
        <td>{{ u.created_at.strftime("%Y-%m-%d") }}</td>
        <td>
          <a href="/admin/users/{{ u.id }}">Geçmişi gör</a>
          {% if u.id != current_user.id %}
          <form method="post" action="/admin/users/{{ u.id }}/delete" style="display:inline; margin-left:8px;">
            <button type="submit" class="link-button">Sil</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 4: Add the admin routes to `app.py`**

```python
from chesswhisper.web.auth import require_admin

# ...

@app.get("/admin", response_class=HTMLResponse)
async def admin_users_list(
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    users = db.query(User).order_by(User.created_at).all()
    return templates.TemplateResponse(
        request, "admin_users.html", {"current_user": current_user, "users": users}
    )


@app.get("/admin/users/{user_id}", response_class=HTMLResponse)
async def admin_user_history(
    request: Request,
    user_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    target = db.get(User, user_id)
    if target is None:
        return _render_error(request, "Kullanıcı bulunamadı.", 404, current_user)

    records = (
        db.query(AnalysisRecord)
        .filter_by(user_id=user_id)
        .order_by(AnalysisRecord.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request, "history_list.html",
        {
            "current_user": current_user, "records": records,
            "detail_url_prefix": f"/admin/users/{user_id}/history",
            "viewing_as_admin": True, "target_username": target.username,
        },
    )


@app.get("/admin/users/{user_id}/history/{record_id}", response_class=HTMLResponse)
async def admin_user_history_detail(
    request: Request,
    user_id: int,
    record_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    record = db.get(AnalysisRecord, record_id)
    if record is None or record.user_id != user_id:
        return _render_error(request, "Kayıt bulunamadı.", 404, current_user)

    report_html = _report_to_safe_html(record.report_markdown)
    return templates.TemplateResponse(
        request, "history_detail.html",
        {"current_user": current_user, "report_html": report_html, "back_url": f"/admin/users/{user_id}"},
    )


@app.post("/admin/users/{user_id}/delete")
async def admin_delete_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if user_id == current_user.id:
        return _render_error(request, "Kendi hesabını silemezsin.", 400, current_user)

    target = db.get(User, user_id)
    if target is None:
        return _render_error(request, "Kullanıcı bulunamadı.", 404, current_user)

    db.delete(target)
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: int,
    request: Request,
    new_password: str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        return _render_error(request, "Kullanıcı bulunamadı.", 404, current_user)

    target.password_hash = hash_password(new_password)
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)
```

The `User.analyses` cascade relationship from Task 1's `models.py` (`cascade="all, delete-orphan"`) means `db.delete(target)` also deletes that user's `AnalysisRecord` and `UserApiKey` rows automatically — no manual cleanup needed in the delete route.

- [ ] **Step 5: Add the Admin link to `base.html`'s nav (admin-only)**

```html
    <nav class="nav-links">
      <a href="/">Analiz</a>
      <a href="/history">Geçmiş</a>
      <a href="/settings">Ayarlar</a>
      {% if current_user.is_admin %}<a href="/admin">Admin</a>{% endif %}
      <form method="post" action="/logout" style="display:inline; margin:0;">
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_web_admin.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/chesswhisper/web/templates/admin_users.html src/chesswhisper/web/app.py src/chesswhisper/web/templates/base.html tests/test_web_admin.py
git commit -m "feat: add admin panel (list users, view history, delete, reset password)"
```

---

### Task 9: `create-admin` CLI command, README update

**Files:**
- Modify: `src/chesswhisper/cli.py` (add the `create-admin` command)
- Modify: `README.md` (document accounts, settings, history, admin, `create-admin`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `init_db` (Task 1), `User` (Task 1), `hash_password` (Task 2).
- Produces: `chesswhisper create-admin --username X --password Y` on the CLI.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_create_admin_creates_admin_user(tmp_path, monkeypatch):
    from chesswhisper.web import db as web_db
    db_path = tmp_path / "create_admin_test.db"
    monkeypatch.setattr(web_db, "_default_db_path", lambda: db_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["create-admin", "--username", "root", "--password", "correct-horse-battery"])

    assert result.exit_code == 0, result.output

    web_db.init_db(db_path)
    from chesswhisper.web.models import User
    db_gen = web_db.get_db()
    db_session = next(db_gen)
    user = db_session.query(User).filter_by(username="root").one()
    assert user.is_admin is True
    from chesswhisper.web.security import verify_password
    assert verify_password("correct-horse-battery", user.password_hash)


def test_create_admin_rejects_duplicate_username(tmp_path, monkeypatch):
    from chesswhisper.web import db as web_db
    db_path = tmp_path / "create_admin_dup_test.db"
    monkeypatch.setattr(web_db, "_default_db_path", lambda: db_path)

    runner = CliRunner()
    runner.invoke(cli, ["create-admin", "--username", "root", "--password", "correct-horse-battery"])
    result = runner.invoke(cli, ["create-admin", "--username", "root", "--password", "another-password"])

    assert result.exit_code != 0
    assert "already" in result.output.lower() or "zaten" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -k create_admin -v`
Expected: FAIL — `create-admin` isn't a recognized command (Click reports "No such command").

- [ ] **Step 3: Implement the `create-admin` command**

Add to `src/chesswhisper/cli.py`, after the `serve` command:

```python
@cli.command("create-admin")
@click.option("--username", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
def create_admin(username, password):
    try:
        from chesswhisper.web.db import get_db, init_db
        from chesswhisper.web.models import User
        from chesswhisper.web.security import hash_password
    except ImportError:
        click.echo(
            'Error: the web UI requires extra dependencies. Install them with: pip install -e ".[web]"',
            err=True,
        )
        sys.exit(1)

    init_db()
    db = next(get_db())
    if db.query(User).filter_by(username=username).first() is not None:
        click.echo(f"Error: a user named {username!r} already exists.", err=True)
        sys.exit(1)

    db.add(User(username=username, password_hash=hash_password(password), is_admin=True))
    db.commit()
    click.echo(f"Admin user {username!r} created.")
```

Note: `--password` uses `prompt=True, hide_input=True, confirmation_prompt=True`, which means when it's passed directly as a CLI flag (as in the tests above), Click still accepts the flag value without prompting — the prompt only triggers when the option is omitted. This matches the test usage exactly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -k create_admin -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Update README**

Replace the "Web UI" section of `README.md` with:

```markdown
## Web UI

For a browser-based alternative to the CLI, with accounts, per-user API
keys, and analysis history, install the `web` extra:

```
pip install -e ".[web]"
```

Create the first admin account:

```
chesswhisper create-admin
```

Then start the server:

```
chesswhisper serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`: anyone who can reach this address can
register their own account. Each user sets their own Anthropic/OpenAI API
key under **Settings** (encrypted at rest, never in an environment
variable shared across users) and every analysis they run is saved to
their **History**. The admin account additionally sees an **Admin** page
listing every registered user, can view any user's history, delete a
user, or reset a user's password.

This is still a local-only tool: the server binds to `127.0.0.1` by
default and isn't hardened for or intended to be exposed on the public
internet (no HTTPS, no rate limiting, no email verification) — the
threat model is "a few people who already have access to this machine,"
not the open internet.
```

- [ ] **Step 6: Run the entire test suite**

Run: `pytest -v`
Expected: every test in the project passes.

- [ ] **Step 7: Commit**

```bash
git add src/chesswhisper/cli.py tests/test_cli.py README.md
git commit -m "feat: add create-admin CLI command; document multi-user web UI"
```
