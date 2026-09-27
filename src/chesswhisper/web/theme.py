"""The colour theme of the web UI: follow the system, or always light or dark.

Like the interface language it is a per-session choice, switched from the
header; the header link steps through the themes in order.
"""

from __future__ import annotations

from starlette.requests import Request

THEMES = ("system", "light", "dark")
DEFAULT_THEME = "system"
_SESSION_KEY = "ui_theme"


def ui_theme(request: Request) -> str:
    theme = request.session.get(_SESSION_KEY)
    return theme if theme in THEMES else DEFAULT_THEME


def set_ui_theme(request: Request, theme: str) -> None:
    if theme in THEMES:
        request.session[_SESSION_KEY] = theme


def next_theme(theme: str) -> str:
    return THEMES[(THEMES.index(theme) + 1) % len(THEMES)]
