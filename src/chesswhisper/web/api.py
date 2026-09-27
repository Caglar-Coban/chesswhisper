"""JSON API: start analyses, follow their progress and read the results.

Requests authenticate with a personal token (Settings → API access) sent as
`Authorization: Bearer <token>`. Analyses start exactly as the web form's do
(same validation, one running analysis per user, the user's own LLM keys);
the interactive documentation is served at /api/docs.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from chesswhisper.game_sources import DEFAULT_MAX_GAMES, MAX_GAMES_LIMIT
from chesswhisper.web import jobs
from chesswhisper.web.db import get_db
from chesswhisper.web.models import AnalysisRecord, User
from chesswhisper.web.security import hash_api_token

router = APIRouter(prefix="/api/v1", tags=["chesswhisper"])

# auto_error=False so a missing header gets the same 401 (and message) as a bad token.
_bearer = HTTPBearer(auto_error=False, description="Your personal token from Settings → API access.")


def api_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    user = None
    if credentials is not None and credentials.credentials.strip():
        user = db.query(User).filter_by(api_token_hash=hash_api_token(credentials.credentials.strip())).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API token. Create one under Settings → API access.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


class Me(BaseModel):
    username: str
    is_admin: bool


class AnalysisRequest(BaseModel):
    source: Literal["pgn", "lichess", "chesscom"] = Field(
        description="`pgn` to send the games in `pgn`, or a site to fetch `username`'s recent games from."
    )
    pgn: str | None = Field(default=None, description="PGN text, when `source` is `pgn`.")
    username: str | None = Field(default=None, description="Account to fetch, when `source` is a site.")
    max_games: int = Field(default=DEFAULT_MAX_GAMES, ge=1, le=MAX_GAMES_LIMIT)
    player: str | None = Field(default=None, description="Whose moves to analyze; detected when omitted.")
    lang: Literal["en", "tr"] = "en"
    llm: Literal["none", "claude", "openai"] = Field(
        default="none", description="Coaching commentary, using the API key saved in your settings."
    )


class JobCreated(BaseModel):
    job_id: str
    status_url: str


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "error"]
    position: int | None = Field(default=None, description="Queued jobs ahead of this one, while it waits.")
    completed: int
    total: int
    analysis_id: int | None = None
    analysis_url: str | None = None
    error: str | None = None
    warnings: list[str] = []


class AnalysisSummary(BaseModel):
    id: int
    created_at: datetime
    player: str
    games_count: int
    accuracy: float
    acpl: float
    lang: str
    llm: str


class AnalysisDetail(AnalysisSummary):
    report_markdown: str
    critical_moves: list[dict[str, Any]]
    games: list[dict[str, Any]]


def _summary(record: AnalysisRecord) -> AnalysisSummary:
    return AnalysisSummary(
        id=record.id, created_at=record.created_at, player=record.player_name, games_count=record.games_count,
        accuracy=record.overall_accuracy, acpl=record.overall_acpl, lang=record.lang, llm=record.llm_used,
    )


def _own_record(db: Session, user: User, analysis_id: int) -> AnalysisRecord:
    record = db.get(AnalysisRecord, analysis_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return record


@router.get("/me", response_model=Me, summary="Who the token belongs to")
def me(user: User = Depends(api_user)) -> Me:
    return Me(username=user.username, is_admin=user.is_admin)


@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobCreated,
    summary="Start an analysis",
    responses={409: {"description": "You already have an analysis running; `job_id` points to it."}},
)
async def create_analysis(
    body: AnalysisRequest, response: Response, user: User = Depends(api_user), db: Session = Depends(get_db)
) -> JobCreated | JSONResponse:
    from chesswhisper.web import app as web_app  # the app module includes this router

    try:
        job = await web_app.start_analysis(
            db, user.id,
            source="file" if body.source == "pgn" else body.source,
            username=body.username or "",
            max_games=body.max_games,
            pgn_bytes=body.pgn.encode("utf-8") if body.pgn else None,
            player=body.player or "",
            lang=body.lang,
            llm=body.llm,
            ui_language="en",
        )
    except web_app.AnalysisAlreadyRunning as exc:
        return JSONResponse(
            {"detail": "You already have an analysis running.", "job_id": exc.job.id,
             "status_url": f"/api/v1/jobs/{exc.job.id}"},
            status_code=409,
        )
    except web_app.MissingLlmKey:
        raise HTTPException(
            status_code=400, detail=f"No {body.llm} API key saved. Add one under Settings."
        ) from None
    except web_app.AnalysisRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    status_url = f"/api/v1/jobs/{job.id}"
    response.headers["Location"] = status_url
    return JobCreated(job_id=job.id, status_url=status_url)


@router.get("/jobs/{job_id}", response_model=JobStatus, summary="Progress of an analysis")
def job_status(job_id: str, user: User = Depends(api_user)) -> JobStatus:
    job = jobs.get_job(job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found. Finished jobs are kept for 7 days.")
    return JobStatus(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        completed=job.completed,
        total=job.total,
        analysis_id=job.record_id,
        analysis_url=f"/api/v1/analyses/{job.record_id}" if job.record_id is not None else None,
        error=job.error_message,
        warnings=job.warnings,
        position=jobs.queue_position(job.id) if job.status == "queued" else None,
    )


@router.get("/analyses", response_model=list[AnalysisSummary], summary="Your analyses, newest first")
def list_analyses(user: User = Depends(api_user), db: Session = Depends(get_db)) -> list[AnalysisSummary]:
    records = (
        db.query(AnalysisRecord)
        .filter_by(user_id=user.id)
        .order_by(AnalysisRecord.created_at.desc(), AnalysisRecord.id.desc())
        .all()
    )
    return [_summary(r) for r in records]


@router.get("/analyses/{analysis_id}", response_model=AnalysisDetail, summary="One analysis in full")
def get_analysis(
    analysis_id: int,
    include_moves: bool = Query(default=False, description="Add every game's move-by-move timeline."),
    user: User = Depends(api_user),
    db: Session = Depends(get_db),
) -> AnalysisDetail:
    record = _own_record(db, user, analysis_id)
    games = json.loads(record.games_json) if record.games_json else []
    if not include_moves:
        games = [{k: v for k, v in game.items() if k != "moves"} for game in games]
    return AnalysisDetail(
        **_summary(record).model_dump(),
        report_markdown=record.report_markdown,
        critical_moves=json.loads(record.critical_moves_json) if record.critical_moves_json else [],
        games=games,
    )


@router.delete("/analyses/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an analysis")
def delete_analysis(analysis_id: int, user: User = Depends(api_user), db: Session = Depends(get_db)) -> Response:
    record = _own_record(db, user, analysis_id)
    db.delete(record)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
