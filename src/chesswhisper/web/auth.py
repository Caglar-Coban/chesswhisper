from __future__ import annotations

import secrets

from fastapi import Depends, Form
from sqlalchemy.orm import Session
from starlette.requests import Request

from chesswhisper.web.db import get_db
from chesswhisper.web.models import User
from chesswhisper.web.security import generate_csrf_token

_CSRF_SESSION_KEY = "csrf_token"


class RedirectToLogin(Exception):
    pass


class Forbidden(Exception):
    pass


def get_csrf_token(request: Request) -> str:
    token = request.session.get(_CSRF_SESSION_KEY)
    if not token:
        token = generate_csrf_token()
        request.session[_CSRF_SESSION_KEY] = token
    return token


async def verify_csrf(request: Request, csrf_token: str = Form(...)) -> None:
    expected = request.session.get(_CSRF_SESSION_KEY)
    if not expected or not secrets.compare_digest(csrf_token, expected):
        raise Forbidden()


def start_session(request: Request, user: User) -> None:
    request.session["user_id"] = user.id
    request.session["session_version"] = user.session_version


def get_optional_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    user = db.get(User, user_id)
    # Sessions from before a password change (or before session versions
    # existed, which count as version 0) no longer identify the user.
    if user is None or request.session.get("session_version", 0) != user.session_version:
        request.session.pop("user_id", None)
        request.session.pop("session_version", None)
        return None
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_optional_current_user(request, db)
    if user is None:
        raise RedirectToLogin()
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise Forbidden()
    return user
