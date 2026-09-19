import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

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
    def login_as(user_id: int, request: Request):
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
