import os
import sys

import click

from chesswhisper.engine_analyzer import analyze_game
from chesswhisper.engine_provision import get_stockfish_path, StockfishProvisionError
from chesswhisper.llm.base import LLMProviderError
from chesswhisper.llm.none_provider import NoneProvider
from chesswhisper.openings import load_eco_table
from chesswhisper.pgn_loader import load_pgn
from chesswhisper.report_writer import write_report
from chesswhisper.stats_engine import build_aggregate_report, build_game_report, detect_player


def _open_engine():
    import chess.engine

    path = get_stockfish_path()
    return chess.engine.SimpleEngine.popen_uci(str(path))


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
    pass


@cli.command()
@click.argument("pgn_path", type=click.Path(exists=False))
@click.option("--player", default=None, help="Player name to analyze (matches PGN White/Black headers).")
@click.option("--lang", type=click.Choice(["tr", "en"]), default="tr")
@click.option("--llm", "llm_name", type=click.Choice(["claude", "openai", "none"]), default="none")
@click.option("-o", "--output", "output_path", default="report.md", type=click.Path())
@click.option("--movetime", default=300, help="Engine think time per position, in milliseconds.")
@click.option("--depth", default=None, type=int, help="Fixed search depth (overrides --movetime).")
def analyze(pgn_path, player, lang, llm_name, output_path, movetime, depth):
    output_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    if not os.access(output_dir, os.W_OK):
        click.echo(f"Error: cannot write to output directory {output_dir}", err=True)
        sys.exit(1)

    try:
        load_result = load_pgn(pgn_path)
    except FileNotFoundError as exc:
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
        engine = _open_engine()
    except StockfishProvisionError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    try:
        game_reports = []
        for loaded in load_result.games:
            evals = analyze_game(loaded.game, engine, movetime_ms=movetime, depth=depth)
            game_reports.append(build_game_report(loaded, evals, player, eco_table))
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    finally:
        if hasattr(engine, "quit"):
            engine.quit()

    aggregate = build_aggregate_report(player, game_reports)

    commentary = ""
    if llm_name != "none":
        try:
            provider = _build_llm_provider(llm_name)
            commentary = provider.generate_commentary(aggregate, lang)
        except LLMProviderError as exc:
            click.echo(f"Warning: LLM commentary unavailable ({exc}); writing stats-only report.", err=True)

    write_report(aggregate, commentary, lang, output_path)
    click.echo(f"Report written to {output_path}")
