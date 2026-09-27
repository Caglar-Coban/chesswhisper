"""Where each game left opening theory, who left it, and how that went.

"Theory" is the bundled opening table (openings.py): a game is in book as
long as its moves so far are the start of some line in it. The first move
that isn't is the exit. The evaluation right after that move, and again
AFTER_PLIES later, both from the player's side, show whether leaving the
book went well.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from chesswhisper.openings import EcoEntry

# Ten moves each after the game leaves the book.
AFTER_PLIES = 20
EVAL_CLAMP_CP = 1000


def book_prefixes(table: list[EcoEntry]) -> frozenset[tuple[str, ...]]:
    """Every move sequence that is the start of a line in *table*."""
    return _prefixes(tuple(table))


@lru_cache(maxsize=4)
def _prefixes(entries: tuple[EcoEntry, ...]) -> frozenset[tuple[str, ...]]:
    return frozenset(entry.moves[:n] for entry in entries for n in range(1, len(entry.moves) + 1))


@dataclass(frozen=True)
class TheoryExit:
    book_plies: int
    left_by: str | None  # "you" | "opponent" | None: the game never left the book
    exit_move: int | None  # the move number of the first move out of book
    eval_at_exit: int | None  # player's side, clamped, right after that move
    eval_later: int | None  # the same, AFTER_PLIES later or at the last move
    exit_san: str | None = None  # the first move out of book


def _player_eval(ev, player_color: bool) -> int:
    cp = ev.cp_after if ev.color == player_color else -ev.cp_after
    return max(-EVAL_CLAMP_CP, min(EVAL_CLAMP_CP, cp))


def theory_exit(evals, player_color: bool, prefixes, custom_start: bool = False) -> TheoryExit | None:
    """How the game left the book, or None for a game set up from a position."""
    if custom_start:
        return None
    sans = tuple(ev.move_san for ev in evals)
    book = 0
    while book < len(sans) and sans[: book + 1] in prefixes:
        book += 1
    if book == len(sans):
        return TheoryExit(book_plies=book, left_by=None, exit_move=None, eval_at_exit=None, eval_later=None)
    exit_ev = evals[book]
    later_ev = evals[min(book + AFTER_PLIES, len(evals) - 1)]
    return TheoryExit(
        book_plies=book,
        left_by="you" if exit_ev.color == player_color else "opponent",
        exit_move=book // 2 + 1,
        eval_at_exit=_player_eval(exit_ev, player_color),
        eval_later=_player_eval(later_ev, player_color),
        exit_san=exit_ev.move_san,
    )


@dataclass
class TheoryStats:
    """Opening theory over the games of one opening."""

    name: str
    eco: str
    games: int = 0
    you_left: int = 0
    opponent_left: int = 0
    avg_book_moves: float = 0.0
    avg_eval_at_exit: float | None = None
    avg_change: float | None = None  # eval_later - eval_at_exit


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def opening_family(name: str) -> str:
    """ "French Defense: Winawer Variation" -> "French Defense"."""
    return name.split(":", 1)[0].strip()


def theory_stats(game_reports: list) -> list[TheoryStats]:
    """One row per opening family (variations together), most games first."""
    groups: dict[str, list] = {}
    eco: dict[str, str] = {}
    for report in game_reports:
        if report.theory is None:
            continue
        family = opening_family(report.opening_name)
        groups.setdefault(family, []).append(report.theory)
        eco.setdefault(family, report.eco)

    rows = []
    for name, exits in groups.items():
        left = [e for e in exits if e.left_by is not None]
        rows.append(TheoryStats(
            name=name,
            eco=eco[name],
            games=len(exits),
            you_left=sum(1 for e in left if e.left_by == "you"),
            opponent_left=sum(1 for e in left if e.left_by == "opponent"),
            avg_book_moves=sum(e.book_plies for e in exits) / len(exits) / 2,
            avg_eval_at_exit=_mean([e.eval_at_exit for e in left]),
            avg_change=_mean([e.eval_later - e.eval_at_exit for e in left]),
        ))
    return sorted(rows, key=lambda row: (-row.games, row.name))
