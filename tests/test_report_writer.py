from chesswhisper.report_writer import render_markdown, write_report
from chesswhisper.stats_engine import (
    PHASES,
    AggregateReport,
    ErrorCounts,
    GameReport,
    OpeningStats,
    PlayerGameStats,
    TimeManagementStats,
)


def _sample_report():
    errors = {p: ErrorCounts() for p in PHASES}
    errors["middlegame"].blunder = 1
    your_stats = PlayerGameStats(acpl=42.5, accuracy=87.3, errors_by_phase=errors)

    game = GameReport(
        game_index=0, your_color="white", opponent="Bob", result="win",
        your_stats=your_stats,
        eco="C60", opening_name="Ruy Lopez",
        time_management=TimeManagementStats(available=True, low_time_error_rate=0.25, normal_time_error_rate=0.05),
        top_mistakes=[],
    )

    return AggregateReport(
        player="Alice", games=[game], overall_acpl=42.5, overall_accuracy=87.3,
        error_totals=errors,
        openings=[OpeningStats(eco="C60", name="Ruy Lopez", games=1, wins=1, losses=0, draws=0)],
        time_management=TimeManagementStats(available=False),
    )


def test_render_markdown_turkish_contains_key_sections():
    md = render_markdown(_sample_report(), commentary="Test yorumu.", lang="tr")

    assert "chesswhisper Analiz Raporu" in md
    assert "Alice" in md
    assert "Ruy Lopez" in md
    assert "42.5" in md or "42.50" in md
    assert "Test yorumu." in md


def test_render_markdown_english_uses_english_labels():
    md = render_markdown(_sample_report(), commentary="Test comment.", lang="en")

    assert "chesswhisper Analysis Report" in md
    assert "Opening Repertoire" in md


def test_write_report_creates_file(tmp_path):
    out = tmp_path / "report.md"
    write_report(_sample_report(), commentary="Hi.", lang="en", output_path=str(out))

    assert out.exists()
    assert "Hi." in out.read_text(encoding="utf-8")


def test_render_markdown_includes_per_game_errors_by_phase_and_time_management():
    md = render_markdown(_sample_report(), commentary="Test comment.", lang="en")

    # Per-game breakdown section should exist and contain both the errors-by-phase
    # table (with the game's middlegame blunder count) and the time-management rates.
    per_game_section = md[md.index("### 1."):]

    assert "**Errors by Phase:**" in per_game_section
    # the game's middlegame blunder count of 1 should appear in the per-game table
    assert "| Middlegame | 0 | 0 | 1 |" in per_game_section
    assert "**Time Management:**" in per_game_section
    assert "25.0%" in per_game_section
    assert "5.0%" in per_game_section


def test_render_markdown_translates_player_color():
    md_tr = render_markdown(_sample_report(), commentary="", lang="tr")
    assert "beyaz" in md_tr
    assert ", white)" not in md_tr

    md_en = render_markdown(_sample_report(), commentary="", lang="en")
    assert ", white)" in md_en


def test_render_markdown_handles_unknown_game_result():
    report = _sample_report()
    report.games[0].result = "unknown"
    report.openings[0].unknown = 1

    md = render_markdown(report, commentary="", lang="en")

    assert "Unknown" in md
    assert "1/0/0/1" in md  # wins/losses/draws/unknown


def test_render_markdown_handles_partial_time_management_data():
    report = _sample_report()
    # available=True but only the low-time bucket has data (normal_time_error_rate is None)
    report.time_management = TimeManagementStats(available=True, low_time_error_rate=0.5, normal_time_error_rate=None)

    md = render_markdown(report, commentary="", lang="en")

    assert "50.0%" in md
    assert "not enough data" in md


def test_every_table_renders_as_an_html_table():
    # The web UI renders reports with Python-Markdown; a table glued to the
    # line above it comes out as literal pipes inside a list item.
    import markdown

    report = _sample_report()
    report.games = report.games * 3
    html = markdown.markdown(render_markdown(report, commentary="", lang="en"), extensions=["tables"])

    # Overall errors + openings + one errors table per game.
    assert html.count("<table>") == 2 + len(report.games)
    assert "| ---" not in html


def test_opening_table_names_its_columns():
    md = render_markdown(_sample_report(), commentary="", lang="en")

    assert "| ECO | Opening | Games | W/L/D/? |" in md


def test_report_includes_breakdowns_and_patterns_when_present():
    from chesswhisper.stats_engine import PatternBucket, SegmentStats

    report = _sample_report()
    report.by_time_class = [SegmentStats("blitz", games=1, wins=1, acpl=42.5, accuracy=87.3)]
    report.by_opponent_strength = [SegmentStats("stronger", games=1, wins=1, acpl=42.5, accuracy=87.3)]
    report.move_ranges = [PatternBucket("1-10", moves=10, errors=1), PatternBucket("11-20", moves=0)]
    report.eval_states = [PatternBucket("winning", moves=4, errors=1), PatternBucket("equal", moves=6)]

    md = render_markdown(report, commentary="", lang="en")

    assert "## Performance by Time Control" in md
    assert "| Blitz | 1 | 1/0/0/0 | 42.5 | 87.3% |" in md
    assert "Stronger (+100 Elo or more)" in md
    assert "| 1-10 | 10 | 1 | 10% |" in md
    assert "| 11-20 | 0 | 0 | - |" in md
    assert "| Ahead (+2 or more) | 4 | 1 | 25% |" in md


def test_report_leaves_out_empty_breakdowns():
    md = render_markdown(_sample_report(), commentary="", lang="en")

    assert "Performance by Time Control" not in md
    assert "Error Patterns" not in md
    assert "Key Findings" not in md


def _with_motifs():
    from chesswhisper.stats_engine import MotifStat

    report = _sample_report()
    report.motifs = [MotifStat("hanging", allowed=4, missed=1), MotifStat("other", unsided=3)]
    return report


def test_tactical_patterns_section_lists_each_motif():
    md = render_markdown(_with_motifs(), commentary="", lang="en")

    assert "## Tactical Patterns" in md
    assert "| hanging piece | 4 | 1 | 5 |" in md
    assert "| other (positional or deeper) | - | - | 3 |" in md


def test_tactical_patterns_section_in_turkish():
    md = render_markdown(_with_motifs(), commentary="", lang="tr")

    assert "## Taktik Desenler" in md
    assert "| korumasız taşı alma | 4 | 1 | 5 |" in md


def test_no_tactical_patterns_section_without_mistakes():
    assert "Tactical Patterns" not in render_markdown(_sample_report(), commentary="", lang="en")


def _with_theory():
    from chesswhisper.theory import TheoryExit, TheoryStats

    report = _sample_report()
    report.opening_theory = [TheoryStats(name="Ruy Lopez", eco="C60", games=2, you_left=1, opponent_left=1,
                                         avg_book_moves=5.5, avg_eval_at_exit=30.0, avg_change=-120.0)]
    report.games[0].theory = TheoryExit(book_plies=8, left_by="you", exit_move=5, eval_at_exit=20,
                                        eval_later=-80, exit_san="c3")
    return report


def test_opening_theory_section_and_per_game_line():
    md = render_markdown(_with_theory(), commentary="", lang="en")

    assert "## Opening Theory" in md
    assert "| Ruy Lopez | 2 | 5.5 | 1 / 1 | +0.3 | \u22121.2 |" in md
    assert "- You left theory on move 5 (c3); eval +0.2, ten moves later \u22120.8" in md


def test_opening_theory_in_turkish_and_a_game_that_stayed_in_book():
    from chesswhisper.theory import TheoryExit

    report = _with_theory()
    report.games[0].theory = TheoryExit(book_plies=12, left_by=None, exit_move=None, eval_at_exit=None, eval_later=None)

    md = render_markdown(report, commentary="", lang="tr")

    assert "## A\u00e7\u0131l\u0131\u015f Teorisi" in md
    assert "Oyun boyunca a\u00e7\u0131l\u0131\u015f teorisinde kal\u0131nd\u0131." in md


def test_no_opening_theory_section_when_no_game_left_the_book():
    assert "Opening Theory" not in render_markdown(_sample_report(), commentary="", lang="en")
