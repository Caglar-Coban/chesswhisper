import textwrap

import pytest
from click.testing import CliRunner

from chesswhisper.cli import _open_engine, cli
from chesswhisper.engine_provision import StockfishProvisionError

SAMPLE_PGN = textwrap.dedent("""
    [Event "Test"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
""").strip() + "\n"


class FakeEngine:
    def __init__(self):
        self._scores = [0, 20, 10, 15]
        self._i = 0

    def analyse(self, board, limit, **kwargs):
        import chess.engine
        cp = self._scores[self._i % len(self._scores)]
        self._i += 1
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE)}


def test_analyze_produces_report_with_none_llm(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    output_path = tmp_path / "report.md"

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "analyze", str(pgn_path),
            "--player", "Alice",
            "--llm", "none",
            "--lang", "en",
            "-o", str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "chesswhisper Analysis Report" in content
    assert "Alice" in content


def test_analyze_pgn_permission_error_fails_cleanly(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    def raise_permission_error(path):
        raise PermissionError(f"cannot read {path}")

    monkeypatch.setattr("chesswhisper.cli.load_pgn", raise_permission_error)

    runner = CliRunner()
    result = runner.invoke(cli, ["analyze", str(pgn_path), "--llm", "none"])

    assert result.exit_code != 0
    assert "Error:" in result.output


def test_analyze_missing_pgn_file_fails_fast(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["analyze", str(tmp_path / "missing.pgn"), "--llm", "none"],
    )

    assert result.exit_code != 0


def test_analyze_llm_failure_falls_back_to_stats_only(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    output_path = tmp_path / "report.md"

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    def boom(*args, **kwargs):
        from chesswhisper.llm.base import LLMProviderError
        raise LLMProviderError("no key")

    monkeypatch.setattr("chesswhisper.cli._build_llm_provider", lambda name: type(
        "P", (), {"generate_commentary": staticmethod(boom)}
    )())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["analyze", str(pgn_path), "--player", "Alice", "--llm", "claude", "-o", str(output_path)],
    )

    assert result.exit_code == 0, result.output
    assert "no key" in result.output or "warning" in result.output.lower()
    assert output_path.exists()


def test_analyze_unwritable_output_path_fails_fast(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    # Output path is an existing directory, so opening it for writing must fail.
    output_path = tmp_path / "report_dir"
    output_path.mkdir()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("engine should not be opened when output path is unwritable")

    monkeypatch.setattr("chesswhisper.cli._open_engine", fail_if_called)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "analyze", str(pgn_path),
            "--player", "Alice",
            "--llm", "none",
            "-o", str(output_path),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.output


def test_open_engine_wraps_bad_explicit_path_as_provision_error(tmp_path):
    bogus_path = tmp_path / "not-a-real-stockfish-binary"

    with pytest.raises(StockfishProvisionError):
        _open_engine(engine_path=str(bogus_path))


def test_analyze_refuses_to_overwrite_input_pgn(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    def fail_if_called(**kwargs):
        raise AssertionError("engine should not be opened when output path == input PGN path")

    monkeypatch.setattr("chesswhisper.cli._open_engine", fail_if_called)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["analyze", str(pgn_path), "--player", "Alice", "--llm", "none", "-o", str(pgn_path)],
    )

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert pgn_path.read_text(encoding="utf-8") == SAMPLE_PGN


def test_analyze_unknown_player_fails_fast(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    output_path = tmp_path / "report.md"

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "analyze", str(pgn_path),
            "--player", "Nobody",
            "--llm", "none",
            "-o", str(output_path),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert not output_path.exists()


MULTI_GAME_PGN = SAMPLE_PGN + "\n" + textwrap.dedent("""
    [Event "Unrelated"]
    [White "Carol"]
    [Black "Dave"]
    [Result "1-0"]

    1. d4 d5 1-0
""").strip() + "\n"


def test_analyze_skips_games_not_featuring_player_without_aborting(tmp_path, monkeypatch):
    pgn_path = tmp_path / "games.pgn"
    pgn_path.write_text(MULTI_GAME_PGN, encoding="utf-8")
    output_path = tmp_path / "report.md"

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["analyze", str(pgn_path), "--player", "Alice", "--llm", "none", "-o", str(output_path)],
    )

    assert result.exit_code == 0, result.output
    assert "skipped" in result.output.lower()
    assert output_path.exists()


def test_analyze_no_games_feature_player_fails_cleanly(tmp_path, monkeypatch):
    pgn_path = tmp_path / "games.pgn"
    pgn_path.write_text(MULTI_GAME_PGN, encoding="utf-8")
    output_path = tmp_path / "report.md"

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["analyze", str(pgn_path), "--player", "Zara", "--llm", "none", "-o", str(output_path)],
    )

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert not output_path.exists()
