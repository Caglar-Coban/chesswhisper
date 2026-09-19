import os
import tempfile

import bleach
import markdown as markdown_lib
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from chesswhisper.cli import run_analysis
from chesswhisper.engine_provision import StockfishProvisionError
from chesswhisper.report_writer import render_markdown

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


def _render_error(request: Request, message: str, status_code: int) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "error.html", {"message": message}, status_code=status_code
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> HTMLResponse:
    return _render_error(request, "Beklenmeyen bir hata oluştu.", 500)


def _report_to_safe_html(report_markdown: str) -> str:
    raw_html = markdown_lib.markdown(report_markdown, extensions=["tables"])
    return bleach.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(
    request: Request,
    pgn: UploadFile | None = File(default=None),  # noqa: B008 - FastAPI's own idiom
    player: str = Form(default=""),
    lang: str = Form(default="tr"),
    llm: str = Form(default="none"),
) -> HTMLResponse:
    if pgn is None or not pgn.filename:
        return _render_error(request, "Lütfen analiz edilecek bir PGN dosyası seçin.", 400)

    contents = await pgn.read()

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pgn", delete=False) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        try:
            aggregate, commentary, warnings = run_analysis(
                tmp_path, player or None, lang, llm
            )
        except (OSError, ValueError) as exc:
            return _render_error(request, str(exc), 400)
        except StockfishProvisionError as exc:
            return _render_error(request, f"Motor kurulum hatası: {exc}", 500)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    report_markdown = render_markdown(aggregate, commentary, lang)
    report_html = _report_to_safe_html(report_markdown)

    return templates.TemplateResponse(
        request, "result.html", {"report_html": report_html, "warnings": warnings}
    )
