from chesswhisper.stats_engine import (
    AggregateReport, GameReport, PlayerGameStats, ErrorCounts,
    TimeManagementStats, OpeningStats, PHASES,
)
from chesswhisper.report_writer import render_markdown, write_report


def _sample_report():
    errors = {p: ErrorCounts() for p in PHASES}
    errors["middlegame"].blunder = 1
    your_stats = PlayerGameStats(acpl=42.5, accuracy=87.3, errors_by_phase=errors)
    opp_stats = PlayerGameStats(acpl=30.0, accuracy=90.0, errors_by_phase={p: ErrorCounts() for p in PHASES})

    game = GameReport(
        game_index=0, your_color="white", opponent="Bob", result="win",
        your_stats=your_stats, opponent_stats=opp_stats,
        eco="C60", opening_name="Ruy Lopez",
        time_management=TimeManagementStats(available=False),
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


def test_render_markdown_handles_partial_time_management_data():
    report = _sample_report()
    # available=True but only the low-time bucket has data (normal_time_error_rate is None)
    report.time_management = TimeManagementStats(available=True, low_time_error_rate=0.5, normal_time_error_rate=None)

    md = render_markdown(report, commentary="", lang="en")

    assert "50.0%" in md
    assert "not enough data" in md
