import shutil
import textwrap

import pytest
from click.testing import CliRunner

from chesswhisper.cli import cli

SAMPLE_PGN = textwrap.dedent("""
    [Event "Smoke"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
""").strip() + "\n"


def _system_stockfish_available() -> bool:
    return shutil.which("stockfish") is not None


@pytest.mark.skipif(not _system_stockfish_available(), reason="no system Stockfish binary on PATH")
def test_end_to_end_with_real_stockfish(tmp_path, monkeypatch):
    import chess.engine

    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    output_path = tmp_path / "report.md"

    monkeypatch.setattr(
        "chesswhisper.cli._open_engine",
        lambda: chess.engine.SimpleEngine.popen_uci(shutil.which("stockfish")),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["analyze", str(pgn_path), "--player", "Alice", "--llm", "none",
         "--movetime", "50", "-o", str(output_path)],
    )

    assert result.exit_code == 0, result.output
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").strip() != ""
