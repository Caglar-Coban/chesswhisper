import io

import chess
import chess.pgn

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.openings import load_eco_table
from chesswhisper.pgn_loader import LoadedGame
from chesswhisper.stats_engine import CRITICAL_MOVES_LIMIT, build_game_report, critical_moves

ECO = load_eco_table()
# 12 plies: White (odd plies) and Black (even plies) each move six times.
MOVES = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5"


def _evals(losses, best=None):
    """PositionEvals for MOVES; losses maps ply -> centipawns lost by the mover (default 0)."""
    game = chess.pgn.read_game(io.StringIO(MOVES + " *"))
    board = game.board()
    evals = []
    for ply, move in enumerate(game.mainline_moves(), start=1):
        fen_before, san, color = board.fen(), board.san(move), board.turn
        board.push(move)
        evals.append(
            PositionEval(
                ply=ply, move_san=san, color=color, cp_before=0, cp_after=-losses.get(ply, 0),
                fen_after=board.fen(), fen_before=fen_before, move_uci=move.uci(),
                best_move_uci=(best or {}).get(ply),
            )
        )
    return evals


def _report(index, evals, white="Alice", black="Bob"):
    text = f'[White "{white}"]\n[Black "{black}"]\n[Result "*"]\n\n{MOVES} *\n'
    game = chess.pgn.read_game(io.StringIO(text))
    loaded = LoadedGame(index=index, game=game, headers=dict(game.headers))
    return build_game_report(loaded, evals, "Alice", ECO)


def test_ranks_blunders_and_mistakes_across_games_and_drops_inaccuracies():
    first = _report(0, _evals({1: 400, 3: 60, 5: 150}))
    second = _report(1, _evals({1: 350}))

    moves = critical_moves([first, second])

    assert [(m["game_index"], m["ply"], m["classification"]) for m in moves] == [
        (0, 1, "blunder"),
        (1, 1, "blunder"),
        (0, 5, "mistake"),
    ]


def test_equal_losses_keep_game_then_ply_order():
    first = _report(0, _evals({3: 200, 1: 200}))
    second = _report(1, _evals({1: 200}))

    moves = critical_moves([second, first])

    assert [(m["game_index"], m["ply"]) for m in moves] == [(0, 1), (0, 3), (1, 1)]


def test_caps_the_list_at_the_limit():
    reports = [_report(i, _evals({1: 400, 3: 400, 5: 400, 7: 400, 9: 400})) for i in range(3)]

    assert CRITICAL_MOVES_LIMIT == 12
    assert len(critical_moves(reports)) == 12
    assert len(critical_moves(reports, limit=2)) == 2


def test_skips_moves_saved_without_a_starting_position():
    evals = [
        PositionEval(ply=1, move_san="e4", color=True, cp_before=0, cp_after=-400,
                     fen_after=chess.STARTING_FEN),
    ]

    assert critical_moves([_report(0, evals)]) == []


def test_entry_carries_what_a_board_diagram_needs():
    evals = _evals({2: 500}, best={2: "c7c5"})
    report = _report(4, evals, white="Bob", black="Alice")

    [move] = critical_moves([report])

    assert move == {
        "game_index": 4,
        "opponent": "Bob",
        "your_color": "black",
        "ply": 2,
        "move_number": 1,
        "san": "e5",
        "uci": "e7e5",
        "best_uci": "c7c5",
        "best_san": "c5",
        "fen_before": evals[1].fen_before,
        "cp_before": 0,
        "cp_after": -500,
        "cp_loss": 500,
        "classification": "blunder",
    }


def test_best_san_is_none_for_a_missing_or_illegal_best_move():
    no_best = _report(0, _evals({1: 400}))
    illegal_best = _report(0, _evals({1: 400}, best={1: "e7e5"}))

    assert critical_moves([no_best])[0]["best_san"] is None
    assert critical_moves([illegal_best])[0]["best_san"] is None


def test_move_number_follows_the_position_for_games_set_up_from_a_fen():
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 5 24")
    move = board.parse_san("Nf6")
    fen_before = board.fen()
    board.push(move)
    evals = [
        PositionEval(ply=1, move_san="Nf6", color=False, cp_before=0, cp_after=-400,
                     fen_after=board.fen(), fen_before=fen_before, move_uci=move.uci()),
    ]

    [entry] = critical_moves([_report(0, evals, white="Bob", black="Alice")])

    assert entry["move_number"] == 24
