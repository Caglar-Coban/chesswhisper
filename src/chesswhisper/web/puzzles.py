"""Puzzles made from the player's own mistakes.

Every critical move saved with an analysis already has what a puzzle needs:
the position before the move, the move that was played, and the engine's
preferred move. The player gets the position back and has to find the
engine's move.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import chess

PuzzleKey = tuple[int, int]  # (analysis record id, index in its critical moves)


class _PuzzleSource(Protocol):
    id: int
    created_at: datetime
    critical_moves_json: str | None


@dataclass(frozen=True)
class Puzzle:
    record_id: int
    index: int
    created_at: datetime
    fen: str
    your_color: str
    best_uci: str
    best_san: str
    played_uci: str
    played_san: str
    move_label: str
    game_number: int
    opponent: str
    cp_loss: int
    classification: str
    ply: int = 0

    @property
    def key(self) -> PuzzleKey:
        return (self.record_id, self.index)


def _puzzle(record: _PuzzleSource, index: int, entry: dict) -> Puzzle | None:
    best_uci, played_uci = entry.get("best_uci"), entry.get("uci")
    if not best_uci or not played_uci or best_uci == played_uci:
        return None
    try:
        board = chess.Board(entry["fen_before"])
        best = chess.Move.from_uci(best_uci)
    except (KeyError, ValueError):
        return None
    if best not in board.legal_moves:
        return None
    number = int(entry.get("move_number") or board.fullmove_number)
    dots = ". " if entry.get("your_color") == "white" else "... "
    return Puzzle(
        record_id=record.id,
        index=index,
        created_at=record.created_at,
        fen=board.fen(),
        your_color=str(entry.get("your_color", "white")),
        best_uci=best_uci,
        best_san=board.san(best),
        played_uci=played_uci,
        played_san=str(entry.get("san", "")),
        move_label=f"{number}{dots}{entry.get('san', '')}",
        game_number=int(entry.get("game_index", 0)) + 1,
        opponent=str(entry.get("opponent", "")),
        cp_loss=int(entry.get("cp_loss", 0)),
        classification=str(entry.get("classification", "")),
        ply=int(entry.get("ply", 0)),
    )


def puzzles_from_records(records) -> list[Puzzle]:
    """Every usable puzzle in *records*, in record order then critical-move order.

    Analyses of overlapping games repeat the same mistakes, so a position and
    solution seen in an earlier record in *records* isn't offered again.
    """
    puzzles = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        if not record.critical_moves_json:
            continue
        try:
            entries = json.loads(record.critical_moves_json)
        except ValueError:
            continue
        for index, entry in enumerate(entries):
            puzzle = _puzzle(record, index, entry) if isinstance(entry, dict) else None
            if puzzle is not None and (puzzle.fen, puzzle.best_uci) not in seen:
                seen.add((puzzle.fen, puzzle.best_uci))
                puzzles.append(puzzle)
    return puzzles


def order_puzzles(puzzles: list[Puzzle], solved: dict[PuzzleKey, bool]) -> list[Puzzle]:
    """New puzzles first, then ones answered wrong, then solved ones; newest analysis first in each.

    *solved* maps each attempted puzzle to whether any attempt was correct.
    """

    def group(p: Puzzle) -> int:
        if p.key not in solved:
            return 0
        return 2 if solved[p.key] else 1

    # sorted() is stable: newest analysis first, then the critical moves' own order.
    by_recency = sorted(puzzles, key=lambda p: (-p.created_at.timestamp(), -p.record_id, p.index))
    return sorted(by_recency, key=group)


def legal_moves(fen: str) -> list[str]:
    return [move.uci() for move in chess.Board(fen).legal_moves]
