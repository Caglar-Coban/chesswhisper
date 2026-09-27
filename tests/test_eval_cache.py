import io

import chess.pgn

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.eval_cache import EvalCache, cache_key


def _game(moves="1. e4 e5 2. Nf3 *", fen=None):
    headers = f'[SetUp "1"]\n[FEN "{fen}"]\n' if fen else ""
    return chess.pgn.read_game(io.StringIO(headers + "\n" + moves))


def _evals():
    return [
        PositionEval(ply=1, move_san="e4", color=True, cp_before=20, cp_after=30, fen_after="f1",
                     clock_seconds=59.5, fen_before="f0", move_uci="e2e4", best_move_uci="d2d4"),
        PositionEval(ply=2, move_san="e5", color=False, cp_before=-30, cp_after=-25, fen_after="f2"),
    ]


def test_round_trips_evaluations(tmp_path):
    cache = EvalCache(tmp_path / "cache.sqlite3")
    key = cache_key(_game(), movetime_ms=300, depth=None)

    assert cache.get(key) is None
    cache.put(key, _evals())

    assert cache.get(key) == _evals()


def test_key_depends_on_moves_start_and_search_limit():
    base = cache_key(_game(), movetime_ms=300, depth=None)

    assert base == cache_key(_game(), movetime_ms=300, depth=None)
    assert base != cache_key(_game("1. e4 e5 2. Nc3 *"), movetime_ms=300, depth=None)
    assert base != cache_key(_game(), movetime_ms=100, depth=None)
    assert base != cache_key(_game(), movetime_ms=300, depth=12)
    custom = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKB1R w KQkq - 0 1"
    assert base != cache_key(_game(fen=custom), movetime_ms=300, depth=None)


def test_key_ignores_movetime_when_depth_is_fixed():
    assert cache_key(_game(), movetime_ms=300, depth=12) == cache_key(_game(), movetime_ms=50, depth=12)


def test_clock_comments_change_the_key():
    # The cached evaluations carry clock times, so a copy of the game with
    # different (or no) clock data must not reuse them.
    with_clock = _game("1. e4 { [%clk 0:05:00] } e5 *")
    assert cache_key(with_clock, 300, None) != cache_key(_game("1. e4 e5 *"), 300, None)


def test_unreadable_entry_is_treated_as_a_miss(tmp_path):
    cache = EvalCache(tmp_path / "cache.sqlite3")
    cache.put("k", _evals())
    import sqlite3
    with sqlite3.connect(tmp_path / "cache.sqlite3") as conn:
        conn.execute("UPDATE evals SET payload = 'not json'")

    assert cache.get("k") is None


def test_entries_saved_before_best_lines_still_load(tmp_path):
    import json
    import sqlite3

    cache = EvalCache(tmp_path / "cache.sqlite3")
    old_entry = {"ply": 1, "move_san": "e4", "color": True, "cp_before": 0, "cp_after": 10, "fen_after": "f"}
    with sqlite3.connect(tmp_path / "cache.sqlite3") as conn:
        conn.execute("INSERT INTO evals VALUES ('k', ?)", (json.dumps([old_entry]),))
    conn.close()

    [ev] = cache.get("k")

    assert ev.best_line_uci is None


def test_best_lines_round_trip(tmp_path):
    cache = EvalCache(tmp_path / "cache.sqlite3")
    ev = PositionEval(ply=1, move_san="e4", color=True, cp_before=0, cp_after=10, fen_after="f",
                      best_line_uci=["d2d4", "d7d5"])

    cache.put("k", [ev])

    assert cache.get("k")[0].best_line_uci == ["d2d4", "d7d5"]
