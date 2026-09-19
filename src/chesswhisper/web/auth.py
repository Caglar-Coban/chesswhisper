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
