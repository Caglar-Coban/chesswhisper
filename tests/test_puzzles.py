import json
from dataclasses import dataclass
from datetime import datetime

import chess

from chesswhisper.web.puzzles import legal_moves, order_puzzles, puzzles_from_records

START = chess.STARTING_FEN
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


@dataclass
class _Record:
    id: int
    created_at: datetime
    critical_moves_json: str | None


def _entry(fen=START, uci="a2a3", best="e2e4", color="white", game_index=0, opponent="Bob", ply=1, loss=350):
    board = chess.Board(fen)
    return {
        "game_index": game_index, "opponent": opponent, "your_color": color, "ply": ply,
        "move_number": board.fullmove_number, "san": board.san(chess.Move.from_uci(uci)), "uci": uci,
        "best_uci": best, "best_san": board.san(chess.Move.from_uci(best)) if best else None,
        "fen_before": fen, "cp_before": 50, "cp_after": 50 - loss, "cp_loss": loss,
        "classification": "blunder" if loss >= 300 else "mistake",
    }


def test_each_critical_move_with_a_better_alternative_is_a_puzzle():
    record = _Record(7, datetime(2026, 9, 1), json.dumps([
        _entry(),
        _entry(fen=AFTER_E4, uci="a7a6", best="e7e5", color="black", game_index=2, opponent="Carol", ply=2, loss=120),
    ]))

    puzzles = puzzles_from_records([record])

    assert [(p.record_id, p.index) for p in puzzles] == [(7, 0), (7, 1)]
    first, second = puzzles
    assert (first.fen, first.best_uci, first.best_san, first.played_san) == (START, "e2e4", "e4", "a3")
    assert first.your_color == "white" and first.game_number == 1 and first.opponent == "Bob"
    assert first.move_label == "1. a3"
    assert second.move_label == "1... a6" and second.classification == "mistake"


def test_moves_without_a_usable_alternative_are_skipped():
    record = _Record(1, datetime(2026, 9, 1), json.dumps([
        _entry(best=None),            # engine gave no move
        _entry(best="a2a3"),          # engine agreed with the move played
        {**_entry(), "best_uci": "e2e5"},  # not legal in the position
        _entry(),
    ]))

    assert [p.index for p in puzzles_from_records([record])] == [3]


def test_records_without_critical_moves_or_with_bad_json_give_nothing():
    records = [_Record(1, datetime(2026, 9, 1), None), _Record(2, datetime(2026, 9, 2), "not json")]

    assert puzzles_from_records(records) == []


def test_order_puts_new_puzzles_first_then_missed_then_solved_newest_first():
    old = _Record(1, datetime(2026, 9, 1), json.dumps([_entry(best="e2e4"), _entry(best="d2d4")]))
    new = _Record(2, datetime(2026, 9, 5), json.dumps([_entry(best="g1f3"), _entry(best="c2c4")]))
    puzzles = puzzles_from_records([old, new])
    attempts = {(2, 0): True, (1, 0): False}  # key -> solved at least once

    ordered = order_puzzles(puzzles, attempts)

    assert [(p.record_id, p.index) for p in ordered] == [(2, 1), (1, 1), (1, 0), (2, 0)]


def test_legal_moves_lists_every_move_in_uci():
    moves = legal_moves(START)

    assert len(moves) == 20
    assert "e2e4" in moves and "g1f3" in moves
    promotion = legal_moves("8/P7/8/8/8/8/8/k6K w - - 0 1")
    assert {"a7a8q", "a7a8n"} <= set(promotion)


def test_the_same_position_from_overlapping_analyses_is_one_puzzle():
    # Re-analyzing recent games repeats their critical moves in every record.
    newer = _Record(9, datetime(2026, 9, 9), json.dumps([_entry(), _entry(fen=AFTER_E4, uci="a7a6", best="e7e5",
                                                                           color="black")]))
    older = _Record(3, datetime(2026, 9, 3), json.dumps([_entry()]))

    puzzles = puzzles_from_records([newer, older])

    assert [(p.record_id, p.index) for p in puzzles] == [(9, 0), (9, 1)]
