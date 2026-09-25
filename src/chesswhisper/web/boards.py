"""Board diagrams for a saved analysis's critical moves.

Turns the JSON stored in AnalysisRecord.critical_moves_json into caption
fields plus an SVG board drawn by python-chess. The SVG is built only from
parsed chess objects, so it is safe to insert unescaped; captions are plain
strings the template escapes as usual.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import chess
import chess.svg

logger = logging.getLogger(__name__)

# python-chess encodes mate-in-N as +/-(100_000 - N) with mate_score=100_000.
_MATE_SCORE = 100_000
_MATE_THRESHOLD = 90_000

_BOARD_COLORS = {
    "square light": "#e9e4d8",
    "square dark": "#a9b3bf",
    "margin": "#00000000",
    "coord": "#8a9099",
    "arrow red": "#c0392bcc",
    "arrow green": "#2e8b57cc",
}


@dataclass(frozen=True)
class BoardCard:
    game_number: int
    opponent: str
    move_label: str
    classification: str
    best_san: str | None
    eval_before: str
    eval_after: str
    svg: str


def format_eval(cp: int) -> str:
    """Centipawns from the player's side as pawns (+1.2 / −3.4) or mate (#3 / −#3)."""
    sign = "−" if cp < 0 else "+"
    if abs(cp) >= _MATE_THRESHOLD:
        mate_in = _MATE_SCORE - abs(cp)
        return f"#{mate_in}" if cp > 0 else f"−#{mate_in}"
    return f"{sign}{abs(cp) / 100:.1f}"


def _move_label(move_number: int, your_color: str, san: str) -> str:
    return f"{move_number}. {san}" if your_color == "white" else f"{move_number}... {san}"


def _card(entry: dict) -> BoardCard:
    board = chess.Board(entry["fen_before"])
    played = chess.Move.from_uci(entry["uci"])
    arrows = [chess.svg.Arrow(played.from_square, played.to_square, color="red")]
    if entry["best_uci"] is not None and entry["best_uci"] != entry["uci"]:
        best = chess.Move.from_uci(entry["best_uci"])
        arrows.append(chess.svg.Arrow(best.from_square, best.to_square, color="green"))
    svg = chess.svg.board(
        board,
        orientation=entry["your_color"] == "white",
        arrows=arrows,
        coordinates=True,
        colors=_BOARD_COLORS,
    )
    return BoardCard(
        game_number=int(entry["game_index"]) + 1,
        opponent=str(entry["opponent"]),
        move_label=_move_label(int(entry["move_number"]), entry["your_color"], str(entry["san"])),
        classification=str(entry["classification"]),
        # A "better move" that is the move played would contradict the card.
        best_san=entry["best_san"] if entry["best_uci"] != entry["uci"] else None,
        eval_before=format_eval(int(entry["cp_before"])),
        eval_after=format_eval(int(entry["cp_after"])),
        svg=svg,
    )


def board_cards(critical_moves_json: str | None) -> list[BoardCard]:
    """Cards for the report page, or [] when there is nothing (valid) to show."""
    if not critical_moves_json:
        return []
    try:
        return [_card(entry) for entry in json.loads(critical_moves_json)]
    except (ValueError, KeyError, TypeError):
        logger.warning("ignoring unreadable critical_moves_json", exc_info=True)
        return []
