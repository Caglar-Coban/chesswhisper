from chesswhisper.openings import UNKNOWN_OPENING, load_eco_table, match_opening


def test_load_eco_table_has_entries():
    table = load_eco_table()
    assert len(table) > 20
    assert any(e.eco == "C60" for e in table)


def test_match_exact_short_sequence():
    table = load_eco_table()
    result = match_opening(["e4", "c5"], table)
    assert result.eco == "B20"


def test_match_prefers_longest_match():
    table = load_eco_table()
    # Ruy Lopez Berlin should win over plain Ruy Lopez (C60) since it's a longer match
    result = match_opening(["e4", "e5", "Nf3", "Nc6", "Bb5", "Nf6"], table)
    assert result.eco == "C65"


def test_match_bare_e4_has_a_catch_all_entry():
    table = load_eco_table()
    result = match_opening(["e4"], table)
    assert result.eco != "A00"  # a real entry, not the UNKNOWN_OPENING fallback
    assert result.moves == ("e4",)


def test_match_falls_back_to_unknown():
    table = load_eco_table()
    assert match_opening([], table) is UNKNOWN_OPENING
    assert match_opening(["e4"], [e for e in table if e.moves[0] != "e4"]) is UNKNOWN_OPENING


def test_the_bundled_table_is_deep_and_every_line_is_legal():
    import chess

    table = load_eco_table()
    assert len(table) > 3000
    assert max(len(e.moves) for e in table) >= 20
    for entry in table:
        board = chess.Board()
        for san in entry.moves:
            board.push_san(san)  # raises on an illegal or ambiguous move
