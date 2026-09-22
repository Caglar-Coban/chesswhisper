import os
import tempfile
from datetime import timedelta

import bleach
import markdown as markdown_lib
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from chesswhisper.cli import run_analysis
from chesswhisper.engine_provision import StockfishProvisionError
from chesswhisper.game_sources import (
    DEFAULT_MAX_GAMES,
    MAX_GAMES_LIMIT,
    GameFetchError,
    GameSourceUnavailable,
    fetch_pgn,
)
from chesswhisper.report_writer import render_markdown
from chesswhisper.web.auth import (
    Forbidden,
    RedirectToLogin,
    get_csrf_token,
    get_current_user,
    get_optional_current_user,
    require_admin,
    verify_csrf,
)
from chesswhisper.web.db import get_db, utcnow
from chesswhisper.web.models import AnalysisRecord, User, UserApiKey
from chesswhisper.web.security import (
    decrypt_api_key,
    encrypt_api_key,
    get_session_secret,
    hash_password,
    verify_password,
)

MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15
FETCH_SOURCES = ("lichess", "chesscom")


def _naive_utcnow():
    # SQLite round-trips DateTime columns as naive, so lockout comparisons
    # must use a naive "now" too, or aware/naive comparison raises TypeError.
    return utcnow().replace(tzinfo=None)


_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

_ALLOWED_TAGS = [
    "p", "br", "hr", "strong", "em", "code", "pre",
    "h1", "h2", "h3", "h4",
    "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td",
    "a",
]
_ALLOWED_ATTRS = {"a": ["href"]}

app = FastAPI(title="chesswhisper")
app.add_middleware(SessionMiddleware, secret_key=get_session_secret().hex())


@app.exception_handler(RedirectToLogin)
async def _redirect_to_login(request: Request, exc: RedirectToLogin) -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden_handler(request: Request, exc: Forbidden) -> HTMLResponse:
    return _render_error(request, "Bu sayfaya erişim yetkiniz yok.", 403)


def _render(request: Request, template_name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request, template_name, {**context, "csrf_token": get_csrf_token(request)}, status_code=status_code
    )


def _render_error(
    request: Request, message: str, status_code: int, current_user: User | None = None
) -> HTMLResponse:
    return _render(request, "error.html", {"message": message, "current_user": current_user}, status_code)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> HTMLResponse:
    return _render_error(request, "Beklenmeyen bir hata oluştu.", 500, None)


def _report_to_safe_html(report_markdown: str) -> str:
    raw_html = markdown_lib.markdown(report_markdown, extensions=["tables"])
    return bleach.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)


@app.get("/register", response_class=HTMLResponse, response_model=None)
async def register_form(
    request: Request, current_user: User | None = Depends(get_optional_current_user)
) -> HTMLResponse | RedirectResponse:
    if current_user is not None:
        return RedirectResponse("/", status_code=303)
    return _render(request, "register.html", {"current_user": None})


@app.post("/register", response_class=HTMLResponse, response_model=None)
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse | RedirectResponse:
    if len(password) < 8:
        return _render(
            request, "register.html",
            {"current_user": None, "error": "Parola en az 8 karakter olmalı."},
        )
    if db.query(User).filter_by(username=username).first() is not None:
        return _render(
            request, "register.html",
            {"current_user": None, "error": "Bu kullanıcı adı zaten kayıtlı."},
        )

    user = User(username=username, password_hash=hash_password(password), is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)

    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse, response_model=None)
async def login_form(
    request: Request, current_user: User | None = Depends(get_optional_current_user)
) -> HTMLResponse | RedirectResponse:
    if current_user is not None:
        return RedirectResponse("/", status_code=303)
    return _render(request, "login.html", {"current_user": None})


@app.post("/login", response_class=HTMLResponse, response_model=None)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse | RedirectResponse:
    user = db.query(User).filter_by(username=username).first()

    if user is not None and user.locked_until is not None and user.locked_until > _naive_utcnow():
        remaining_minutes = max(1, int((user.locked_until - _naive_utcnow()).total_seconds() // 60) + 1)
        return _render(
            request, "login.html",
            {
                "current_user": None,
                "error": f"Hesap kilitlendi. {remaining_minutes} dakika sonra tekrar dene.",
            },
        )

    if user is None or not verify_password(password, user.password_hash):
        if user is not None:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
                user.locked_until = _naive_utcnow() + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
            db.commit()
        return _render(
            request, "login.html",
            {"current_user": None, "error": "Kullanıcı adı veya parola yanlış."},
        )

    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()

    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request, _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_form(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    key_row = db.get(UserApiKey, current_user.id)
    return _render(
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
    _csrf: None = Depends(verify_csrf),
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

    return _render(
        request, "settings.html",
        {
            "current_user": current_user,
            "has_anthropic_key": bool(key_row.anthropic_key_encrypted),
            "has_openai_key": bool(key_row.openai_key_encrypted),
            "message": "Kaydedildi.",
        },
    )


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
    return _render(
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
    return _render(
        request, "history_detail.html",
        {"current_user": current_user, "report_html": report_html, "back_url": "/history"},
    )


@app.post("/history/{record_id}/delete", response_model=None)
async def history_delete(
    record_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse | RedirectResponse:
    record = db.get(AnalysisRecord, record_id)
    if record is None or record.user_id != current_user.id:
        return _render_error(request, "Kayıt bulunamadı.", 404, current_user)

    db.delete(record)
    db.commit()
    return RedirectResponse("/history", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, current_user: User = Depends(get_current_user)) -> HTMLResponse:
    return _render(
        request, "index.html",
        {"current_user": current_user, "default_max_games": DEFAULT_MAX_GAMES, "max_games_limit": MAX_GAMES_LIMIT},
    )


@app.post("/analyze", response_class=HTMLResponse, response_model=None)
async def analyze(
    request: Request,
    pgn: UploadFile | None = File(default=None),  # noqa: B008 - FastAPI's own idiom
    source: str = Form(default="file"),
    username: str = Form(default=""),
    max_games: str = Form(default=str(DEFAULT_MAX_GAMES)),
    player: str = Form(default=""),
    lang: str = Form(default="tr"),
    llm: str = Form(default="none"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse | RedirectResponse:
    fetching = source in FETCH_SOURCES
    if source != "file" and not fetching:
        return _render_error(request, "Geçersiz oyun kaynağı.", 400, current_user)

    contents = b""
    game_count = DEFAULT_MAX_GAMES
    if fetching:
        username = username.strip()
        if not username:
            return _render_error(request, "Lütfen bir kullanıcı adı girin.", 400, current_user)
        try:
            game_count = int(max_games)
        except ValueError:
            return _render_error(request, "Oyun sayısı geçerli bir tam sayı olmalı.", 400, current_user)
    else:
        if pgn is None or not pgn.filename:
            return _render_error(request, "Lütfen analiz edilecek bir PGN dosyası seçin.", 400, current_user)
        contents = await pgn.read()

    llm_api_key = None
    if llm != "none":
        key_row = db.get(UserApiKey, current_user.id)
        encrypted = None
        if key_row is not None:
            encrypted = key_row.anthropic_key_encrypted if llm == "claude" else key_row.openai_key_encrypted
        if not encrypted:
            return RedirectResponse("/settings?message=missing_key", status_code=303)
        llm_api_key = decrypt_api_key(encrypted)

    fetch_warnings: list[str] = []
    if fetching:
        try:
            fetched = await run_in_threadpool(fetch_pgn, source, username, game_count)
        except GameSourceUnavailable as exc:
            return _render_error(request, f"Oyunlar alınamadı: {exc}", 502, current_user)
        except GameFetchError as exc:
            return _render_error(request, str(exc), 400, current_user)
        contents = fetched.pgn_text.encode("utf-8")
        fetch_warnings = fetched.warnings
        player = player or fetched.player

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

    return _render(
        request, "result.html",
        {
            "report_html": report_html,
            "warnings": fetch_warnings + warnings,
            "current_user": current_user,
            "saved": True,
        },
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_users_list(
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    users = db.query(User).order_by(User.created_at).all()
    return _render(
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
    return _render(
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
    return _render(
        request, "history_detail.html",
        {"current_user": current_user, "report_html": report_html, "back_url": f"/admin/users/{user_id}"},
    )


@app.post("/admin/users/{user_id}/delete", response_model=None)
async def admin_delete_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse | RedirectResponse:
    if user_id == current_user.id:
        return _render_error(request, "Kendi hesabını silemezsin.", 400, current_user)

    target = db.get(User, user_id)
    if target is None:
        return _render_error(request, "Kullanıcı bulunamadı.", 404, current_user)

    db.delete(target)
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{user_id}/reset-password", response_model=None)
async def admin_reset_password(
    user_id: int,
    request: Request,
    new_password: str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse | RedirectResponse:
    target = db.get(User, user_id)
    if target is None:
        return _render_error(request, "Kullanıcı bulunamadı.", 404, current_user)

    if len(new_password) < 8:
        return _render_error(request, "Parola en az 8 karakter olmalı.", 400, current_user)

    target.password_hash = hash_password(new_password)
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)
