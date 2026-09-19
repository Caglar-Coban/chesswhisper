import os
import sys

import click
from dotenv import find_dotenv, load_dotenv

from chesswhisper.engine_analyzer import analyze_game
from chesswhisper.engine_provision import StockfishProvisionError, get_stockfish_path
from chesswhisper.llm.base import LLMProviderError
from chesswhisper.llm.none_provider import NoneProvider
from chesswhisper.openings import load_eco_table
from chesswhisper.pgn_loader import load_pgn
from chesswhisper.report_writer import write_report
from chesswhisper.stats_engine import build_aggregate_report, build_game_report, detect_player


def _open_engine(engine_path=None):
    import chess.engine

    from chesswhisper.engine_provision import resolve_installed_stockfish

    path = resolve_installed_stockfish(engine_path) or get_stockfish_path()
    try:
        return chess.engine.SimpleEngine.popen_uci(str(path))
    except OSError as exc:
        raise StockfishProvisionError(f"Failed to start Stockfish at {path}: {exc}") from exc


def _build_llm_provider(name: str):
    if name == "none":
        return NoneProvider()
    if name == "claude":
        from chesswhisper.llm.claude_provider import ClaudeProvider
        return ClaudeProvider()
    if name == "openai":
        from chesswhisper.llm.openai_provider import OpenAIProvider
        return OpenAIProvider()
    raise ValueError(f"Unknown LLM provider: {name}")


@click.group()
def cli():
    load_dotenv(find_dotenv(usecwd=True))


@cli.command()
@click.argument("pgn_path", type=click.Path(exists=False))
@click.option("--player", default=None, help="Player name to analyze (matches PGN White/Black headers).")
@click.option("--lang", type=click.Choice(["tr", "en"]), default="tr")
@click.option("--llm", "llm_name", type=click.Choice(["claude", "openai", "none"]), default="none")
@click.option("-o", "--output", "output_path", default="report.md", type=click.Path())
@click.option("--movetime", default=300, help="Engine think time per position, in milliseconds.")
@click.option("--depth", default=None, type=int, help="Fixed search depth (overrides --movetime).")
@click.option("--engine-path", default=None, help="Path to an existing Stockfish binary (skips auto-download).")
def analyze(pgn_path, player, lang, llm_name, output_path, movetime, depth, engine_path):
    if os.path.abspath(output_path) == os.path.abspath(pgn_path):
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

    try:
        load_result = load_pgn(pgn_path)
    except OSError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    for warning in load_result.warnings:
        click.echo(f"Warning: {warning}", err=True)

    if not load_result.games:
        click.echo("Error: no valid games found in PGN file", err=True)
        sys.exit(1)

    if player is None:
        try:
            player = detect_player(load_result.games)
        except ValueError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)

    eco_table = load_eco_table()

    try:
        engine = _open_engine(engine_path=engine_path)
    except StockfishProvisionError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    skipped_for_player = 0
    try:
        game_reports = []
        for loaded in load_result.games:
            white = loaded.headers.get("White", "")
            black = loaded.headers.get("Black", "")
            if player not in (white, black):
                skipped_for_player += 1
                continue
            evals = analyze_game(loaded.game, engine, movetime_ms=movetime, depth=depth)
            game_reports.append(build_game_report(loaded, evals, player, eco_table))
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    finally:
        if hasattr(engine, "quit"):
            engine.quit()

    if skipped_for_player:
        click.echo(
            f"Warning: skipped {skipped_for_player} game(s) not featuring player {player!r}.",
            err=True,
        )

    if not game_reports:
        click.echo(f"Error: no games featuring player {player!r} were found in {pgn_path}", err=True)
        sys.exit(1)

    aggregate = build_aggregate_report(player, game_reports)

    commentary = ""
    if llm_name != "none":
        try:
            provider = _build_llm_provider(llm_name)
            commentary = provider.generate_commentary(aggregate, lang)
        except LLMProviderError as exc:
            click.echo(f"Warning: LLM commentary unavailable ({exc}); writing stats-only report.", err=True)

    try:
        write_report(aggregate, commentary, lang, output_path)
    except OSError as exc:
        click.echo(f"Error: failed to write report to {output_path}: {exc}", err=True)
        sys.exit(1)
    click.echo(f"Report written to {output_path}")
