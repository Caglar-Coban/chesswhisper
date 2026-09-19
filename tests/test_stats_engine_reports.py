import io

import chess.pgn
import pytest

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.openings import load_eco_table
from chesswhisper.pgn_loader import LoadedGame
from chesswhisper.stats_engine import (
    build_aggregate_report,
    build_game_report,
    detect_player,
)

WHITE = True
BLACK = False


def _loaded_game(index, white, black, result, moves_text):
    text = (
        f'[White "{white}"]\n[Black "{black}"]\n[Result "{result}"]\n\n{moves_text} {result}\n'
    )
    game = chess.pgn.read_game(io.StringIO(text))
    return LoadedGame(index=index, game=game, headers=dict(game.headers))


def test_detect_player_picks_most_common_name():
    games = [
        _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5"),
        _loaded_game(1, "Carol", "Alice", "0-1", "1. d4 d5"),
        _loaded_game(2, "Alice", "Dave", "1/2-1/2", "1. c4 c5"),
    ]
    assert detect_player(games) == "Alice"


def test_detect_player_raises_when_no_repeated_name():
    games = [
        _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5"),
        _loaded_game(1, "Carol", "Dave", "0-1", "1. d4 d5"),
    ]
    with pytest.raises(ValueError):
        detect_player(games)


def _evals_no_errors():
    # e4 (White, no loss), e5 (Black, no loss)
    return [
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=0, cp_after=20,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"),
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=-20, cp_after=-15,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"),
    ]


def test_build_game_report_assigns_your_color_and_result():
    loaded = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    evals = _evals_no_errors()
    eco_table = load_eco_table()

    report = build_game_report(loaded, evals, player_name="Alice", eco_table=eco_table)

    assert report.your_color == "white"
    assert report.opponent == "Bob"
    assert report.result == "win"
    assert report.eco == "B20" or report.eco == "C20"  # depends on ECO table content for "e4 e5"


def test_build_game_report_counts_your_blunder():
    loaded = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    evals = [
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=50, cp_after=-260,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"),
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=260, cp_after=250,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"),
    ]
    eco_table = load_eco_table()

    report = build_game_report(loaded, evals, player_name="Alice", eco_table=eco_table)

    assert report.your_stats.errors_by_phase["opening"].blunder == 1
    assert len(report.top_mistakes) == 1
    assert report.top_mistakes[0].move_san == "e4"


def test_build_game_report_raises_when_player_not_in_game():
    loaded = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    evals = _evals_no_errors()
    eco_table = load_eco_table()

    with pytest.raises(ValueError):
        build_game_report(loaded, evals, player_name="Zara", eco_table=eco_table)


def test_build_game_report_result_unknown_for_unrecorded_games():
    loaded = _loaded_game(0, "Alice", "Bob", "*", "1. e4 e5")
    evals = _evals_no_errors()
    eco_table = load_eco_table()

    report = build_game_report(loaded, evals, player_name="Alice", eco_table=eco_table)

    assert report.result == "unknown"


def test_build_aggregate_report_counts_unknown_results_separately():
    loaded = _loaded_game(0, "Alice", "Bob", "*", "1. e4 e5")
    evals = _evals_no_errors()
    eco_table = load_eco_table()

    report = build_game_report(loaded, evals, "Alice", eco_table)
    aggregate = build_aggregate_report("Alice", [report])

    opening = aggregate.openings[0]
    assert opening.unknown == 1
    assert opening.wins == 0
    assert opening.losses == 0
    assert opening.draws == 0


def test_build_game_report_marks_custom_start_position():
    text = (
        '[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n'
        '[SetUp "1"]\n[FEN "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"]\n\n'
        '1. Kd2 1-0\n'
    )
    game = chess.pgn.read_game(io.StringIO(text))
    loaded = LoadedGame(index=0, game=game, headers=dict(game.headers))
    evals = [
        PositionEval(ply=1, move_san="Kd2", color=WHITE, cp_before=0, cp_after=0,
                     fen_after="4k3/8/8/8/8/4K3/4P3/8 b - - 1 1"),
    ]
    eco_table = load_eco_table()

    report = build_game_report(loaded, evals, "Alice", eco_table)

    assert report.eco == "N/A"
    assert "custom" in report.opening_name.lower()


def test_build_aggregate_report_sums_across_games():
    loaded_a = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    loaded_b = _loaded_game(1, "Carol", "Alice", "0-1", "1. e4 e5")
    eco_table = load_eco_table()
    evals = _evals_no_errors()

    report_a = build_game_report(loaded_a, evals, "Alice", eco_table)
    report_b = build_game_report(loaded_b, evals, "Alice", eco_table)

    aggregate = build_aggregate_report("Alice", [report_a, report_b])

    assert aggregate.player == "Alice"
    assert len(aggregate.games) == 2
    assert aggregate.games[1].your_color == "black"
    assert aggregate.games[1].result == "win"  # Alice was Black in a 0-1 result


def test_build_aggregate_report_weights_acpl_by_move_count():
    loaded_a = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    loaded_b = _loaded_game(1, "Alice", "Carol", "1-0", "1. e4 e5")
    eco_table = load_eco_table()

    # Game A: Alice (White) makes one move with a huge 300cp loss.
    game_a_evals = [
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=300, cp_after=0,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"),
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=0, cp_after=0,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"),
    ]
    # Game B: Alice (White) makes three perfect moves (0 cp loss each).
    game_b_evals = [
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=0, cp_after=0,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"),
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=0, cp_after=0,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"),
        PositionEval(ply=3, move_san="Nf3", color=WHITE, cp_before=0, cp_after=0,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"),
        PositionEval(ply=4, move_san="Nc6", color=BLACK, cp_before=0, cp_after=0,
                     fen_after="r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"),
        PositionEval(ply=5, move_san="Bb5", color=WHITE, cp_before=0, cp_after=0,
                     fen_after="r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"),
        PositionEval(ply=6, move_san="a6", color=BLACK, cp_before=0, cp_after=0,
                     fen_after="r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"),
    ]

    report_a = build_game_report(loaded_a, game_a_evals, "Alice", eco_table)
    report_b = build_game_report(loaded_b, game_b_evals, "Alice", eco_table)

    aggregate = build_aggregate_report("Alice", [report_a, report_b])

    # Alice made 1 move in game A (300 cp lost) and 3 moves in game B (0 cp lost each).
    # Weighted by move count: 300 / (1 + 3) = 75.0, NOT the naive per-game mean of
    # (300 + 0) / 2 = 150.0.
    assert aggregate.overall_acpl == 75.0


def test_build_game_report_time_management_unavailable_without_clock():
    loaded = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    evals = _evals_no_errors()  # no clock_seconds set on these PositionEval fixtures
    eco_table = load_eco_table()

    report = build_game_report(loaded, evals, player_name="Alice", eco_table=eco_table)

    assert report.time_management.available is False


def test_build_game_report_time_management_flags_low_time_errors():
    loaded = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    evals = [
        # Alice (White) blunders with 10s left -> low-time error
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=50, cp_after=-260,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                     clock_seconds=10.0),
        # Bob (Black) response, not Alice's move, ignored for Alice's time management
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=260, cp_after=250,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                     clock_seconds=500.0),
    ]
    eco_table = load_eco_table()

    report = build_game_report(loaded, evals, player_name="Alice", eco_table=eco_table)

    assert report.time_management.available is True
    assert report.time_management.low_time_error_rate == 1.0
    assert report.time_management.normal_time_error_rate is None


def test_build_aggregate_report_merges_time_buckets_across_games():
    loaded_a = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    loaded_b = _loaded_game(1, "Alice", "Carol", "1-0", "1. e4 e5")
    eco_table = load_eco_table()

    low_time_blunder = [
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=50, cp_after=-260,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                     clock_seconds=5.0),
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=260, cp_after=250,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                     clock_seconds=500.0),
    ]
    clean_with_time = [
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=0, cp_after=20,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                     clock_seconds=400.0),
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=-20, cp_after=-15,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                     clock_seconds=390.0),
    ]

    report_a = build_game_report(loaded_a, low_time_blunder, "Alice", eco_table)
    report_b = build_game_report(loaded_b, clean_with_time, "Alice", eco_table)

    aggregate = build_aggregate_report("Alice", [report_a, report_b])

    assert aggregate.time_management.available is True
    assert aggregate.time_management.low_time_error_rate == 1.0    # 1 error / 1 low-time move
    assert aggregate.time_management.normal_time_error_rate == 0.0  # 0 errors / 1 normal-time move
