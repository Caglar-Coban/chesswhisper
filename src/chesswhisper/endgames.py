"""How the player does once a game reaches an endgame, by kind of endgame.

A game's endgame starts at the first position the phase detection calls an
endgame. What is left on the board then gives its kind, and the evaluation
then (from the player's side) what a fair result would be: a winning
position should be won ("converted"), a level one at least drawn ("held"),
and a losing one drawn or better is "saved".
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

ENDGAME_TYPES = ("pawn", "rook", "minor", "rook_minor", "queen", "mixed")
DECISIVE_CP = 200
EVAL_CLAMP_CP = 1000


def endgame_type(board: chess.Board) -> str:
    pieces = {
        piece_type
        for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
        if board.pieces(piece_type, chess.WHITE) or board.pieces(piece_type, chess.BLACK)
    }
    if not pieces:
        return "pawn"
    if pieces == {chess.ROOK}:
        return "rook"
    if pieces == {chess.QUEEN}:
        return "queen"
    if pieces <= {chess.KNIGHT, chess.BISHOP}:
        return "minor"
    if chess.QUEEN not in pieces:
        return "rook_minor"
    return "mixed"


@dataclass(frozen=True)
class EndgameEntry:
    kind: str
    move_number: int
    eval_at_entry: int  # player's side, clamped
    expectation: str  # "winning" | "level" | "losing"
    result: str  # the game's result for the player
    acpl: float  # the player's average centipawn loss from the entry on


def endgame_entry(evals, player_color: bool, result: str, custom_start: bool = False) -> EndgameEntry | None:
    # Imported here: stats_engine imports this module.
    from chesswhisper.stats_engine import cp_loss, game_phase

    for index, ev in enumerate(evals):
        if game_phase(ev.ply, ev.fen_after, force_material_based=custom_start) != "endgame":
            continue
        board = chess.Board(ev.fen_after)
        cp = ev.cp_after if ev.color == player_color else -ev.cp_after
        cp = max(-EVAL_CLAMP_CP, min(EVAL_CLAMP_CP, cp))
        if cp >= DECISIVE_CP:
            expectation = "winning"
        elif cp <= -DECISIVE_CP:
            expectation = "losing"
        else:
            expectation = "level"
        losses = [cp_loss(later) for later in evals[index:] if later.color == player_color]
        return EndgameEntry(
            kind=endgame_type(board),
            move_number=board.fullmove_number,
            eval_at_entry=cp,
            expectation=expectation,
            result=result,
            acpl=sum(losses) / len(losses) if losses else 0.0,
        )
    return None


@dataclass
class EndgameStats:
    kind: str
    games: int = 0
    winning: int = 0
    converted: int = 0  # winning -> won
    level: int = 0
    held: int = 0  # level -> drawn or won
    losing: int = 0
    saved: int = 0  # losing -> drawn or won
    acpl: float = 0.0


def endgame_stats(game_reports: list) -> list[EndgameStats]:
    """One row per kind of endgame reached, in ENDGAME_TYPES order.

    Games with an unknown result count towards games and ACPL only.
    """
    rows: dict[str, EndgameStats] = {}
    acpls: dict[str, list[float]] = {}
    for report in game_reports:
        entry = report.endgame
        if entry is None:
            continue
        row = rows.setdefault(entry.kind, EndgameStats(entry.kind))
        row.games += 1
        acpls.setdefault(entry.kind, []).append(entry.acpl)
        if entry.result not in ("win", "draw", "loss"):
            continue
        not_lost = entry.result in ("win", "draw")
        if entry.expectation == "winning":
            row.winning += 1
            row.converted += entry.result == "win"
        elif entry.expectation == "level":
            row.level += 1
            row.held += not_lost
        else:
            row.losing += 1
            row.saved += not_lost
    for kind, values in acpls.items():
        rows[kind].acpl = sum(values) / len(values)
    return [rows[kind] for kind in ENDGAME_TYPES if kind in rows]
