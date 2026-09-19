import textwrap

from click.testing import CliRunner

from chesswhisper.cli import cli

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

    def analyse(self, board, limit):
        import chess.engine
        cp = self._scores[self._i % len(self._scores)]
        self._i += 1
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE)}


def test_analyze_produces_report_with_none_llm(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    output_path = tmp_path / "report.md"

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda: FakeEngine())

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

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda: FakeEngine())

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


def test_analyze_unknown_player_fails_fast(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    output_path = tmp_path / "report.md"

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda: FakeEngine())

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
