import chess

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.stats_engine import (
    AggregateReport, GameReport, PlayerGameStats, ErrorCounts,
    TimeManagementStats, OpeningStats, PHASES,
)
from chesswhisper.llm.prompt import build_prompt


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
