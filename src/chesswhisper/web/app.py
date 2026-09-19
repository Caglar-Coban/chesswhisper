import os
import tempfile

import bleach
import markdown as markdown_lib
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from chesswhisper.cli import run_analysis
from chesswhisper.engine_provision import StockfishProvisionError
from chesswhisper.report_writer import render_markdown
from chesswhisper.web.auth import Forbidden, RedirectToLogin, get_current_user, get_optional_current_user
from chesswhisper.web.db import get_db
from chesswhisper.web.models import AnalysisRecord, User, UserApiKey
from chesswhisper.web.security import decrypt_api_key, encrypt_api_key, get_session_secret, hash_password, verify_password

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
app.add_middleware(SessionMiddleware, secret_key=get_session_secret())


@app.exception_handler(RedirectToLogin)
async def _redirect_to_login(request: Request, exc: RedirectToLogin) -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden_handler(request: Request, exc: Forbidden) -> HTMLResponse:
    return _render_error(request, "Bu sayfaya erişim yetkiniz yok.", 403)


def _render_error(
    request: Request, message: str, status_code: int, current_user: User | None = None
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "error.html", {"message": message, "current_user": current_user}, status_code=status_code
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> HTMLResponse:
    return _render_error(request, "Beklenmeyen bir hata oluştu.", 500, None)


def _report_to_safe_html(report_markdown: str) -> str:
    raw_html = markdown_lib.markdown(report_markdown, extensions=["tables"])
    return bleach.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)


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
