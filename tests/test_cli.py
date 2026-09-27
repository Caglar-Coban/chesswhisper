import os
import textwrap

import pytest
from click.testing import CliRunner

from chesswhisper.cli import ENGINE_HASH_MB, _open_engine, cli, default_threads, run_analysis
from chesswhisper.engine_provision import StockfishProvisionError
from chesswhisper.game_sources import DEFAULT_MAX_GAMES, MAX_GAMES_LIMIT, FetchResult, GameFetchError

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


def test_cli_loads_dotenv_from_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHESSWHISPER_TEST_VAR", raising=False)
    (tmp_path / ".env").write_text("CHESSWHISPER_TEST_VAR=hello\n", encoding="utf-8")

    runner = CliRunner()
    # Any subcommand invocation runs the group callback first, which loads .env;
    # the analyze call itself is expected to fail fast (missing PGN) -- that's fine.
    runner.invoke(cli, ["analyze", str(tmp_path / "nope.pgn"), "--llm", "none"])

    assert os.environ.get("CHESSWHISPER_TEST_VAR") == "hello"


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

    monkeypatch.setattr("chesswhisper.cli._build_llm_provider", lambda name, api_key=None: type(
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


def test_run_analysis_calls_on_progress_after_each_game(tmp_path, monkeypatch):
    pgn_path = tmp_path / "games.pgn"
    pgn_path.write_text(MULTI_GAME_PGN, encoding="utf-8")

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    progress_calls = []
    run_analysis(
        str(pgn_path), "Alice", "en", "none",
        on_progress=lambda completed, total: progress_calls.append((completed, total)),
    )

    # MULTI_GAME_PGN has two games (Alice-Bob, Carol-Dave); with player="Alice"
    # the first is analyzed and the second is skipped, but on_progress fires
    # for both, in order, each reporting the fixed total of 2.
    assert progress_calls == [(1, 2), (2, 2)]


def test_run_analysis_without_on_progress_is_unaffected(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    aggregate, commentary, warnings = run_analysis(str(pgn_path), "Alice", "en", "none")

    assert aggregate.player == "Alice"


# --- run_analysis: the pure function shared by the CLI and the (future) web UI ---


def test_run_analysis_returns_report_and_empty_commentary_with_none_llm(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    aggregate, commentary, warnings = run_analysis(str(pgn_path), "Alice", "en", "none")

    assert aggregate.player == "Alice"
    assert commentary == ""
    assert warnings == []


def test_run_analysis_raises_file_not_found_for_missing_pgn(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_analysis(str(tmp_path / "missing.pgn"), "Alice", "en", "none")


def test_run_analysis_raises_value_error_for_unknown_player(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    with pytest.raises(ValueError):
        run_analysis(str(pgn_path), "Nobody", "en", "none")


def test_run_analysis_propagates_stockfish_provision_error(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    def fail_provisioning(**kwargs):
        raise StockfishProvisionError("no engine available")

    monkeypatch.setattr("chesswhisper.cli._open_engine", fail_provisioning)

    with pytest.raises(StockfishProvisionError):
        run_analysis(str(pgn_path), "Alice", "en", "none")


def test_run_analysis_collects_skip_and_llm_warnings_instead_of_printing(tmp_path, monkeypatch):
    pgn_path = tmp_path / "games.pgn"
    pgn_path.write_text(MULTI_GAME_PGN, encoding="utf-8")

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    def boom(*args, **kwargs):
        from chesswhisper.llm.base import LLMProviderError
        raise LLMProviderError("no key")

    monkeypatch.setattr("chesswhisper.cli._build_llm_provider", lambda name, api_key=None: type(
        "P", (), {"generate_commentary": staticmethod(boom)}
    )())

    aggregate, commentary, warnings = run_analysis(str(pgn_path), "Alice", "en", "claude")

    assert aggregate.player == "Alice"
    assert commentary == ""
    assert any("skipped" in w.lower() for w in warnings)
    assert any("no key" in w for w in warnings)


def test_run_analysis_passes_explicit_api_key_to_provider(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    captured = {}

    class FakeProvider:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key

        def generate_commentary(self, report, lang):
            return "ok"

    monkeypatch.setattr("chesswhisper.llm.claude_provider.ClaudeProvider", FakeProvider)

    run_analysis(str(pgn_path), "Alice", "en", "claude", llm_api_key="sk-explicit-key")

    assert captured["api_key"] == "sk-explicit-key"


def test_create_admin_creates_admin_user(tmp_path, monkeypatch):
    from chesswhisper.web import db as web_db
    db_path = tmp_path / "create_admin_test.db"
    monkeypatch.setattr(web_db, "_default_db_path", lambda: db_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["create-admin", "--username", "root", "--password", "correct-horse-battery"])

    assert result.exit_code == 0, result.output

    web_db.init_db(db_path)
    from chesswhisper.web.models import User
    db_gen = web_db.get_db()
    db_session = next(db_gen)
    user = db_session.query(User).filter_by(username="root").one()
    assert user.is_admin is True
    from chesswhisper.web.security import verify_password
    assert verify_password("correct-horse-battery", user.password_hash)


def test_create_admin_rejects_duplicate_username(tmp_path, monkeypatch):
    from chesswhisper.web import db as web_db
    db_path = tmp_path / "create_admin_dup_test.db"
    monkeypatch.setattr(web_db, "_default_db_path", lambda: db_path)

    runner = CliRunner()
    runner.invoke(cli, ["create-admin", "--username", "root", "--password", "correct-horse-battery"])
    result = runner.invoke(cli, ["create-admin", "--username", "root", "--password", "another-password"])

    assert result.exit_code != 0
    assert "already" in result.output.lower() or "zaten" in result.output.lower()


# --- fetching games from Lichess / chess.com ---


def test_analyze_requires_exactly_one_game_source(tmp_path):
    runner = CliRunner()

    none_given = runner.invoke(cli, ["analyze"])
    two_given = runner.invoke(cli, ["analyze", str(tmp_path / "g.pgn"), "--lichess", "alice"])
    both_platforms = runner.invoke(cli, ["analyze", "--lichess", "alice", "--chesscom", "alice"])

    for result in (none_given, two_given, both_platforms):
        assert result.exit_code == 1
        assert "exactly one of" in result.output


def test_analyze_max_is_rejected_with_a_pgn_file(tmp_path):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    result = CliRunner().invoke(cli, ["analyze", str(pgn_path), "--max", "5"])

    assert result.exit_code == 1
    assert "--max" in result.output


def test_analyze_max_help_text_matches_the_shared_constants():
    result = CliRunner().invoke(cli, ["analyze", "--help"])
    # Click wraps help text across lines, so compare against the collapsed form
    # rather than depending on exact line breaks.
    collapsed = " ".join(result.output.split())

    assert result.exit_code == 0, result.output
    assert f"1-{MAX_GAMES_LIMIT}" in collapsed
    assert f"default {DEFAULT_MAX_GAMES}" in collapsed


def test_analyze_lichess_fetches_games_and_uses_the_fetched_player(tmp_path, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    calls = {}

    def fake_fetch(platform, username, max_games):
        calls["args"] = (platform, username, max_games)
        return FetchResult(pgn_text=SAMPLE_PGN, player="Alice", warnings=["only 1 game(s) found (requested 3)."])

    monkeypatch.setattr("chesswhisper.cli.fetch_pgn", fake_fetch)
    output_path = tmp_path / "report.md"

    result = CliRunner().invoke(
        cli,
        ["analyze", "--lichess", "alice", "--max", "3", "--lang", "en", "-o", str(output_path)],
    )

    assert result.exit_code == 0, result.output
    assert calls["args"] == ("lichess", "alice", 3)
    assert "Warning: only 1 game(s) found" in result.output
    assert "Alice" in output_path.read_text(encoding="utf-8")


def test_analyze_strips_whitespace_from_a_fetched_username(tmp_path, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    calls = {}

    def fake_fetch(platform, username, max_games):
        calls["args"] = (platform, username, max_games)
        return FetchResult(pgn_text=SAMPLE_PGN, player="Alice")

    monkeypatch.setattr("chesswhisper.cli.fetch_pgn", fake_fetch)

    result = CliRunner().invoke(
        cli, ["analyze", "--lichess", "  alice  ", "-o", str(tmp_path / "r.md")]
    )

    assert result.exit_code == 0, result.output
    assert calls["args"] == ("lichess", "alice", DEFAULT_MAX_GAMES)


def test_analyze_chesscom_defaults_to_ten_games(tmp_path, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    calls = {}

    def fake_fetch(platform, username, max_games):
        calls["args"] = (platform, username, max_games)
        return FetchResult(pgn_text=SAMPLE_PGN, player="Alice")

    monkeypatch.setattr("chesswhisper.cli.fetch_pgn", fake_fetch)

    result = CliRunner().invoke(cli, ["analyze", "--chesscom", "alice", "-o", str(tmp_path / "r.md")])

    assert result.exit_code == 0, result.output
    assert calls["args"] == ("chesscom", "alice", 10)


def test_analyze_player_option_overrides_the_fetched_player(tmp_path, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    monkeypatch.setattr(
        "chesswhisper.cli.fetch_pgn",
        lambda platform, username, max_games: FetchResult(pgn_text=SAMPLE_PGN, player="Bob"),
    )
    captured = {}
    real_run_analysis = run_analysis

    def spy_run_analysis(pgn_path, player, *args, **kwargs):
        captured["player"] = player
        return real_run_analysis(pgn_path, player, *args, **kwargs)

    monkeypatch.setattr("chesswhisper.cli.run_analysis", spy_run_analysis)

    result = CliRunner().invoke(
        cli, ["analyze", "--lichess", "bob", "--player", "Alice", "-o", str(tmp_path / "r.md")]
    )

    assert result.exit_code == 0, result.output
    assert captured["player"] == "Alice"


def test_analyze_fetch_error_exits_cleanly_without_running_the_engine(tmp_path, monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("the engine should not start when fetching fails")

    monkeypatch.setattr("chesswhisper.cli._open_engine", fail_if_called)

    def fake_fetch(platform, username, max_games):
        raise GameFetchError("Lichess user 'alice' not found.")

    monkeypatch.setattr("chesswhisper.cli.fetch_pgn", fake_fetch)

    result = CliRunner().invoke(cli, ["analyze", "--lichess", "alice", "-o", str(tmp_path / "r.md")])

    assert result.exit_code == 1
    assert "Error: Lichess user 'alice' not found." in result.output


def test_analyze_removes_the_temporary_pgn_after_a_fetched_run(tmp_path, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    monkeypatch.setattr(
        "chesswhisper.cli.fetch_pgn",
        lambda platform, username, max_games: FetchResult(pgn_text=SAMPLE_PGN, player="Alice"),
    )
    seen = {}
    real_run_analysis = run_analysis

    def spy_run_analysis(pgn_path, *args, **kwargs):
        seen["path"] = pgn_path
        seen["existed_during_run"] = os.path.exists(pgn_path)
        return real_run_analysis(pgn_path, *args, **kwargs)

    monkeypatch.setattr("chesswhisper.cli.run_analysis", spy_run_analysis)

    result = CliRunner().invoke(cli, ["analyze", "--lichess", "alice", "-o", str(tmp_path / "r.md")])

    assert result.exit_code == 0, result.output
    assert seen["existed_during_run"] is True
    assert not os.path.exists(seen["path"])


def test_serve_migrates_the_database_before_starting_the_server(mocker):
    calls = []
    mocker.patch("chesswhisper.web.db.init_db", side_effect=lambda: calls.append("init_db"))
    mocker.patch("uvicorn.run", side_effect=lambda *a, **k: calls.append("uvicorn.run"))

    result = CliRunner().invoke(cli, ["serve"])

    assert result.exit_code == 0, result.output
    assert calls == ["init_db", "uvicorn.run"]


# --- input validation and small conveniences ---


def test_run_analysis_matches_the_player_name_case_insensitively(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    aggregate, _, warnings = run_analysis(str(pgn_path), " alice ", "en", "none")

    assert aggregate.player == "Alice"
    assert len(aggregate.games) == 1
    assert warnings == []


def test_create_admin_rejects_a_short_password(tmp_path, monkeypatch):
    from chesswhisper.web import db as web_db
    monkeypatch.setattr(web_db, "_default_db_path", lambda: tmp_path / "short_pw.db")

    result = CliRunner().invoke(cli, ["create-admin", "--username", "root", "--password", "short"])

    assert result.exit_code != 0
    assert "8" in result.output


def test_create_admin_rejects_a_blank_username(tmp_path, monkeypatch):
    from chesswhisper.web import db as web_db
    monkeypatch.setattr(web_db, "_default_db_path", lambda: tmp_path / "blank_user.db")

    result = CliRunner().invoke(cli, ["create-admin", "--username", "  ", "--password", "correct-horse"])

    assert result.exit_code != 0


@pytest.mark.parametrize("option", [["--movetime", "0"], ["--depth", "0"], ["--movetime", "-5"]])
def test_analyze_rejects_non_positive_engine_limits(tmp_path, option):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    result = CliRunner().invoke(cli, ["analyze", str(pgn_path), *option])

    assert result.exit_code == 2  # click usage error


def test_version_option_prints_the_package_version():
    from importlib.metadata import version

    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert version("chesswhisper") in result.output


# --- engine configuration ---


class _RecordingEngine:
    def __init__(self):
        self.options = None

    def configure(self, options):
        self.options = options


def test_open_engine_configures_threads_and_hash(monkeypatch):
    import chess.engine

    engine = _RecordingEngine()
    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", staticmethod(lambda path: engine))
    monkeypatch.setattr("chesswhisper.engine_provision.resolve_installed_stockfish", lambda path: "stockfish")

    assert _open_engine(threads=3) is engine
    assert engine.options == {"Threads": 3, "Hash": ENGINE_HASH_MB}


def test_open_engine_defaults_to_half_the_cores_capped_at_four(monkeypatch):
    import chess.engine

    engine = _RecordingEngine()
    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", staticmethod(lambda path: engine))
    monkeypatch.setattr("chesswhisper.engine_provision.resolve_installed_stockfish", lambda path: "stockfish")
    monkeypatch.setattr("chesswhisper.cli.os.cpu_count", lambda: 32)

    _open_engine()

    assert engine.options["Threads"] == 4


def test_default_threads_is_at_least_one(monkeypatch):
    monkeypatch.setattr("chesswhisper.cli.os.cpu_count", lambda: None)
    assert default_threads() == 1


def test_analyze_passes_threads_to_the_engine(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    seen = {}

    def fake_open_engine(**kwargs):
        seen.update(kwargs)
        return FakeEngine()

    monkeypatch.setattr("chesswhisper.cli._open_engine", fake_open_engine)

    result = CliRunner().invoke(
        cli, ["analyze", str(pgn_path), "--player", "Alice", "--threads", "2", "-o", str(tmp_path / "r.md")]
    )

    assert result.exit_code == 0, result.output
    assert seen["threads"] == 2


# --- evaluation cache ---


class CountingEngine(FakeEngine):
    calls = 0

    def analyse(self, board, limit, **kwargs):
        CountingEngine.calls += 1
        return super().analyse(board, limit, **kwargs)


def test_run_analysis_reuses_cached_evaluations(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    opened = []

    def open_engine(**kwargs):
        opened.append(1)
        return CountingEngine()

    monkeypatch.setattr("chesswhisper.cli._open_engine", open_engine)
    CountingEngine.calls = 0

    first, _, _ = run_analysis(str(pgn_path), "Alice", "en", "none")
    calls_after_first = CountingEngine.calls
    second, _, _ = run_analysis(str(pgn_path), "Alice", "en", "none")

    assert calls_after_first > 0
    assert CountingEngine.calls == calls_after_first  # nothing re-analyzed
    assert len(opened) == 1  # and Stockfish wasn't even started the second time
    assert second.overall_acpl == first.overall_acpl


def test_run_analysis_can_skip_the_cache(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: CountingEngine())
    CountingEngine.calls = 0

    run_analysis(str(pgn_path), "Alice", "en", "none", use_cache=False)
    once = CountingEngine.calls
    run_analysis(str(pgn_path), "Alice", "en", "none", use_cache=False)

    assert CountingEngine.calls == 2 * once


def test_analyze_no_cache_flag(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    seen = {}

    def fake_run_analysis(*args, **kwargs):
        seen.update(kwargs)
        raise ValueError("stop here")

    monkeypatch.setattr("chesswhisper.cli.run_analysis", fake_run_analysis)

    CliRunner().invoke(cli, ["analyze", str(pgn_path), "--no-cache", "-o", str(tmp_path / "r.md")])

    assert seen["use_cache"] is False


# --- filters ---------------------------------------------------------------------

FILTER_PGN = textwrap.dedent("""
    [Event "Blitz"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]
    [TimeControl "180+2"]

    1. e4 e5 2. Nf3 Nc6 1-0

    [Event "Bullet"]
    [White "Bob"]
    [Black "Alice"]
    [Result "0-1"]
    [TimeControl "60+0"]

    1. d4 d5 2. c4 e6 0-1
""").strip() + "\n"


def _filter_pgn(tmp_path):
    path = tmp_path / "filter.pgn"
    path.write_text(FILTER_PGN, encoding="utf-8")
    return path


def test_run_analysis_keeps_only_games_inside_the_filter(tmp_path, monkeypatch):
    from chesswhisper.game_filter import GameFilter

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    game_filter = GameFilter(color="black")

    aggregate, _, warnings = run_analysis(str(_filter_pgn(tmp_path)), "Alice", "en", "none", game_filter=game_filter)

    assert [g.your_color for g in aggregate.games] == ["black"]
    assert aggregate.game_filter == game_filter
    assert "left out 1 game(s) outside the filter." in warnings


def test_run_analysis_with_an_empty_filter_is_unfiltered(tmp_path, monkeypatch):
    from chesswhisper.game_filter import GameFilter

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    aggregate, _, _ = run_analysis(str(_filter_pgn(tmp_path)), "Alice", "en", "none", game_filter=GameFilter())

    assert len(aggregate.games) == 2
    assert aggregate.game_filter is None


def test_run_analysis_fails_clearly_when_nothing_matches(tmp_path, monkeypatch):
    from chesswhisper.game_filter import GameFilter

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    with pytest.raises(ValueError, match="match the filter"):
        run_analysis(str(_filter_pgn(tmp_path)), "Alice", "en", "none",
                     game_filter=GameFilter(time_classes=frozenset({"classical"})))


def test_cli_filter_options_narrow_the_report(tmp_path, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    output = tmp_path / "report.md"

    result = CliRunner().invoke(cli, [
        "analyze", str(_filter_pgn(tmp_path)), "--player", "Alice", "--time-control", "blitz",
        "--time-control", "rapid", "--color", "white", "-o", str(output),
    ])

    assert result.exit_code == 0, result.output
    report = output.read_text(encoding="utf-8")
    assert "**Filter:** Blitz, Rapid \u00b7 as White" in report


@pytest.mark.parametrize(("args", "tool"), [(["backup"], "pg_dump"), (["restore", __file__], "pg_restore")])
def test_backup_and_restore_point_to_postgres_tools_when_the_database_is_there(monkeypatch, args, tool):
    monkeypatch.setenv("CHESSWHISPER_DATABASE_URL", "postgresql://u:p@db.example/cw")

    result = CliRunner().invoke(cli, args)

    assert result.exit_code == 1
    assert tool in result.output
    assert "api_key_encryption.key" in result.output
