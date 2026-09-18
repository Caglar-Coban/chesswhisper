import textwrap
from chesswhisper.pgn_loader import load_pgn

VALID_SINGLE = textwrap.dedent("""
    [Event "Test"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
""").strip() + "\n"

VALID_MULTI = VALID_SINGLE + "\n" + textwrap.dedent("""
    [Event "Test2"]
    [White "Bob"]
    [Black "Alice"]
    [Result "0-1"]

    1. d4 d5 2. c4 e6 0-1
""").strip() + "\n"

MALFORMED_THEN_VALID = textwrap.dedent("""
    [Event "Broken"]
    [White "Alice"]
    [Black "Bob"]
    [Result "*"]

    1. e4 e5 2. Nf3 Nc6 3. Qh5$$$ garbage not a move *

    [Event "Ok"]
    [White "Bob"]
    [Black "Alice"]
    [Result "1/2-1/2"]

    1. e4 e5 1/2-1/2
""").strip() + "\n"


def test_loads_single_game(tmp_path):
    p = tmp_path / "one.pgn"
    p.write_text(VALID_SINGLE, encoding="utf-8")

    result = load_pgn(str(p))

    assert len(result.games) == 1
    assert result.warnings == []
    assert result.games[0].index == 0
    assert result.games[0].headers["White"] == "Alice"
    assert result.games[0].headers["Result"] == "1-0"


def test_loads_multiple_games(tmp_path):
    p = tmp_path / "multi.pgn"
    p.write_text(VALID_MULTI, encoding="utf-8")

    result = load_pgn(str(p))

    assert len(result.games) == 2
    assert result.games[0].headers["White"] == "Alice"
    assert result.games[1].headers["White"] == "Bob"


def test_skips_malformed_game_and_warns(tmp_path):
    p = tmp_path / "bad.pgn"
    p.write_text(MALFORMED_THEN_VALID, encoding="utf-8")

    result = load_pgn(str(p))

    assert len(result.games) == 1
    assert result.games[0].headers["Event"] == "Ok"
    assert len(result.warnings) == 1
    assert "0" in result.warnings[0]


def test_missing_file_raises(tmp_path):
    missing = tmp_path / "nope.pgn"
    try:
        load_pgn(str(missing))
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass
