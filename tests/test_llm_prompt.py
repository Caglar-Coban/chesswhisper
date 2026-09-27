import chess

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.llm.prompt import build_prompt
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
    stats = PlayerGameStats(acpl=42.5, accuracy=87.3, errors_by_phase=errors)
    top_mistake = PositionEval(
        ply=10, move_san="Nf3", color=chess.WHITE, cp_before=20, cp_after=-180,
        fen_after=chess.STARTING_FEN,
    )
    game = GameReport(
        game_index=0, your_color="white", opponent="Bob", result="win",
        your_stats=stats, eco="C60", opening_name="Ruy Lopez",
        time_management=TimeManagementStats(available=False), top_mistakes=[top_mistake],
    )
    return AggregateReport(
        player="Alice", games=[game], overall_acpl=42.5, overall_accuracy=87.3,
        error_totals=errors, openings=[OpeningStats(eco="C60", name="Ruy Lopez", games=1, wins=1)],
        time_management=TimeManagementStats(available=False),
    )


def test_build_prompt_includes_key_numbers():
    prompt = build_prompt(_sample_report(), lang="tr")
    assert "Alice" in prompt
    assert "42.5" in prompt
    assert "Ruy Lopez" in prompt
    assert "tr" not in prompt.split()  # sanity: lang code itself isn't dumped verbatim into prose
    assert chess.STARTING_FEN in prompt


def test_build_prompt_english_instruction():
    prompt = build_prompt(_sample_report(), lang="en")
    assert "Write your response in English." in prompt


def test_build_prompt_includes_breakdowns_and_findings():
    from chesswhisper.stats_engine import PatternBucket, SegmentStats

    report = _sample_report()
    report.by_time_class = [SegmentStats("blitz", games=4, wins=3, losses=1, acpl=30.0, accuracy=88.0)]
    report.by_opponent_strength = [SegmentStats("stronger", games=2, wins=1, losses=1)]
    report.move_ranges = [PatternBucket("1-10", moves=40, errors=1), PatternBucket("21-30", moves=40, errors=12),
                          PatternBucket("41+", moves=0)]
    report.eval_states = [PatternBucket("winning", moves=20, errors=6), PatternBucket("equal", moves=60, errors=7)]

    prompt = build_prompt(report, lang="en")

    assert "blitz: 4 games, 3W/1L/0D, ACPL 30.0, accuracy 88.0%" in prompt
    assert "stronger: 2 games, 1W/1L/0D" in prompt
    assert "moves 21-30: 12/40" in prompt
    assert "moves 41+" not in prompt  # empty buckets are left out
    assert "winning: 6/20" in prompt
    assert "Automatically detected patterns:" in prompt
    assert "Your mistakes cluster in moves 21-30" in prompt


def test_build_prompt_includes_tactical_motifs():
    from chesswhisper.stats_engine import MotifStat

    report = _sample_report()
    report.motifs = [MotifStat("fork", allowed=2, missed=1), MotifStat("other", unsided=4)]

    prompt = build_prompt(report, lang="en")

    assert "fork: allowed 2, missed 1" in prompt
    assert "no clear tactic (positional or deeper): 4" in prompt


def test_build_prompt_includes_opening_theory():
    from chesswhisper.theory import TheoryStats

    report = _sample_report()
    report.opening_theory = [TheoryStats(name="Ruy Lopez", eco="C60", games=3, you_left=2, opponent_left=1,
                                         avg_book_moves=6.0, avg_eval_at_exit=15.0, avg_change=-90.0)]

    prompt = build_prompt(report, lang="en")

    assert "Ruy Lopez: 3 games, 6.0 moves in book on average, left by the player 2x" in prompt
    assert "change over the next 10 moves -90" in prompt


def test_build_prompt_includes_opponent_errors():
    from chesswhisper.chances import ChanceStats

    report = _sample_report()
    report.chances = ChanceStats(total=6, punished=2, missed_by_motif=[("fork", 3), ("other", 1)])

    assert "Opponents' mistakes and blunders: 6, punished by the player: 2; missed ones by the tactic the reply needed: fork 3, other 1" in build_prompt(report, lang="en")


def test_build_prompt_includes_endgames():
    from chesswhisper.endgames import EndgameStats

    report = _sample_report()
    report.endgames = [EndgameStats("rook", games=3, winning=2, converted=1, level=1, held=1, acpl=24.5)]

    assert "rook: 3 games, winning 2 (won 1), level 1 (held 1), losing 0 (saved 0), ACPL 24.5" in build_prompt(report, lang="en")


def test_build_prompt_includes_recent_form():
    from datetime import datetime

    from chesswhisper.form import FormPeriod, RecentForm

    report = _sample_report()
    period = FormPeriod(games=10, first=datetime(2026, 1, 1), last=datetime(2026, 2, 1), accuracy=90.0, acpl=30.0,
                        blunders_per_game=0.5, score=0.6)
    report.recent_form = RecentForm(recent=period, earlier=period)

    assert "recent: 10 games, accuracy 90.0%, ACPL 30.0, 0.5 blunders per game, score 60%" in build_prompt(report, lang="en")
