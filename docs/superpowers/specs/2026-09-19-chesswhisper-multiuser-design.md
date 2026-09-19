# chesswhisper Multi-User Web App — Design Spec

Date: 2026-09-19
Status: Approved (pending implementation plan)

## 1. Overview

Turn the web UI from a no-login, single-shot tool into a local multi-user
application: user accounts, per-user LLM API keys, persistent history of
past analyses, and an admin role that can see every registered user and
their reports. This supersedes the "no authentication, no multi-user
support, no persistent storage" non-goals in the original web UI spec
(`2026-09-19-chesswhisper-web-ui-design.md`) — those applied to v1 of the
web UI; this spec is the next iteration of the web UI specifically.

**The CLI is unaffected.** `chesswhisper analyze` stays exactly as it is
today: single-user, reads API keys from the environment/`.env`, no
database, no accounts. Everything in this spec is scoped to the web app
(`chesswhisper serve`) and one new CLI command (`create-admin`) needed to
bootstrap it.

**Deployment model:** runs locally on one machine at a time (binds to
`127.0.0.1` by default, same as today), distributed by cloning/installing
the GitHub repo — not deployed to a public server. Multiple people on the
same machine/household share one running instance and each get their own
account. This shapes every security trade-off below: no TLS, no email
verification, no rate limiting/brute-force lockout, no CSRF tokens — the
threat model is "a small group of people who already have access to this
machine," not "the public internet."

## 2. Goals

- Username/password accounts with hashed passwords (bcrypt).
- Open self-registration (anyone who can reach the app can create an
  account).
- A `chesswhisper create-admin` CLI command to bootstrap the first admin
  (prompts for username/password).
- Per-user storage of Anthropic/OpenAI API keys, encrypted at rest, set
  via a `/settings` page — `/analyze` uses the logged-in user's own key,
  never a shared environment variable.
- Every analysis a user runs is saved (summary stats + full rendered
  report) and listable/re-viewable later from `/history`.
- An `/admin` area, visible only to admin users: list of every registered
  user with their basic stats, drill into any user's full history, delete
  a user, reset a user's password.
- All of this backed by a single SQLite file in the same app-data
  directory the Stockfish binary already lives in (via `platformdirs`) —
  no separate database server to install.

## 3. Non-goals (v1)

- Not deployed to / hardened for the public internet (see Overview).
- No email verification, no password-reset-via-email (admin resets
  passwords directly instead).
- No rate limiting or account lockout on failed logins.
- No CSRF token protection — accepted risk given the closed, trusted,
  non-internet-facing threat model above.
- No editing/deleting individual analysis records (by the owning user);
  only admin can delete a whole user (cascading their records).
- No database migrations tooling (Alembic, etc.) — tables are created
  with `SQLAlchemy`'s `Base.metadata.create_all()` on startup. If the
  schema changes later, migrations become a real requirement; not needed
  for a from-scratch v1.
- The old no-login web flow is removed entirely, not kept alongside this.

## 4. Architecture

```
Browser
  |  GET/POST /register, /login, /logout          (public)
  |  GET /, POST /analyze, GET /history[/{id}],     (requires login)
  |  GET/POST /settings
  |  GET /admin, GET /admin/users/{id},             (requires is_admin)
  |  POST /admin/users/{id}/delete
  |  POST /admin/users/{id}/reset-password
  v
FastAPI app (chesswhisper.web.app)
  |
  |-- SessionMiddleware (signed cookie, secret loaded from app-data dir)
  |-- auth dependencies: get_current_user, require_admin
  |
  v
chesswhisper.web.db (SQLAlchemy engine/session bound to
  <app-data-dir>/chesswhisper.db)
  |
  v
Models: User, UserApiKey, AnalysisRecord

Analysis itself still goes through chesswhisper.cli.run_analysis exactly
as before — only the API key now comes from the logged-in user's row
instead of the environment.
```

## 5. Data Model

```python
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str]
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime]

class UserApiKey(Base):
    __tablename__ = "user_api_keys"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    anthropic_key_encrypted: Mapped[str | None]
    openai_key_encrypted: Mapped[str | None]
    updated_at: Mapped[datetime]

class AnalysisRecord(Base):
    __tablename__ = "analysis_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime]
    player_name: Mapped[str]
    lang: Mapped[str]
    llm_used: Mapped[str]
    games_count: Mapped[int]
    overall_acpl: Mapped[float]
    overall_accuracy: Mapped[float]
    report_markdown: Mapped[str]
```

`games_count`/`overall_acpl`/`overall_accuracy` are denormalized onto
`AnalysisRecord` purely so `/history`'s list view doesn't need to
re-parse `report_markdown` for every row.

## 6. Components

### 6.1 `chesswhisper.web.db`

- `get_engine()` — SQLAlchemy engine for `sqlite:///<app-data-dir>/chesswhisper.db`
  (app-data-dir resolved the same way `engine_provision.py` already
  resolves it, via `platformdirs.user_data_dir("chesswhisper")`).
- `init_db()` — `Base.metadata.create_all(engine)`; called once at FastAPI
  startup and from the `create-admin` CLI command.
- `get_db()` — a FastAPI dependency yielding a `Session`, closed after the
  request.

### 6.2 `chesswhisper.web.security`

- `hash_password(password: str) -> str` / `verify_password(password, hash) -> bool`
  — thin wrappers around `bcrypt.hashpw`/`bcrypt.checkpw`.
- `get_or_create_secret_key(name: str) -> bytes` — reads
  `<app-data-dir>/<name>` if it exists, otherwise generates
  `Fernet.generate_key()` (for `api_key_encryption.key`) or
  `secrets.token_bytes(32)` (for `session_secret.key`), writes it, and
  returns it. Two separate secrets: one for cookie signing, one for
  Fernet-encrypting stored API keys — a leaked session secret should not
  also expose stored API keys.
- `encrypt_api_key(plaintext: str) -> str` / `decrypt_api_key(ciphertext: str) -> str`
  — `Fernet(encryption_key).encrypt/decrypt`, base64 text in, text out for
  storage in the `String` columns above.

### 6.3 Auth dependencies (in `chesswhisper.web.app`)

```python
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Raises a redirect-to-/login exception if no valid session user."""

def require_admin(user: User = Depends(get_current_user)) -> User:
    """Raises HTTP 403 (rendered as error.html) if user.is_admin is False."""
```

A registered exception handler turns the "not logged in" case into a
`RedirectResponse("/login")` rather than a raw 401, since every route here
serves an HTML page, not an API.

### 6.4 `run_analysis` / `_build_llm_provider` extension

`_build_llm_provider(name: str, api_key: str | None = None)` gains an
optional explicit key, passed straight to `ClaudeProvider(api_key=...)` /
`OpenAIProvider(api_key=...)` (both already accept this — no change needed
there). `run_analysis(...)` gains a matching optional `llm_api_key: str | None = None`
parameter, threaded through to `_build_llm_provider`. The CLI's call site
never passes it (stays `None`, falls back to the environment exactly as
today — zero behavior change for the CLI). The web route resolves the
current user's decrypted key for the selected provider and passes it
explicitly; if the user hasn't configured a key for the provider they
picked, the route redirects to `/settings` with a message instead of ever
calling `run_analysis`.

### 6.5 Routes

| Route | Method | Auth | Behavior |
|---|---|---|---|
| `/register` | GET, POST | public | Registration form; POST validates username uniqueness + minimum password length (8 chars), hashes password, creates `User`, logs them in, redirects to `/`. |
| `/login` | GET, POST | public | Login form; POST verifies password, sets `request.session["user_id"]`, redirects to `/`. |
| `/logout` | POST | logged in | Clears the session, redirects to `/login`. |
| `/` | GET | logged in | The analyze form (same as today) plus nav links (History, Settings, Admin if applicable, Logout). |
| `/analyze` | POST | logged in | Resolves the user's API key if needed (see 6.4), calls `run_analysis`, saves an `AnalysisRecord`, renders the report (same template as today, plus "saved to your history" note). |
| `/history` | GET | logged in | Lists the current user's `AnalysisRecord` rows, newest first. |
| `/history/{id}` | GET | logged in | Renders one record's stored `report_markdown`; 404 if the record doesn't belong to the current user. |
| `/settings` | GET, POST | logged in | View (masked) / update the user's own Anthropic/OpenAI keys. |
| `/admin` | GET | admin | Table of every user: username, admin flag, analysis count, joined date. |
| `/admin/users/{id}` | GET | admin | That user's full history (reuses the `/history` list template). |
| `/admin/users/{id}/delete` | POST | admin | Deletes the user and their `UserApiKey`/`AnalysisRecord` rows (cascade). Refuses to let an admin delete their own account (prevents an accidental admin-lockout). |
| `/admin/users/{id}/reset-password` | POST | admin | Admin submits a new password directly; it's hashed and replaces the user's `password_hash`. |

### 6.6 CLI: `create-admin`

```
chesswhisper create-admin
```
Prompts for username and password (`click.option(..., prompt=True)`,
password hidden + confirmed), calls `init_db()`, checks the username isn't
taken, hashes the password, inserts a `User` row with `is_admin=True`.

## 7. Error Handling

| Situation | Behavior |
|---|---|
| Wrong username/password on login | Re-render `/login` with an inline error, no redirect. |
| Username already taken on register | Re-render `/register` with an inline error. |
| Password shorter than 8 characters | Re-render `/register` with an inline error. |
| Unauthenticated request to a protected route | Redirect to `/login`. |
| Non-admin request to an admin route | HTTP 403, rendered via `error.html`. |
| `/history/{id}` for another user's record | HTTP 404 (not 403 — don't reveal the record exists). |
| `/analyze` with an LLM provider the user hasn't configured a key for | Redirect to `/settings` with a message, before calling `run_analysis`. |
| Admin tries to delete their own account | HTTP 400, rendered via `error.html`. |
| Everything already covered by the existing web UI spec (bad PGN, unknown player, Stockfish provisioning failure, unhandled exceptions) | Unchanged — still routes through `run_analysis`'s existing exception contract. |

## 8. Security Considerations

- Passwords: bcrypt, never stored or logged in plaintext.
- API keys: encrypted at rest with Fernet; the encryption key file lives
  in the OS-specific per-user app-data directory (already
  user-isolated by the OS on Windows/macOS/Linux); not further
  ACL-hardened in v1 (see Non-goals).
- Session cookie: signed (not encrypted) via `SessionMiddleware` — holds
  only a user ID, never a password or API key.
- SQL access goes through SQLAlchemy's parameterized queries throughout
  (no raw string-built SQL anywhere), so this doesn't reopen the
  SQL-injection surface.
- The existing `bleach`-sanitized Markdown-to-HTML rendering from the v1
  web UI spec is reused unchanged for both `/analyze`'s immediate result
  and `/history/{id}`'s stored-report view.

## 9. Testing Strategy

- Auth: register creates a user and logs them in; duplicate username
  rejected; wrong password rejected; protected routes redirect
  when logged out; admin routes 403 for a non-admin logged-in user.
- API key settings: saving a key round-trips through encrypt/decrypt
  correctly; `/analyze` uses the current user's key (verified via a
  mocked `ClaudeProvider`/`OpenAIProvider` construction, asserting the
  `api_key` argument it received); missing key for the chosen provider
  redirects to `/settings` without calling `run_analysis`.
- History: an analysis run by user A is not visible to user B via
  `/history/{id}` (404); admin can view it via `/admin/users/{id}`.
- Admin: non-admin gets 403 on all `/admin*` routes; delete removes the
  user and cascades their records; an admin cannot delete themselves.
- All of this via `fastapi.testclient.TestClient` against a temporary
  SQLite file (`tmp_path`), with the Stockfish engine mocked exactly as
  in the existing CLI/web tests — no real network or engine calls.

## 10. Dependencies

New dependencies, added to the existing `web` extra (this is entirely a
web-app concern, the CLI extra/base install is untouched):

```toml
web = [
    "fastapi>=0.115", "uvicorn[standard]>=0.30", "python-multipart>=0.0.9",
    "jinja2>=3.1", "markdown>=3.6", "bleach>=6.1",
    "sqlalchemy>=2.0", "bcrypt>=4.0", "cryptography>=42", "itsdangerous>=2.1",
]
```

## 11. Open Items for Implementation Planning

- Exact Jinja2 template set for the new pages (register/login/history
  list/history detail/settings/admin list/admin user detail) — page
  count and layout reuse decisions are implementation detail, not a
  design question; they should all extend the existing `base.html`.
- Whether `/admin/users/{id}` reuses `/history`'s template with an
  "acting as admin, viewing X's history" banner, or gets its own —
  reuse is the obvious choice but the plan should confirm the template
  is parameterized cleanly rather than duplicated.
