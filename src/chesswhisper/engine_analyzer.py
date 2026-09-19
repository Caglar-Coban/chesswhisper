from __future__ import annotations

import re
from dataclasses import dataclass

import chess
import chess.engine
import chess.pgn

_CLK_RE = re.compile(r"\[%clk (\d+):(\d+):(\d+(?:\.\d+)?)\]")


@dataclass
class PositionEval:
    ply: int
    move_san: str
    color: bool
    cp_before: int
    cp_after: int
    fen_after: str
    clock_seconds: float | None = None


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

    prev_score = engine.analyse(board, limit, game=game)["score"]

    for ply, node in enumerate(game.mainline(), start=1):
        move = node.move
        mover = board.turn
        san = board.san(move)
        cp_before = prev_score.pov(mover).score(mate_score=100_000)

        board.push(move)
        info_after = engine.analyse(board, limit, game=game)
        current_score = info_after["score"]
        cp_after = current_score.pov(mover).score(mate_score=100_000)

        results.append(
            PositionEval(
                ply=ply,
                move_san=san,
                color=mover,
                cp_before=cp_before,
                cp_after=cp_after,
                fen_after=board.fen(),
                clock_seconds=parse_clock_seconds(node.comment),
            )
        )
        prev_score = current_score

    return results
