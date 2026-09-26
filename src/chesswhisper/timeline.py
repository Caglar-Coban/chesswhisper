"""Move-by-move data for replaying an analyzed game in the web UI's viewer.

Evaluations are given from White's side, the convention eval graphs use, and
every move (both players') carries its classification so the viewer can mark
the opponent's errors too.
"""

from __future__ import annotations

import chess

from chesswhisper.stats_engine import _best_san, classify_move, cp_loss


def _move_entry(ev) -> dict:
    best_uci = ev.best_move_uci if ev.best_move_uci != ev.move_uci else None
    best_san = _best_san(ev.fen_before, best_uci) if ev.fen_before and best_uci else None
    return {
        "ply": ev.ply,
        "move_number": chess.Board(ev.fen_before).fullmove_number if ev.fen_before else (ev.ply + 1) // 2,
        "color": "white" if ev.color else "black",
        "san": ev.move_san,
        "uci": ev.move_uci,
        "fen": ev.fen_after,
        "eval": ev.cp_after if ev.color else -ev.cp_after,
        "classification": classify_move(cp_loss(ev)),
        "best_san": best_san,
        "best_uci": best_uci if best_san else None,
    }


def game_timelines(game_reports: list) -> list[dict]:
    timelines = []
    for report in game_reports:
        evals = report.evals
        start_fen = evals[0].fen_before if evals and evals[0].fen_before else chess.STARTING_FEN
        timelines.append({
            "game_number": report.game_index + 1,
            "white": report.white,
            "black": report.black,
            "your_color": report.your_color,
            "opponent": report.opponent,
            "result": report.result,
            "opening": report.opening_name,
            "eco": report.eco,
            "time_class": report.time_class,
            "start_fen": start_fen,
            "moves": [_move_entry(ev) for ev in evals],
        })
    return timelines
