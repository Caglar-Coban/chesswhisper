"""Narrowing an analysis to some of the games: by time control and by colour.

A filter is applied before any engine work, so a filtered analysis of games
already analyzed comes straight from the evaluation cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from chesswhisper.stats_engine import TIME_CLASSES, time_class

FILTER_TIME_CLASSES = tuple(tc for tc in TIME_CLASSES if tc != "unknown")
COLORS = ("white", "black")


@dataclass(frozen=True)
class GameFilter:
    time_classes: frozenset[str] = field(default_factory=frozenset)  # empty: any
    color: str | None = None  # the player's colour; None: either

    def __post_init__(self):
        unknown = set(self.time_classes) - set(FILTER_TIME_CLASSES)
        if unknown:
            raise ValueError(f"unknown time control(s): {', '.join(sorted(unknown))}")
        if self.color is not None and self.color not in COLORS:
            raise ValueError(f"unknown colour: {self.color!r}")

    @property
    def is_empty(self) -> bool:
        return not self.time_classes and self.color is None

    def matches(self, headers: dict, player: str) -> bool:
        if self.time_classes and time_class(headers.get("TimeControl")) not in self.time_classes:
            return False
        if self.color is not None:
            side = "White" if self.color == "white" else "Black"
            if headers.get(side, "") != player:
                return False
        return True

    def to_json(self) -> str | None:
        if self.is_empty:
            return None
        return json.dumps({"time_classes": sorted(self.time_classes, key=FILTER_TIME_CLASSES.index),
                           "color": self.color})

    @classmethod
    def from_json(cls, text: str | None) -> GameFilter:
        if not text:
            return cls()
        data = json.loads(text)
        return cls(time_classes=frozenset(data.get("time_classes") or ()), color=data.get("color"))

    @property
    def ordered_time_classes(self) -> list[str]:
        return sorted(self.time_classes, key=FILTER_TIME_CLASSES.index)
