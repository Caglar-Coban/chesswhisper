from __future__ import annotations

import re
from dataclasses import dataclass

import chess
import chess.engine
import chess.pgn

_CLK_RE = re.compile(r"\[%clk (\d+):(\d+):(\d+(?:\.\d+)?)\]")
# How much of the engine's principal variation to keep per position.
BEST_LINE_PLIES = 10


@dataclass
class PositionEval:
    ply: int
    move_san: str
    color: bool
    cp_before: int
    cp_after: int
    fen_after: str
    clock_seconds: float | None = None
    # Position the move was played from, the move itself, and the engine's
    # preferred move there -- what a board diagram of this move needs.
    fen_before: str | None = None
    move_uci: str | None = None
    best_move_uci: str | None = None
    # The start of the engine's principal variation from fen_before (UCI,
    # best_move_uci first): what a multi-move puzzle plays out.
    best_line_uci: list[str] | None = None


def parse_clock_seconds(comment: str) -> float | None:
    if not comment:
        return None
    match = _CLK_RE.search(comment)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def analyze_game(
    game: chess.pgn.Game,
    engine,
    movetime_ms: int = 300,
    depth: int | None = None,
) -> list[PositionEval]:
    limit = chess.engine.Limit(depth=depth) if depth is not None else chess.engine.Limit(time=movetime_ms / 1000)

    board = game.board()
    results: list[PositionEval] = []

    info_before = engine.analyse(board, limit, game=game)

    for ply, node in enumerate(game.mainline(), start=1):
        move = node.move
        mover = board.turn
        san = board.san(move)
        fen_before = board.fen()
        cp_before = info_before["score"].pov(mover).score(mate_score=100_000)
        pv = info_before.get("pv")
        best_move_uci = pv[0].uci() if pv else None
        best_line_uci = [pv_move.uci() for pv_move in pv[:BEST_LINE_PLIES]] if pv else None

        board.push(move)
        info_after = engine.analyse(board, limit, game=game)
        cp_after = info_after["score"].pov(mover).score(mate_score=100_000)

        results.append(
            PositionEval(
                ply=ply,
                move_san=san,
                color=mover,
                cp_before=cp_before,
                cp_after=cp_after,
                fen_after=board.fen(),
                clock_seconds=parse_clock_seconds(node.comment),
                fen_before=fen_before,
                move_uci=move.uci(),
                best_move_uci=best_move_uci,
                best_line_uci=best_line_uci,
            )
        )
        info_before = info_after

    return results
