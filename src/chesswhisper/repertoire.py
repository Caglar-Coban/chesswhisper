"""An opening explorer built from a player's own analysed games.

The games (the web UI's saved timelines) are merged into a move tree from
the starting position, up to MAX_PLIES deep. Each position lists the moves
played from it, with how many games went that way, the player's score in
them, how often the player's own move there was an inaccuracy or worse,
and whether the move is still in the bundled opening table. The same game
saved in two analyses counts once.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import chess

from chesswhisper.openings import EcoEntry, match_opening
from chesswhisper.theory import book_prefixes

MAX_PLIES = 20
COLORS = ("white", "black")
_POINTS = {"win": 1.0, "draw": 0.5, "loss": 0.0}


@dataclass
class _Node:
    san: str = ""
    games: int = 0
    points: float = 0.0
    scored: int = 0  # games with a known result
    errors: int = 0  # the player's move here was an inaccuracy, mistake or blunder
    children: dict[str, _Node] = field(default_factory=dict)


@dataclass(frozen=True)
class MoveStat:
    uci: str
    san: str
    games: int
    score_pct: float | None  # the player's score in these games, 0-100
    error_pct: float | None  # only for the player's own moves
    in_book: bool


@dataclass(frozen=True)
class Position:
    color: str
    games: int  # games that reached this position
    line_uci: list[str]
    line_san: list[str]
    fen: str
    opening: str | None
    player_to_move: bool
    moves: list[MoveStat]


def _game_key(timeline: dict) -> tuple:
    return (timeline.get("white"), timeline.get("black"), tuple(m["uci"] for m in timeline["moves"]))


def build_tree(timelines: Iterable[dict], color: str) -> _Node:
    """The tree of the games the player had *color* in, from the standard start position."""
    root = _Node()
    seen: set[tuple] = set()
    for timeline in timelines:
        if timeline.get("your_color") != color or timeline.get("start_fen", chess.STARTING_FEN) != chess.STARTING_FEN:
            continue
        key = _game_key(timeline)
        if key in seen:
            continue
        seen.add(key)
        points = _POINTS.get(timeline.get("result", ""))
        node = root
        node.games += 1
        if points is not None:
            node.points += points
            node.scored += 1
        for move in timeline["moves"][:MAX_PLIES]:
            child = node.children.get(move["uci"])
            if child is None:
                child = node.children[move["uci"]] = _Node(san=move["san"])
            child.games += 1
            if points is not None:
                child.points += points
                child.scored += 1
            if move["color"] == color and move.get("classification"):
                child.errors += 1
            node = child
    return root


def explore(root: _Node, color: str, line_uci: list[str], table: list[EcoEntry]) -> Position | None:
    """The position after *line_uci* in the tree, or None if the games never reached it."""
    node, board, sans = root, chess.Board(), []
    for uci in line_uci:
        child = node.children.get(uci)
        if child is None:
            return None
        node = child
        sans.append(node.san)
        board.push_uci(uci)
    prefixes = book_prefixes(table)
    player_to_move = (board.turn == chess.WHITE) == (color == "white")
    moves = [
        MoveStat(
            uci=uci,
            san=child.san,
            games=child.games,
            score_pct=round(100 * child.points / child.scored, 1) if child.scored else None,
            error_pct=round(100 * child.errors / child.games, 1) if player_to_move else None,
            in_book=tuple([*sans, child.san]) in prefixes,
        )
        for uci, child in sorted(node.children.items(), key=lambda item: (-item[1].games, item[1].san))
    ]
    opening = match_opening(sans, table) if sans else None
    return Position(
        color=color,
        games=node.games,
        line_uci=list(line_uci),
        line_san=sans,
        fen=board.fen(),
        opening=opening.name if opening is not None and opening.moves else None,
        player_to_move=player_to_move,
        moves=moves,
    )
