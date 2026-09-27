import json
import os
import re
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib.metadata import version as package_version

import bleach
import markdown as markdown_lib
from fastapi import Depends, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from chesswhisper.cli import run_analysis
from chesswhisper.engine_provision import StockfishProvisionError
from chesswhisper.game_filter import FILTER_TIME_CLASSES, GameFilter
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
    start_session,
    verify_csrf,
)
from chesswhisper.web.boards import PIECE_SVGS, VIEWER_COLORS, board_cards
from chesswhisper.web.db import get_db, session_scope, utcnow
from chesswhisper.web.demo import DEMO_MAX_GAMES, DEMO_PASSWORD, DEMO_USERNAME, demo_mode
from chesswhisper.web.models import AnalysisRecord, PuzzleAttempt, User, UserApiKey
from chesswhisper.web.puzzles import THEMES as PUZZLE_THEMES
from chesswhisper.web.puzzles import (
    Puzzle,
    check_moves,
    filter_by_theme,
    legal_moves,
    order_puzzles,
    puzzles_from_records,
    theme_counts,
)
from chesswhisper.web.ratelimit import SlidingWindowLimiter
from chesswhisper.web.review import ReviewState, review_state
from chesswhisper.web.security import (
    MIN_PASSWORD_LENGTH,
    decrypt_api_key,
    encrypt_api_key,
    generate_api_token,
    get_session_secret,
    hash_api_token,
    hash_password,
    verify_password,
)
from chesswhisper.web.trend import build_trend
from chesswhisper.web.ui_text import UI_LANGS, set_ui_lang, ui_lang, ui_text
from chesswhisper.web.worker import JobWorker, worker_count_from_env

MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15
# Per client address, across all accounts.
LOGIN_RATE_LIMIT = 20
LOGIN_RATE_WINDOW_SECONDS = 5 * 60
ALLOW_REGISTRATION_ENV = "CHESSWHISPER_ALLOW_REGISTRATION"
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

login_limiter = SlidingWindowLimiter(LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW_SECONDS)


def registration_open() -> bool:
    """Self-service sign-up, on unless CHESSWHISPER_ALLOW_REGISTRATION is 0/false/no/off."""
    return os.environ.get(ALLOW_REGISTRATION_ENV, "1").strip().lower() not in ("0", "false", "no", "off")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Picks up analyses a previous process left queued or unfinished.
    await run_in_threadpool(_worker.start)
    yield
    await run_in_threadpool(_worker.stop)


app = FastAPI(
    lifespan=_lifespan,
    title="chesswhisper API",
    version=package_version("chesswhisper"),
    description=(
        "Start chess analyses, follow their progress and read the results. "
        "Authenticate with the personal token from Settings → API access, sent as "
        "`Authorization: Bearer <token>`."
    ),
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)
app.add_middleware(SessionMiddleware, secret_key=get_session_secret().hex())


@app.exception_handler(RedirectToLogin)
async def _redirect_to_login(request: Request, exc: RedirectToLogin) -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden_handler(request: Request, exc: Forbidden) -> HTMLResponse:
    return _render_error(request, _tr(request, "error.forbidden"), 403)


def _tr(request: Request, key: str, **params) -> str:
    return ui_text(key, ui_lang(request), **params)


def _render(request: Request, template_name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    lang = ui_lang(request)
    return templates.TemplateResponse(
        request, template_name,
        {
            **context,
            "csrf_token": get_csrf_token(request),
            "ui_lang": lang,
            "t": lambda key, **params: ui_text(key, lang, **params),
            "registration_open": registration_open(),
            "demo": demo_mode(),
            "demo_username": DEMO_USERNAME,
            "demo_password": DEMO_PASSWORD,
        },
        status_code=status_code,
    )


def _render_error(
    request: Request, message: str, status_code: int, current_user: User | None = None
) -> HTMLResponse:
    return _render(request, "error.html", {"message": message, "current_user": current_user}, status_code)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> HTMLResponse | JSONResponse:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Internal server error."}, status_code=500)
    return _render_error(request, _tr(request, "error.unexpected"), 500, None)


def _report_to_safe_html(report_markdown: str) -> str:
    raw_html = markdown_lib.markdown(report_markdown, extensions=["tables"])
    return bleach.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)


def _enqueue(job_id: str) -> None:
    _worker.submit(job_id)


def save_analysis(
    db: Session, user_id: int, aggregate, commentary: str, lang: str, llm: str, created_at=None,
    pgn_text: str | None = None, filters_json: str | None = None,
) -> AnalysisRecord:
    """Store a finished analysis: the report plus what the boards, viewer and puzzles need."""
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
        report_markdown=render_markdown(aggregate, commentary, lang),
        critical_moves_json=json.dumps(moves) if moves else None,
        games_json=json.dumps(timelines) if timelines else None,
        pgn_text=pgn_text,
        filters_json=filters_json,
    )
    if created_at is not None:
        record.created_at = created_at
    db.add(record)
    db.commit()
    return record


def _llm_key_for(user_id: int, llm: str) -> str | None:
    """The user's saved key for *llm*, decrypted, or None if there is none."""
    with session_scope() as db:
        key_row = db.get(UserApiKey, user_id)
        encrypted = None
        if key_row is not None:
            encrypted = key_row.anthropic_key_encrypted if llm == "claude" else key_row.openai_key_encrypted
        return decrypt_api_key(encrypted) if encrypted else None


def _run_analysis_job(job_id: str) -> None:
    """Run one queued job to the end; called by the worker pool."""
    job = jobs.get_job(job_id)
    inputs = jobs.job_inputs(job_id)
    if job is None or inputs is None or not jobs.mark_running(job_id):
        return
    ui_language = inputs.ui_language
    tmp_path = None
    db = next(get_db())
    try:
        llm_api_key = None
        if inputs.llm != "none":
            # Read now rather than stored with the job, so no key sits in the queue.
            llm_api_key = _llm_key_for(job.user_id, inputs.llm)
            if llm_api_key is None:
                jobs.mark_error(job_id, ui_text("settings.msg.missing_key", ui_language))
                return
        with tempfile.NamedTemporaryFile(suffix=".pgn", delete=False) as tmp:
            tmp.write(inputs.pgn_text.encode("utf-8"))
            tmp_path = tmp.name
        try:
            aggregate, commentary, warnings = run_analysis(
                tmp_path, inputs.player, inputs.lang, inputs.llm,
                llm_api_key=llm_api_key,
                on_progress=lambda completed, total: jobs.update_progress(job_id, completed, total),
                game_filter=GameFilter.from_json(inputs.filters_json),
            )
        except (OSError, ValueError) as exc:
            jobs.mark_error(job_id, str(exc))
            return
        except StockfishProvisionError as exc:
            jobs.mark_error(job_id, ui_text("error.engine_setup", ui_language, detail=exc))
            return

        record = save_analysis(
            db, job.user_id, aggregate, commentary, inputs.lang, inputs.llm,
            pgn_text=inputs.pgn_text, filters_json=inputs.filters_json,
        )
        jobs.mark_done(job_id, record.id, inputs.fetch_warnings + warnings)
    except Exception:
        # Last-resort safety net: an exception type not handled above (e.g. a
        # database error on commit) must still end the job, or its progress
        # page would wait forever. Message text matches the top-level
        # _unhandled_exception_handler for the same failure class.
        jobs.mark_error(job_id, ui_text("error.unexpected", ui_language))
    finally:
        db.close()
        if tmp_path is not None and os.path.exists(tmp_path):
            os.remove(tmp_path)


_worker = JobWorker(_run_analysis_job, workers=worker_count_from_env())


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
        return _render_error(request, _tr(request, "error.game_not_found"), 404, current_user)
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


@app.get("/language/{lang}", response_model=None)
async def switch_language(
    request: Request, lang: str, next_path: str = Query("/", alias="next")
) -> RedirectResponse:
    if lang in UI_LANGS:
        set_ui_lang(request, lang)
    # Only same-site paths, so the switch can't be used as an open redirect.
    safe = next_path.startswith("/") and not next_path.startswith("//") and "\\" not in next_path
    target = next_path if safe else "/"
    return RedirectResponse(target, status_code=303)


@app.get("/register", response_class=HTMLResponse, response_model=None)
async def register_form(
    request: Request, current_user: User | None = Depends(get_optional_current_user)
) -> HTMLResponse | RedirectResponse:
    if current_user is not None:
        return RedirectResponse("/", status_code=303)
    if not registration_open():
        return _render_error(request, _tr(request, "auth.registration_closed"), 403)
    return _render(request, "register.html", {"current_user": None})


@app.post("/register", response_class=HTMLResponse, response_model=None)
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse | RedirectResponse:
    if not registration_open():
        return _render_error(request, _tr(request, "auth.registration_closed"), 403)
    username = username.strip()
    if not _USERNAME_RE.fullmatch(username):
        return _render(
            request, "register.html",
            {"current_user": None, "error": _tr(request, "auth.error.username")},
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        return _render(
            request, "register.html",
            {"current_user": None, "error": _tr(request, "auth.error.password_length", n=MIN_PASSWORD_LENGTH)},
        )
    if db.query(User).filter_by(username=username).first() is not None:
        return _render(
            request, "register.html",
            {"current_user": None, "error": _tr(request, "auth.error.username_taken")},
        )

    user = User(username=username, password_hash=hash_password(password), is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)

    start_session(request, user)
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
    client = request.client.host if request.client else "unknown"
    if not login_limiter.allow(client):
        return _render(
            request, "login.html", {"current_user": None, "error": _tr(request, "auth.error.rate_limited")},
            status_code=429,
        )

    user = db.query(User).filter_by(username=username.strip()).first()

    if user is not None and user.locked_until is not None and user.locked_until > _naive_utcnow():
        remaining_minutes = max(1, int((user.locked_until - _naive_utcnow()).total_seconds() // 60) + 1)
        return _render(
            request, "login.html",
            {
                "current_user": None,
                "error": _tr(request, "auth.error.locked", minutes=remaining_minutes),
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
            {"current_user": None, "error": _tr(request, "auth.error.invalid")},
        )

    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()

    start_session(request, user)
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
    new_api_token: str | None = None,
) -> HTMLResponse:
    key_row = db.get(UserApiKey, current_user.id)
    return _render(
        request, "settings.html",
        {
            "current_user": current_user,
            "has_api_token": current_user.api_token_hash is not None,
            "new_api_token": new_api_token,
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
        request, current_user, db, message=_tr(request, "settings.msg.missing_key") if missing_key else None
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
    if demo_mode():
        return _render_error(request, _tr(request, "demo.keys_off"), 403, current_user)
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

    return _render_settings(request, current_user, db, message=_tr(request, "settings.msg.saved"))


@app.post("/settings/delete-key", response_class=HTMLResponse)
async def settings_delete_key(
    request: Request,
    provider: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    if demo_mode():
        return _render_error(request, _tr(request, "demo.keys_off"), 403, current_user)
    column = _KEY_COLUMNS.get(provider)
    if column is None:
        return _render_error(request, _tr(request, "error.bad_provider"), 400, current_user)

    key_row = db.get(UserApiKey, current_user.id)
    if key_row is not None:
        setattr(key_row, column, None)
        key_row.updated_at = utcnow()
        db.commit()

    return _render_settings(request, current_user, db, message=_tr(request, "settings.msg.key_removed"))


@app.post("/settings/api-token", response_class=HTMLResponse)
async def settings_create_api_token(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    token = generate_api_token()
    current_user.api_token_hash = hash_api_token(token)  # replaces any earlier token
    db.commit()
    return _render_settings(request, current_user, db, new_api_token=token)


@app.post("/settings/api-token/delete", response_class=HTMLResponse)
async def settings_delete_api_token(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    current_user.api_token_hash = None
    db.commit()
    return _render_settings(request, current_user, db, message=_tr(request, "settings.msg.token_revoked"))


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
        error = _tr(request, "settings.error.current_password")
    elif len(new_password) < MIN_PASSWORD_LENGTH:
        error = _tr(request, "settings.error.new_password_length", n=MIN_PASSWORD_LENGTH)
    elif new_password != confirm_password:
        error = _tr(request, "settings.error.mismatch")
    if error is not None:
        return _render_settings(request, current_user, db, password_error=error)

    current_user.password_hash = hash_password(new_password)
    current_user.session_version += 1
    db.commit()
    start_session(request, current_user)  # keep this session; every other one is now stale
    return _render_settings(request, current_user, db, password_message=_tr(request, "settings.msg.password_updated"))


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
        return _render_error(request, _tr(request, "error.record_not_found"), 404, current_user)

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
            "filter_url": f"/history/{record_id}/filter" if record.pgn_text else None,
            "current_filter": GameFilter.from_json(record.filters_json),
            "filter_time_classes": FILTER_TIME_CLASSES,
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
        return _render_error(request, _tr(request, "error.record_not_found"), 404, current_user)
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
        return _render_error(request, _tr(request, "error.record_not_found"), 404, current_user)
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
        return _render_error(request, _tr(request, "error.record_not_found"), 404, current_user)

    db.delete(record)
    db.commit()
    return RedirectResponse("/history", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, current_user: User = Depends(get_current_user)) -> HTMLResponse:
    return _render(
        request, "index.html",
        {
            "current_user": current_user,
            "default_max_games": DEFAULT_MAX_GAMES,
            "max_games_limit": DEMO_MAX_GAMES if demo_mode() else MAX_GAMES_LIMIT,
            "filter_time_classes": FILTER_TIME_CLASSES,
        },
    )


_UCI_RE = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?")


@dataclass
class _PuzzleProgress:
    solved: dict[tuple[int, int], bool]  # attempted puzzles: solved at least once?
    attempts: dict[tuple[int, int], list[tuple[datetime, bool]]]
    states: dict[tuple[int, int], ReviewState]


def _user_puzzles(db: Session, user: User) -> tuple[list[Puzzle], _PuzzleProgress]:
    """The user's puzzles in the order to offer them, and their progress on each."""
    records = (
        db.query(AnalysisRecord).filter_by(user_id=user.id).order_by(AnalysisRecord.created_at.desc()).all()
    )
    solved: dict[tuple[int, int], bool] = {}
    attempts: dict[tuple[int, int], list[tuple[datetime, bool]]] = {}
    for record_id, index, was_solved, created_at in db.query(
        PuzzleAttempt.record_id, PuzzleAttempt.move_index, PuzzleAttempt.solved, PuzzleAttempt.created_at
    ).filter_by(user_id=user.id):
        key = (record_id, index)
        solved[key] = solved.get(key, False) or was_solved
        attempts.setdefault(key, []).append((created_at, was_solved))
    states = {key: review_state(history) for key, history in attempts.items()}
    progress = _PuzzleProgress(solved=solved, attempts=attempts, states=states)
    return order_puzzles(puzzles_from_records(records), states, utcnow()), progress


def _due_count(puzzles: list[Puzzle], progress: _PuzzleProgress, now: datetime) -> int:
    return sum(1 for p in puzzles if p.key in progress.states and progress.states[p.key].status(now) == "due")


def _solved_count(puzzles: list[Puzzle], solved: dict[tuple[int, int], bool]) -> int:
    return sum(1 for p in puzzles if solved.get(p.key))


def _theme_param(theme: str | None) -> str | None:
    return theme if theme in PUZZLE_THEMES else None


def _theme_query(theme: str | None) -> str:
    return f"?theme={theme}" if theme else ""


def _theme_context(puzzles: list[Puzzle], theme: str | None) -> dict:
    return {"theme": theme, "theme_counts": theme_counts(puzzles), "next_url": f"/puzzles{_theme_query(theme)}"}


@app.get("/puzzles", response_class=HTMLResponse, response_model=None)
async def puzzles_next(
    request: Request,
    theme: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    theme = _theme_param(theme)
    puzzles, _ = _user_puzzles(db, current_user)
    themed = filter_by_theme(puzzles, theme)
    if not themed:
        return _render(
            request, "puzzle.html",
            {"current_user": current_user, "puzzle": None, "has_any": bool(puzzles), **_theme_context(puzzles, theme)},
        )
    first = themed[0]
    return RedirectResponse(f"/puzzles/{first.record_id}/{first.index}{_theme_query(theme)}", status_code=303)


@app.get("/puzzles/{record_id}/{index}", response_class=HTMLResponse)
async def puzzle_page(
    request: Request,
    record_id: int,
    index: int,
    theme: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    theme = _theme_param(theme)
    puzzles, progress = _user_puzzles(db, current_user)
    themed = filter_by_theme(puzzles, theme)
    puzzle = next((p for p in puzzles if p.key == (record_id, index)), None)
    if puzzle is None:
        return _render_error(request, _tr(request, "error.puzzle_not_found"), 404, current_user)
    now = utcnow()
    state = progress.states.get(puzzle.key, ReviewState())
    record = db.get(AnalysisRecord, record_id)
    viewer_url = (
        f"/history/{record_id}/games/{puzzle.game_number}?ply={puzzle.ply}"
        if record is not None and record.games_json else None
    )
    return _render(
        request, "puzzle.html",
        {
            "current_user": current_user,
            "puzzle": puzzle,
            "legal_moves": legal_moves(puzzle.fen),
            "solved_before": progress.solved.get(puzzle.key, False),
            "solved_count": _solved_count(themed, progress.solved),
            "total": len(themed),
            "due_count": _due_count(themed, progress, now),
            "review_status": state.status(now),
            "days_until_due": state.days_until_due(now),
            **_theme_context(puzzles, theme),
            "viewer_url": viewer_url,
            "pieces": PIECE_SVGS,
            "board_colors": VIEWER_COLORS,
        },
    )


@app.post("/puzzles/{record_id}/{index}/attempt", response_model=None)
async def puzzle_attempt(
    record_id: int,
    index: int,
    move: str = Form(...),
    previous: str = Form(default=""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> JSONResponse:
    """Judge the player's latest *move*, after the *previous* ones (comma-separated) of this try."""
    puzzles, progress = _user_puzzles(db, current_user)
    puzzle = next((p for p in puzzles if p.key == (record_id, index)), None)
    if puzzle is None:
        return JSONResponse({"detail": "Puzzle not found."}, status_code=404)
    moves = [m.strip().lower() for m in previous.split(",") if m.strip()] + [move.strip().lower()]
    if not all(_UCI_RE.fullmatch(m) for m in moves):
        return JSONResponse({"detail": "Not a legal move in this position."}, status_code=400)
    try:
        outcome = check_moves(puzzle, moves)
    except ValueError:
        return JSONResponse({"detail": "Not a legal move in this position."}, status_code=400)

    expected = puzzle.solution[2 * outcome.step]
    if outcome.status == "continue":
        # Right so far: nothing is saved until the line is finished or missed.
        return JSONResponse({
            "correct": True,
            "status": "continue",
            "best_uci": moves[-1],
            "reply_uci": outcome.reply_uci,
            "reply_san": outcome.reply_san,
            "fen": outcome.fen,
            "legal_moves": legal_moves(outcome.fen) if outcome.fen else [],
        })

    correct = outcome.status == "solved"
    move = moves[-1]
    now = utcnow()
    db.add(PuzzleAttempt(
        user_id=current_user.id, record_id=record_id, move_index=index, move_uci=move, solved=correct,
        created_at=now,
    ))
    db.commit()
    solved = progress.solved
    solved[puzzle.key] = solved.get(puzzle.key, False) or correct
    state = review_state([*progress.attempts.get(puzzle.key, []), (now, correct)])
    return JSONResponse({
        "next_review_days": state.days_until_due(now),
        "correct": correct,
        "status": outcome.status,
        "best_uci": move if correct else expected,
        "best_san": puzzle.solution_san,
        "played_uci": puzzle.played_uci,
        "played_san": puzzle.played_san,
        "solved": _solved_count(puzzles, solved),
        "total": len(puzzles),
    })


class AnalysisRequestError(Exception):
    """An analysis request that can't start; *message* is already translated."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class MissingLlmKey(Exception):
    """Commentary was requested from a provider the user has no key for."""


class AnalysisAlreadyRunning(Exception):
    def __init__(self, job: jobs.Job):
        super().__init__(job.id)
        self.job = job


async def start_analysis(
    db: Session,
    user_id: int,
    *,
    source: str,
    username: str = "",
    max_games: int | str = DEFAULT_MAX_GAMES,
    pgn_bytes: bytes | None = None,
    player: str = "",
    lang: str = "en",
    llm: str = "none",
    ui_language: str = "en",
    game_filter: GameFilter | None = None,
) -> jobs.Job:
    """Validate a request, fetch the games if asked to, and start the background job.

    Shared by the web form and the JSON API. *source* is "file" (with
    *pgn_bytes*) or one of FETCH_SOURCES (with *username*). Raises
    AnalysisRequestError, MissingLlmKey or AnalysisAlreadyRunning.
    """

    def fail(status_code: int, key: str, **params) -> AnalysisRequestError:
        return AnalysisRequestError(status_code, ui_text(key, ui_language, **params))

    fetching = source in FETCH_SOURCES
    if source != "file" and not fetching:
        raise fail(400, "error.bad_source")
    if lang not in REPORT_LANGS:
        raise fail(400, "error.bad_lang")
    if llm not in LLM_CHOICES:
        raise fail(400, "error.bad_llm")
    if demo_mode() and llm != "none":
        raise fail(400, "error.demo_no_llm")

    running = jobs.running_job_for(user_id)
    if running is not None:
        # One analysis per user at a time: each one runs its own Stockfish.
        raise AnalysisAlreadyRunning(running)

    contents = b""
    game_count = DEFAULT_MAX_GAMES
    if fetching:
        username = username.strip()
        if not username:
            raise fail(400, "error.need_username")
        try:
            game_count = int(max_games)
        except (TypeError, ValueError):
            raise fail(400, "error.bad_game_count") from None
        if demo_mode() and game_count > DEMO_MAX_GAMES:
            raise fail(400, "error.demo_max_games", n=DEMO_MAX_GAMES)
    else:
        if not pgn_bytes:
            raise fail(400, "error.need_file")
        if len(pgn_bytes) > MAX_UPLOAD_BYTES:
            raise fail(413, "error.file_too_large", mb=MAX_UPLOAD_BYTES // (1024 * 1024))
        contents = pgn_bytes

    if llm != "none":
        key_row = db.get(UserApiKey, user_id)
        encrypted = None
        if key_row is not None:
            encrypted = key_row.anthropic_key_encrypted if llm == "claude" else key_row.openai_key_encrypted
        if not encrypted:
            raise MissingLlmKey()

    fetch_warnings: list[str] = []
    if fetching:
        try:
            fetched = await run_in_threadpool(fetch_pgn, source, username, game_count)
        except GameSourceUnavailable as exc:
            raise fail(502, "error.fetch_failed", detail=exc) from exc
        except GameFetchError as exc:
            raise AnalysisRequestError(400, str(exc)) from exc
        contents = fetched.pgn_text.encode("utf-8")
        fetch_warnings = fetched.warnings
        player = player or fetched.player

    inputs = jobs.JobInputs(
        pgn_text=contents.decode("utf-8", errors="replace"),
        player=player or None,
        lang=lang,
        llm=llm,
        ui_language=ui_language,
        fetch_warnings=fetch_warnings,
        filters_json=game_filter.to_json() if game_filter is not None else None,
    )
    job, created = jobs.create_job_unless_running(user_id, inputs)
    if not created:
        # Another submission started an analysis while this one was fetching.
        raise AnalysisAlreadyRunning(job)
    _enqueue(job.id)
    return job


def _form_filter(time_controls: list[str], color: str) -> GameFilter | None:
    """The filter chosen in a form, or None if it names something unknown."""
    try:
        return GameFilter(time_classes=frozenset(tc for tc in time_controls if tc), color=color or None)
    except ValueError:
        return None


@app.post("/history/{record_id}/filter", response_class=HTMLResponse, response_model=None)
async def history_filter(
    request: Request,
    record_id: int,
    time_control: list[str] = Form(default=[]),  # noqa: B008 - FastAPI's own idiom
    color: str = Form(default=""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse | RedirectResponse:
    """Run a saved analysis's games again, narrowed by a filter (quick: the evaluations are cached)."""
    record = db.get(AnalysisRecord, record_id)
    if record is None or record.user_id != current_user.id or not record.pgn_text:
        return _render_error(request, _tr(request, "error.record_not_found"), 404, current_user)
    game_filter = _form_filter(time_control, color)
    if game_filter is None:
        return _render_error(request, _tr(request, "error.bad_filter"), 400, current_user)
    try:
        job = await start_analysis(
            db, current_user.id, source="file", pgn_bytes=record.pgn_text.encode("utf-8"),
            player=record.player_name, lang=record.lang, llm="none", ui_language=ui_lang(request),
            game_filter=game_filter,
        )
    except AnalysisAlreadyRunning as exc:
        return RedirectResponse(f"/analyze/progress/{exc.job.id}", status_code=303)
    except AnalysisRequestError as exc:
        return _render_error(request, exc.message, exc.status_code, current_user)
    return RedirectResponse(f"/analyze/progress/{job.id}", status_code=303)


@app.post("/analyze", response_class=HTMLResponse, response_model=None)
async def analyze(
    request: Request,
    pgn: UploadFile | None = File(default=None),  # noqa: B008 - FastAPI's own idiom
    source: str = Form(default="file"),
    username: str = Form(default=""),
    max_games: str = Form(default=str(DEFAULT_MAX_GAMES)),
    player: str = Form(default=""),
    lang: str = Form(default="en"),
    llm: str = Form(default="none"),
    time_control: list[str] = Form(default=[]),  # noqa: B008 - FastAPI's own idiom
    color: str = Form(default=""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse | RedirectResponse:
    game_filter = _form_filter(time_control, color)
    if game_filter is None:
        return _render_error(request, _tr(request, "error.bad_filter"), 400, current_user)
    pgn_bytes = None
    if source == "file" and pgn is not None and pgn.filename:
        # Read one byte past the limit so start_analysis can tell it was exceeded.
        pgn_bytes = await pgn.read(MAX_UPLOAD_BYTES + 1)
    try:
        job = await start_analysis(
            db, current_user.id, source=source, username=username, max_games=max_games,
            pgn_bytes=pgn_bytes, player=player, lang=lang, llm=llm, ui_language=ui_lang(request),
            game_filter=game_filter,
        )
    except AnalysisAlreadyRunning as exc:
        return RedirectResponse(f"/analyze/progress/{exc.job.id}", status_code=303)
    except MissingLlmKey:
        return RedirectResponse("/settings?message=missing_key", status_code=303)
    except AnalysisRequestError as exc:
        return _render_error(request, exc.message, exc.status_code, current_user)
    return RedirectResponse(f"/analyze/progress/{job.id}", status_code=303)


@app.get("/analyze/progress/{job_id}", response_class=HTMLResponse, response_model=None)
async def analyze_progress(
    request: Request,
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    job = jobs.get_job(job_id)
    if job is None:
        return _render_error(request, _tr(request, "error.analysis_not_found"), 404, current_user)
    if job.user_id != current_user.id:
        raise Forbidden()
    return _render(request, "progress.html", {"current_user": current_user, "job_id": job_id})


@app.get("/analyze/status/{job_id}", response_model=None)
async def analyze_status(
    request: Request,
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    job = jobs.get_job(job_id)
    if job is None:
        return JSONResponse({"status": "error", "message": _tr(request, "error.analysis_not_found")}, status_code=404)
    if job.user_id != current_user.id:
        return JSONResponse({"status": "error", "message": _tr(request, "error.unauthorized")}, status_code=403)

    body: dict[str, object] = {"status": job.status, "completed": job.completed, "total": job.total}
    if job.status == "queued":
        body["position"] = jobs.queue_position(job.id)
    elif job.status == "done":
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
        return _render_error(request, _tr(request, "error.user_not_found"), 404, current_user)

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
        return _render_error(request, _tr(request, "error.record_not_found"), 404, current_user)

    report_html = _report_to_safe_html(record.report_markdown)
    return _render(
        request, "history_detail.html",
        {
            "current_user": current_user, "report_html": report_html,
            "cards": board_cards(record.critical_moves_json),
            "games": _game_summaries(record),
            "games_url_prefix": f"/admin/users/{user_id}/history/{record_id}/games",
            "download_url": f"/admin/users/{user_id}/history/{record_id}/report.md",
            "back_url": f"/admin/users/{user_id}", "viewing_as_admin": True,
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
        return _render_error(request, _tr(request, "error.record_not_found"), 404, current_user)
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
        return _render_error(request, _tr(request, "error.record_not_found"), 404, current_user)
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
        return _render_error(request, _tr(request, "error.delete_self"), 400, current_user)

    target = db.get(User, user_id)
    if target is None:
        return _render_error(request, _tr(request, "error.user_not_found"), 404, current_user)

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
        return _render_error(request, _tr(request, "error.user_not_found"), 404, current_user)

    if len(new_password) < MIN_PASSWORD_LENGTH:
        return _render_error(request, _tr(request, "auth.error.password_length", n=MIN_PASSWORD_LENGTH), 400, current_user)

    target.password_hash = hash_password(new_password)
    # A reset is how an admin lets a locked-out user back in.
    target.failed_login_attempts = 0
    target.locked_until = None
    target.session_version += 1  # sign the user out everywhere
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


# The JSON API lives in its own module; it calls start_analysis above.
from chesswhisper.web.api import router as api_router  # noqa: E402

app.include_router(api_router)

# Keep the published OpenAPI schema (and /api/docs) to the JSON API.
for _route in app.routes:
    if hasattr(_route, "include_in_schema") and not getattr(_route, "path", "").startswith("/api/"):
        _route.include_in_schema = False
