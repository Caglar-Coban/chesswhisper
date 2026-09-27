import chess
import pytest

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.motifs import MATE_THRESHOLD, error_motif, tactic_motif

# --- tactic_motif ----------------------------------------------------------


@pytest.mark.parametrize(
    ("fen", "move", "motif"),
    [
        # Ra8#: rook mates on the back rank behind the king's own pawns.
        ("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "a1a8", "back_rank"),
        # Scholar's mate: Qxf7# is mate, but not on the back rank.
        ("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4", "h5f7", "mate"),
        ("8/P6k/8/8/8/8/8/K7 w - - 0 1", "a7a8q", "promotion"),
        # Nc7+ forks king and rook.
        ("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", "b5c7", "fork"),
        # Nd6+ forks king and the b7 rook.
        ("4k3/1r6/8/8/2N5/8/8/4K3 w - - 0 1", "c4d6", "fork"),
        # Bb5 pins the c6 knight to the king.
        ("4k3/8/2n5/8/8/8/8/4KB2 w - - 0 1", "f1b5", "pin"),
        # Rxd5 takes an undefended knight.
        ("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1", "d1d5", "hanging"),
        # Rxd5 takes a queen the c6 pawn defends: still a rook for a queen.
        ("4k3/8/2p5/3q4/8/8/8/3RK3 w - - 0 1", "d1d5", "hanging"),
        # exd5 takes a defended knight with a pawn.
        ("4k3/8/2p5/3n4/4P3/8/8/4K3 w - - 0 1", "e4d5", "hanging"),
    ],
)
def test_motifs(fen, move, motif):
    assert tactic_motif(fen, move) == motif


def test_a_forker_the_queen_takes_for_free_is_no_fork():
    # Nc7+ hits king and rook, but the d8 queen takes the undefended knight.
    assert tactic_motif("r2qk3/8/8/1N6/8/8/8/4K3 w - - 0 1", "b5c7") is None


def test_a_forker_a_pawn_takes_is_no_fork():
    # Nd6+ hits king and rook, but exd6 wins the knight.
    assert tactic_motif("4k3/1r2p3/8/8/2N5/8/8/4K3 w - - 0 1", "c4d6") is None


def test_an_even_trade_is_not_hanging():
    # Nxd5 cxd5: a knight for a knight.
    assert tactic_motif("4k3/8/2p5/3n4/8/4N3/8/4K3 w - - 0 1", "e3d5") is None


def test_forced_mate_hint_names_a_quiet_move_mate():
    assert tactic_motif("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "g1f1", forced_mate=True) == "mate"


def test_quiet_move_has_no_motif():
    assert tactic_motif(chess.STARTING_FEN, "e2e4") is None


@pytest.mark.parametrize("move", [None, "", "zz99", "e2e5"])
def test_bad_or_illegal_moves_have_no_motif(move):
    assert tactic_motif(chess.STARTING_FEN, move) is None


def test_bad_fen_has_no_motif():
    assert tactic_motif("not a fen", "e2e4") is None


# --- error_motif -------------------------------------------------------------


def _ev(fen_before, move_uci, best_uci, cp_before=0, cp_after=-300, ply=1):
    board = chess.Board(fen_before)
    move = chess.Move.from_uci(move_uci)
    san = board.san(move)
    color = board.turn
    board.push(move)
    return PositionEval(
        ply=ply, move_san=san, color=color, cp_before=cp_before, cp_after=cp_after,
        fen_after=board.fen(), fen_before=fen_before, move_uci=move_uci, best_move_uci=best_uci,
    )


def _reply(ev, best_uci):
    """The next ply's entry: its best move is the refutation of *ev*."""
    return PositionEval(
        ply=ev.ply + 1, move_san="?", color=not ev.color, cp_before=-ev.cp_after, cp_after=0,
        fen_after=ev.fen_after, fen_before=ev.fen_after, move_uci=None, best_move_uci=best_uci,
    )


def test_moving_a_piece_en_prise_allows_a_hanging_piece():
    # Nd5?? walks into the d8 queen.
    ev = _ev("3qk3/8/8/8/8/2N5/8/4K3 w - - 0 1", "c3d5", best_uci="e1e2")
    assert error_motif(ev, _reply(ev, "d8d5")) == ("hanging", "allowed")


def test_allowing_a_fork():
    # Kd2-e2?? lets ...Nc3+ fork the king and the a2 rook.
    ev = _ev("4k3/8/8/8/4n3/8/R2K4/8 w - - 0 1", "d2e2", best_uci="a2a8")
    assert error_motif(ev, _reply(ev, "e4c3")) == ("fork", "allowed")


def test_allowing_a_back_rank_mate():
    # Ne4?? takes the knight away from b1/f1, and ...Ra1 is mate.
    ev = _ev("r5k1/5ppp/8/8/8/8/3N1PPP/6K1 w - - 0 1", "d2e4", best_uci="g1f1")
    assert error_motif(ev, _reply(ev, "a8a1")) == ("back_rank", "allowed")


def test_allowing_a_forced_mate_uses_the_evaluation():
    ev = _ev("4k3/8/8/8/8/8/8/4K3 w - - 0 1", "e1e2", best_uci="e1d1", cp_after=-(MATE_THRESHOLD + 10))
    assert error_motif(ev, _reply(ev, "e8e7")) == ("mate", "allowed")


def test_a_missed_free_piece():
    ev = _ev("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1", "e1e2", best_uci="d1d5")
    assert error_motif(ev, _reply(ev, "e8e7")) == ("hanging", "missed")


def test_a_missed_forced_mate_uses_the_evaluation():
    ev = _ev("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "g1f1", best_uci="a1a8", cp_before=MATE_THRESHOLD + 5, cp_after=0)
    assert error_motif(ev, _reply(ev, "g8f8")) == ("back_rank", "missed")


def test_what_was_allowed_wins_over_what_was_missed():
    # Rxh5 would have won a knight; Nd5?? instead hangs the knight to the queen.
    ev = _ev("3qk3/8/8/7n/8/2N5/8/4K2R w - - 0 1", "c3d5", best_uci="h1h5")
    assert error_motif(ev, _reply(ev, "d8d5")) == ("hanging", "allowed")


def test_a_recapture_after_the_players_capture_is_an_exchange():
    # Nxd5 cxd5: the reply takes back a knight for a knight.
    ev = _ev("4k3/8/2p5/3n4/8/4N3/8/4K3 w - - 0 1", "e3d5", best_uci="e1e2")
    assert error_motif(ev, _reply(ev, "c6d5")) == ("other", None)


def test_last_ply_mistake_uses_only_the_missed_side():
    ev = _ev("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1", "e1e2", best_uci="d1d5")
    assert error_motif(ev, None) == ("hanging", "missed")


def test_a_next_entry_from_another_position_is_ignored():
    ev = _ev("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1", "e1f1", best_uci="e1f2")
    unrelated = _reply(ev, "d5e3")
    unrelated.fen_before = chess.STARTING_FEN
    assert error_motif(ev, unrelated) == ("other", None)


def test_playing_the_best_move_misses_nothing():
    ev = _ev("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1", "d1d5", best_uci="d1d5")
    assert error_motif(ev, None) == ("other", None)


# --- aggregation ---------------------------------------------------------------


class _Report:
    def __init__(self, moves):
        self.moves = moves


def _move(motif, side, classification="mistake"):
    from chesswhisper.stats_engine import MoveRecord

    return MoveRecord(ply=1, move_number=1, eval_before=0, cp_loss=200, classification=classification,
                      motif=motif, motif_side=side)


def test_motif_stats_counts_by_side_and_orders_by_frequency():
    from chesswhisper.stats_engine import MoveRecord, motif_stats

    reports = [
        _Report([_move("fork", "missed"), _move("hanging", "allowed"), _move("other", None)]),
        _Report([_move("hanging", "allowed"), _move("hanging", "missed"), _move("other", None),
                 _move("other", None), _move("other", None),
                 MoveRecord(ply=2, move_number=1, eval_before=0, cp_loss=0, classification="fine")]),
    ]

    stats = motif_stats(reports)

    assert [(s.motif, s.allowed, s.missed, s.total) for s in stats] == [
        ("hanging", 2, 1, 3),
        ("fork", 0, 1, 1),
        ("other", 0, 0, 4),
    ]


def test_a_king_attacking_two_pieces_is_no_fork():
    # Kb4 attacks the c5 knight and the b5 pawn: real, but not what "fork" should mean.
    assert tactic_motif("8/p5pp/6p1/1pnk4/8/2K2P2/P5PP/3N4 w - - 0 35", "c3b4") is None


def test_a_pin_by_a_piece_that_is_taken_back_is_no_pin():
    # Qxg4 trades queens (fxg4); that the queen pins the g2 knight on the way is incidental.
    fen = "2q1r1k1/pb3pp1/1p5p/4r1Pn/3pP1QP/P2B1P2/2P1R1N1/5RK1 b - - 2 27"
    assert tactic_motif(fen, "c8g4") is None


def test_a_capture_that_leads_to_mate_is_still_a_capture():
    # ...gxf6 wins a hanging rook; the engine's forced mate comes much later.
    fen = "8/6p1/3R1Rp1/2r1p3/6K1/4k3/8/8 b - - 1 44"
    assert tactic_motif(fen, "g7f6", forced_mate=True) == "hanging"
