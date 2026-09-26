import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from datetime import timedelta

import bleach
import markdown as markdown_lib
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
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
from chesswhisper.stats_engine import critical_moves
from chesswhisper.timeline import game_timelines
from chesswhisper.web import jobs
from chesswhisper.web.auth import (
    Forbidden,
    RedirectToLogin,
    get_csrf_token,
    get_current_user,
    get_optional_current_user,
    require_admin,
    verify_csrf,
)
from chesswhisper.web.boards import PIECE_SVGS, VIEWER_COLORS, board_cards
from chesswhisper.web.db import get_db, utcnow
from chesswhisper.web.models import AnalysisRecord, User, UserApiKey
from chesswhisper.web.security import (
    MIN_PASSWORD_LENGTH,
    decrypt_api_key,
    encrypt_api_key,
    get_session_secret,
    hash_password,
    verify_password,
)
from chesswhisper.web.trend import build_trend

MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15
FETCH_SOURCES = ("lichess", "chesscom")
REPORT_LANGS = ("tr", "en")
LLM_CHOICES = ("none", "claude", "openai")
# A PGN of several thousand games fits comfortably; this only stops runaway uploads.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_USERNAME_RE = re.compile(r"\S{3,30}")
_KEY_COLUMNS = {"anthropic": "anthropic_key_encrypted", "openai": "openai_key_encrypted"}


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


def _start_background(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


def _run_analysis_job(
    job_id: str,
    user_id: int,
    tmp_path: str,
    player: str | None,
    lang: str,
    llm: str,
    llm_api_key: str | None,
    fetch_warnings: list[str],
) -> None:
    db = next(get_db())
    try:
        try:
            aggregate, commentary, warnings = run_analysis(
                tmp_path, player, lang, llm,
                llm_api_key=llm_api_key,
                on_progress=lambda completed, total: jobs.update_progress(job_id, completed, total),
            )
        except (OSError, ValueError) as exc:
            jobs.mark_error(job_id, str(exc))
            return
        except StockfishProvisionError as exc:
            jobs.mark_error(job_id, f"Motor kurulum hatası: {exc}")
            return

        report_markdown = render_markdown(aggregate, commentary, lang)
        moves = critical_moves(aggregate.games)
        timelines = game_timelines(aggregate.games)
        record = AnalysisRecord(
            user_id=user_id,
            player_name=aggregate.player,
            lang=lang,
            llm_used=llm,
            games_count=len(aggregate.games),
            overall_acpl=aggregate.overall_acpl,
            overall_accuracy=aggregate.overall_accuracy,
            report_markdown=report_markdown,
            critical_moves_json=json.dumps(moves) if moves else None,
            games_json=json.dumps(timelines) if timelines else None,
        )
        db.add(record)
        db.commit()
        jobs.mark_done(job_id, record.id, fetch_warnings + warnings)
    except Exception:
        # Last-resort safety net: without this, an exception type this
        # function doesn't already handle (e.g. a database error on commit)
        # would propagate out of the background thread silently, leaving
        # the job stuck in "running" forever from the client's point of
        # view. Message text matches the existing top-level
        # _unhandled_exception_handler for the same failure class.
        jobs.mark_error(job_id, "Beklenmeyen bir hata oluştu.")
    finally:
        db.close()
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _timelines(record: AnalysisRecord) -> list[dict]:
    if not record.games_json:
        return []
    try:
        timelines = json.loads(record.games_json)
    except ValueError:
        return []
    return timelines if isinstance(timelines, list) else []


def _game_summaries(record: AnalysisRecord) -> list[dict]:
    """What the report page's game list shows, without each game's moves."""
    return [{k: v for k, v in timeline.items() if k != "moves"} for timeline in _timelines(record)]


def _render_game_viewer(
    request: Request, record: AnalysisRecord, game_number: int, url_prefix: str, current_user: User
) -> HTMLResponse:
    timelines = _timelines(record)
    if not 1 <= game_number <= len(timelines):
        return _render_error(request, "Oyun bulunamadı.", 404, current_user)
    return _render(
        request, "game_viewer.html",
        {
            "current_user": current_user,
            "timeline": timelines[game_number - 1],
            "game_number": game_number,
            "games_total": len(timelines),
            "url_prefix": url_prefix,
            "pieces": PIECE_SVGS,
            "board_colors": VIEWER_COLORS,
        },
    )


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
    username = username.strip()
    if not _USERNAME_RE.fullmatch(username):
        return _render(
            request, "register.html",
            {"current_user": None, "error": "Kullanıcı adı 3-30 karakter olmalı ve boşluk içermemeli."},
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        return _render(
            request, "register.html",
            {"current_user": None, "error": f"Parola en az {MIN_PASSWORD_LENGTH} karakter olmalı."},
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
    user = db.query(User).filter_by(username=username.strip()).first()

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


def _render_settings(
    request: Request,
    current_user: User,
    db: Session,
    message: str | None = None,
    password_message: str | None = None,
    password_error: str | None = None,
) -> HTMLResponse:
    key_row = db.get(UserApiKey, current_user.id)
    return _render(
        request, "settings.html",
        {
            "current_user": current_user,
            "has_anthropic_key": bool(key_row and key_row.anthropic_key_encrypted),
            "has_openai_key": bool(key_row and key_row.openai_key_encrypted),
            "message": message,
            "password_message": password_message,
            "password_error": password_error,
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_form(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    missing_key = request.query_params.get("message") == "missing_key"
    return _render_settings(
        request, current_user, db, message="Devam etmeden önce bir API anahtarı gir." if missing_key else None
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

    anthropic_key, openai_key = anthropic_key.strip(), openai_key.strip()
    if anthropic_key:
        key_row.anthropic_key_encrypted = encrypt_api_key(anthropic_key)
    if openai_key:
        key_row.openai_key_encrypted = encrypt_api_key(openai_key)
    key_row.updated_at = utcnow()

    db.commit()

    return _render_settings(request, current_user, db, message="Kaydedildi.")


@app.post("/settings/delete-key", response_class=HTMLResponse)
async def settings_delete_key(
    request: Request,
    provider: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    column = _KEY_COLUMNS.get(provider)
    if column is None:
        return _render_error(request, "Geçersiz sağlayıcı.", 400, current_user)

    key_row = db.get(UserApiKey, current_user.id)
    if key_row is not None:
        setattr(key_row, column, None)
        key_row.updated_at = utcnow()
        db.commit()

    return _render_settings(request, current_user, db, message="Anahtar silindi.")


@app.post("/settings/password", response_class=HTMLResponse)
async def settings_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    error = None
    if not verify_password(current_password, current_user.password_hash):
        error = "Mevcut parola yanlış."
    elif len(new_password) < MIN_PASSWORD_LENGTH:
        error = f"Yeni parola en az {MIN_PASSWORD_LENGTH} karakter olmalı."
    elif new_password != confirm_password:
        error = "Yeni parola ile tekrarı eşleşmiyor."
    if error is not None:
        return _render_settings(request, current_user, db, password_error=error)

    current_user.password_hash = hash_password(new_password)
    db.commit()
    return _render_settings(request, current_user, db, password_message="Parola güncellendi.")


@app.get("/history", response_class=HTMLResponse)
async def history_list(
    request: Request,
    player: str | None = None,
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
            "trend": build_trend(records, player),
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

    warnings: list[str] = []
    job_id = request.query_params.get("job")
    if job_id is not None:
        job = jobs.get_job(job_id)
        if (
            job is not None
            and job.user_id == current_user.id
            and job.status == "done"
            and job.record_id == record_id
        ):
            warnings = job.warnings

    report_html = _report_to_safe_html(record.report_markdown)
    return _render(
        request, "history_detail.html",
        {
            "current_user": current_user, "report_html": report_html,
            "cards": board_cards(record.critical_moves_json),
            "games": _game_summaries(record), "games_url_prefix": f"/history/{record_id}/games",
            "download_url": f"/history/{record_id}/report.md",
            "back_url": "/history", "warnings": warnings,
        },
    )


def _markdown_download(record: AnalysisRecord) -> Response:
    filename = f"chesswhisper-report-{record.id}-{record.created_at.strftime('%Y%m%d')}.md"
    return Response(
        record.report_markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/history/{record_id}/report.md", response_model=None)
async def history_download(
    request: Request,
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    record = db.get(AnalysisRecord, record_id)
    if record is None or record.user_id != current_user.id:
        return _render_error(request, "Kayıt bulunamadı.", 404, current_user)
    return _markdown_download(record)


@app.get("/history/{record_id}/games/{game_number}", response_class=HTMLResponse)
async def history_game(
    request: Request,
    record_id: int,
    game_number: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    record = db.get(AnalysisRecord, record_id)
    if record is None or record.user_id != current_user.id:
        return _render_error(request, "Kayıt bulunamadı.", 404, current_user)
    return _render_game_viewer(request, record, game_number, f"/history/{record_id}", current_user)


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
    if lang not in REPORT_LANGS:
        return _render_error(request, "Geçersiz rapor dili.", 400, current_user)
    if llm not in LLM_CHOICES:
        return _render_error(request, "Geçersiz koçluk yorumu seçeneği.", 400, current_user)

    running = jobs.running_job_for(current_user.id)
    if running is not None:
        # One analysis per user at a time: each one runs its own Stockfish.
        return RedirectResponse(f"/analyze/progress/{running.id}", status_code=303)

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
        contents = await pgn.read(MAX_UPLOAD_BYTES + 1)
        if len(contents) > MAX_UPLOAD_BYTES:
            return _render_error(
                request, f"PGN dosyası en fazla {MAX_UPLOAD_BYTES // (1024 * 1024)} MB olabilir.", 413, current_user
            )

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

    with tempfile.NamedTemporaryFile(suffix=".pgn", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    job, created = jobs.create_job_unless_running(current_user.id)
    if not created:
        # Another submission started an analysis while this one was fetching.
        os.remove(tmp_path)
        return RedirectResponse(f"/analyze/progress/{job.id}", status_code=303)
    user_id = current_user.id
    _start_background(
        lambda: _run_analysis_job(
            job.id, user_id, tmp_path, player or None, lang, llm, llm_api_key, fetch_warnings
        )
    )
    return RedirectResponse(f"/analyze/progress/{job.id}", status_code=303)


@app.get("/analyze/progress/{job_id}", response_class=HTMLResponse, response_model=None)
async def analyze_progress(
    request: Request,
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    job = jobs.get_job(job_id)
    if job is None:
        return _render_error(request, "Analiz bulunamadı.", 404, current_user)
    if job.user_id != current_user.id:
        raise Forbidden()
    return _render(request, "progress.html", {"current_user": current_user, "job_id": job_id})


@app.get("/analyze/status/{job_id}", response_model=None)
async def analyze_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    job = jobs.get_job(job_id)
    if job is None:
        return JSONResponse({"status": "error", "message": "Analiz bulunamadı."}, status_code=404)
    if job.user_id != current_user.id:
        return JSONResponse({"status": "error", "message": "Yetkisiz."}, status_code=403)

    body: dict[str, object] = {"status": job.status, "completed": job.completed, "total": job.total}
    if job.status == "done":
        body["redirect"] = f"/history/{job.record_id}?job={job.id}"
    elif job.status == "error":
        body["message"] = job.error_message
    return JSONResponse(body)


@app.get("/admin", response_class=HTMLResponse)
async def admin_users_list(
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    users = db.query(User).order_by(User.created_at).all()
    now = _naive_utcnow()
    locked_ids = {u.id for u in users if u.locked_until is not None and u.locked_until > now}
    return _render(
        request, "admin_users.html", {"current_user": current_user, "users": users, "locked_ids": locked_ids}
    )


@app.get("/admin/users/{user_id}", response_class=HTMLResponse)
async def admin_user_history(
    request: Request,
    user_id: int,
    player: str | None = None,
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
            "trend": build_trend(records, player),
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
        {
            "current_user": current_user, "report_html": report_html,
            "cards": board_cards(record.critical_moves_json),
            "games": _game_summaries(record),
            "games_url_prefix": f"/admin/users/{user_id}/history/{record_id}/games",
            "download_url": f"/admin/users/{user_id}/history/{record_id}/report.md",
            "back_url": f"/admin/users/{user_id}",
        },
    )


@app.get("/admin/users/{user_id}/history/{record_id}/report.md", response_model=None)
async def admin_history_download(
    request: Request,
    user_id: int,
    record_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    record = db.get(AnalysisRecord, record_id)
    if record is None or record.user_id != user_id:
        return _render_error(request, "Kayıt bulunamadı.", 404, current_user)
    return _markdown_download(record)


@app.get("/admin/users/{user_id}/history/{record_id}/games/{game_number}", response_class=HTMLResponse)
async def admin_user_history_game(
    request: Request,
    user_id: int,
    record_id: int,
    game_number: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    record = db.get(AnalysisRecord, record_id)
    if record is None or record.user_id != user_id:
        return _render_error(request, "Kayıt bulunamadı.", 404, current_user)
    return _render_game_viewer(
        request, record, game_number, f"/admin/users/{user_id}/history/{record_id}", current_user
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

    if len(new_password) < MIN_PASSWORD_LENGTH:
        return _render_error(request, f"Parola en az {MIN_PASSWORD_LENGTH} karakter olmalı.", 400, current_user)

    target.password_hash = hash_password(new_password)
    # A reset is how an admin lets a locked-out user back in.
    target.failed_login_attempts = 0
    target.locked_until = None
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)
