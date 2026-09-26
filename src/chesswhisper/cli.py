import os
import sys
import tempfile
from collections.abc import Callable

import click
from dotenv import find_dotenv, load_dotenv

from chesswhisper.engine_analyzer import analyze_game
from chesswhisper.engine_provision import StockfishProvisionError, get_stockfish_path
from chesswhisper.eval_cache import EvalCache, cache_key
from chesswhisper.game_sources import DEFAULT_MAX_GAMES, MAX_GAMES_LIMIT, GameFetchError, fetch_pgn
from chesswhisper.llm.base import LLMProviderError
from chesswhisper.llm.none_provider import NoneProvider
from chesswhisper.openings import load_eco_table
from chesswhisper.pgn_loader import load_pgn
from chesswhisper.report_writer import write_report
from chesswhisper.stats_engine import build_aggregate_report, build_game_report, detect_player

# Stockfish defaults to one thread and a 16 MB hash table; more of both
# searches several times as many positions in the same think time.
ENGINE_HASH_MB = 128
MAX_DEFAULT_THREADS = 4


def default_threads() -> int:
    """Half the CPU cores, capped so a busy machine keeps some headroom."""
    return max(1, min(MAX_DEFAULT_THREADS, (os.cpu_count() or 1) // 2))


def _open_engine(engine_path=None, threads=None):
    import chess.engine

    from chesswhisper.engine_provision import resolve_installed_stockfish

    path = resolve_installed_stockfish(engine_path) or get_stockfish_path()
    try:
        engine = chess.engine.SimpleEngine.popen_uci(str(path))
    except OSError as exc:
        raise StockfishProvisionError(f"Failed to start Stockfish at {path}: {exc}") from exc
    engine.configure({"Threads": threads or default_threads(), "Hash": ENGINE_HASH_MB})
    return engine


def _build_llm_provider(name: str, api_key: str | None = None):
    if name == "none":
        return NoneProvider()
    if name == "claude":
        from chesswhisper.llm.claude_provider import ClaudeProvider
        return ClaudeProvider(api_key=api_key)
    if name == "openai":
        from chesswhisper.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key)
    raise ValueError(f"Unknown LLM provider: {name}")


def _fetch_to_tempfile(platform, username, max_games):
    """Fetches games and writes them to a temporary .pgn file.

    Returns (temp_path, player, warnings). Raises GameFetchError. The caller
    owns the returned file and must delete it.
    """
    fetched = fetch_pgn(platform, username, max_games)
    fd, temp_path = tempfile.mkstemp(suffix=".pgn")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(fetched.pgn_text.encode("utf-8"))
    except OSError:
        os.remove(temp_path)
        raise
    return temp_path, fetched.player, fetched.warnings


def _spelled_player(games, player: str) -> str:
    """*player* as the PGN headers spell it, so "alice" finds a game played by "Alice".

    An exact match wins; otherwise the first case-insensitive match; otherwise
    the name is returned trimmed and unchanged, and the caller reports that no
    game features it.
    """
    player = player.strip()
    names = [lg.headers.get(side, "") for lg in games for side in ("White", "Black")]
    if player in names:
        return player
    wanted = player.casefold()
    return next((name for name in names if name.casefold() == wanted), player)


def run_analysis(
    pgn_path,
    player,
    lang,
    llm_name,
    movetime=300,
    depth=None,
    engine_path=None,
    llm_api_key=None,
    on_progress: Callable[[int, int], None] | None = None,
    threads: int | None = None,
    use_cache: bool = True,
):
    """Runs the full analysis pipeline and returns (aggregate_report, commentary, warnings).

    Writes no output file. Raises FileNotFoundError/OSError, ValueError, or
    StockfishProvisionError on unrecoverable input/engine problems -- callers
    (the CLI, the web UI) decide how to present these. LLM failures are caught
    internally and folded into the returned `warnings` list with an empty
    `commentary` instead of raising.

    llm_api_key, if given, is passed straight to the chosen LLM provider
    instead of letting it fall back to reading from the environment --
    used by the web UI to supply a specific logged-in user's own key.

    on_progress, if given, is called once per game in the PGN -- whether
    that game is analyzed or skipped for not featuring the tracked player --
    as on_progress(games_processed, total_games), with total_games fixed
    before the loop starts. The CLI does not pass this; the web UI uses it
    to report progress on a background job.

    With use_cache, each game's evaluations are looked up in (and saved to)
    the on-disk EvalCache, and Stockfish is only started for games it hasn't
    seen with the same search limit.
    """
    warnings = []

    load_result = load_pgn(pgn_path)
    warnings.extend(load_result.warnings)

    if not load_result.games:
        raise ValueError("no valid games found in PGN file")

    if player is None:
        player = detect_player(load_result.games)
    else:
        player = _spelled_player(load_result.games, player)

    eco_table = load_eco_table()

    cache = EvalCache() if use_cache else None
    engine = None

    total_games = len(load_result.games)
    skipped_for_player = 0
    try:
        game_reports = []
        for processed, loaded in enumerate(load_result.games, start=1):
            white = loaded.headers.get("White", "")
            black = loaded.headers.get("Black", "")
            if player not in (white, black):
                skipped_for_player += 1
                if on_progress is not None:
                    on_progress(processed, total_games)
                continue
            key = cache_key(loaded.game, movetime, depth) if cache is not None else None
            evals = cache.get(key) if cache is not None and key is not None else None
            if evals is None:
                if engine is None:
                    engine = _open_engine(engine_path=engine_path, threads=threads)
                evals = analyze_game(loaded.game, engine, movetime_ms=movetime, depth=depth)
                if cache is not None and key is not None:
                    cache.put(key, evals)
            game_reports.append(build_game_report(loaded, evals, player, eco_table))
            if on_progress is not None:
                on_progress(processed, total_games)
    finally:
        if engine is not None and hasattr(engine, "quit"):
            engine.quit()

    if skipped_for_player:
        warnings.append(f"skipped {skipped_for_player} game(s) not featuring player {player!r}.")

    if not game_reports:
        raise ValueError(f"no games featuring player {player!r} were found in {pgn_path}")

    aggregate = build_aggregate_report(player, game_reports)

    commentary = ""
    if llm_name != "none":
        try:
            provider = _build_llm_provider(llm_name, api_key=llm_api_key)
            commentary = provider.generate_commentary(aggregate, lang)
        except LLMProviderError as exc:
            warnings.append(f"LLM commentary unavailable ({exc}); writing stats-only report.")

    return aggregate, commentary, warnings


@click.group()
@click.version_option(package_name="chesswhisper")
def cli():
    load_dotenv(find_dotenv(usecwd=True))


@cli.command()
@click.argument("pgn_path", required=False, type=click.Path(exists=False))
@click.option("--lichess", "lichess_user", default=None, help="Fetch this Lichess user's recent games instead of reading a PGN file.")
@click.option("--chesscom", "chesscom_user", default=None, help="Fetch this chess.com user's recent games instead of reading a PGN file.")
@click.option(
    "--max", "max_games", default=None, type=int,
    help=f"Number of recent games to fetch, 1-{MAX_GAMES_LIMIT} (default {DEFAULT_MAX_GAMES}). "
    "Only with --lichess/--chesscom.",
)
@click.option("--player", default=None, help="Player name to analyze (matches PGN White/Black headers).")
@click.option("--lang", type=click.Choice(["tr", "en"]), default="tr")
@click.option("--llm", "llm_name", type=click.Choice(["claude", "openai", "none"]), default="none")
@click.option("-o", "--output", "output_path", default="report.md", type=click.Path())
@click.option(
    "--movetime", default=300, type=click.IntRange(min=1), help="Engine think time per position, in milliseconds."
)
@click.option("--depth", default=None, type=click.IntRange(min=1), help="Fixed search depth (overrides --movetime).")
@click.option("--engine-path", default=None, help="Path to an existing Stockfish binary (skips auto-download).")
@click.option(
    "--threads", default=None, type=click.IntRange(min=1, max=256),
    help=f"Stockfish search threads (default: half your CPU cores, at most {MAX_DEFAULT_THREADS}).",
)
@click.option("--no-cache", is_flag=True, help="Re-analyze every game instead of reusing saved evaluations.")
def analyze(
    pgn_path, lichess_user, chesscom_user, max_games, player, lang, llm_name, output_path, movetime, depth,
    engine_path, threads, no_cache,
):
    sources_given = [s for s in (pgn_path, lichess_user, chesscom_user) if s is not None]
    if len(sources_given) != 1:
        click.echo("Error: provide exactly one of PGN_PATH, --lichess USER, or --chesscom USER.", err=True)
        sys.exit(1)
    fetching = pgn_path is None
    if max_games is not None and not fetching:
        click.echo("Error: --max only applies with --lichess or --chesscom.", err=True)
        sys.exit(1)

    if pgn_path is not None and os.path.abspath(output_path) == os.path.abspath(pgn_path):
        click.echo(
            f"Error: output path {output_path} is the same as the input PGN file; "
            "refusing to overwrite it. Use -o to choose a different output path.",
            err=True,
        )
        sys.exit(1)

    existed_before = os.path.exists(output_path)
    try:
        with open(output_path, "a", encoding="utf-8"):
            pass
    except OSError as exc:
        click.echo(f"Error: cannot write to output path {output_path}: {exc}", err=True)
        sys.exit(1)
    if not existed_before:
        try:
            os.remove(output_path)
        except OSError:
            pass

    temp_path = None
    try:
        analysis_path = pgn_path
        if fetching:
            platform, username = (
                ("lichess", lichess_user.strip()) if lichess_user is not None else ("chesscom", chesscom_user.strip())
            )
            count = DEFAULT_MAX_GAMES if max_games is None else max_games
            try:
                temp_path, fetched_player, fetch_warnings = _fetch_to_tempfile(platform, username, count)
            except (GameFetchError, OSError) as exc:
                click.echo(f"Error: {exc}", err=True)
                sys.exit(1)
            for warning in fetch_warnings:
                click.echo(f"Warning: {warning}", err=True)
            analysis_path = temp_path
            player = player or fetched_player

        try:
            aggregate, commentary, warnings = run_analysis(
                analysis_path, player, lang, llm_name, movetime=movetime, depth=depth, engine_path=engine_path,
                threads=threads, use_cache=not no_cache,
            )
        except (OSError, ValueError, StockfishProvisionError) as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)

    try:
        write_report(aggregate, commentary, lang, output_path)
    except OSError as exc:
        click.echo(f"Error: failed to write report to {output_path}: {exc}", err=True)
        sys.exit(1)
    click.echo(f"Report written to {output_path}")


@cli.command()
@click.option("--host", default="127.0.0.1", help="Interface to bind the web server to.")
@click.option("--port", default=8000, type=int, help="Port to bind the web server to.")
def serve(host, port):
    try:
        import uvicorn

        from chesswhisper.web.db import init_db
    except ImportError:
        click.echo(
            'Error: the web UI requires extra dependencies. Install them with: pip install -e ".[web]"',
            err=True,
        )
        sys.exit(1)

    # Migrate once, up front: a schema problem stops startup with a clear
    # traceback instead of surfacing as a 500 on the first request.
    init_db()
    click.echo(f"chesswhisper web UI running at http://{host}:{port}")
    uvicorn.run("chesswhisper.web.app:app", host=host, port=port)


@cli.command("create-admin")
@click.option("--username", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
def create_admin(username, password):
    try:
        from chesswhisper.web.db import get_db, init_db
        from chesswhisper.web.models import User
        from chesswhisper.web.security import MIN_PASSWORD_LENGTH, hash_password
    except ImportError:
        click.echo(
            'Error: the web UI requires extra dependencies. Install them with: pip install -e ".[web]"',
            err=True,
        )
        sys.exit(1)

    username = username.strip()
    if not username or any(ch.isspace() for ch in username):
        click.echo("Error: the username must not be blank or contain spaces.", err=True)
        sys.exit(1)
    if len(password) < MIN_PASSWORD_LENGTH:
        click.echo(f"Error: the password must be at least {MIN_PASSWORD_LENGTH} characters.", err=True)
        sys.exit(1)

    init_db()
    db = next(get_db())
    if db.query(User).filter_by(username=username).first() is not None:
        click.echo(f"Error: a user named {username!r} already exists.", err=True)
        sys.exit(1)

    db.add(User(username=username, password_hash=hash_password(password), is_admin=True))
    db.commit()
    click.echo(f"Admin user {username!r} created.")
