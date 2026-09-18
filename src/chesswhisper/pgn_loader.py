from dataclasses import dataclass
import os

import chess.pgn


@dataclass
class LoadedGame:
    index: int
    game: chess.pgn.Game
    headers: dict[str, str]


@dataclass
class PgnLoadResult:
    games: list[LoadedGame]
    warnings: list[str]


def load_pgn(path: str) -> PgnLoadResult:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"PGN file not found: {path}")

    games: list[LoadedGame] = []
    warnings: list[str] = []

    with open(path, encoding="utf-8", errors="replace") as fh:
        index = 0
        while True:
            offset_before = fh.tell()
            try:
                game = chess.pgn.read_game(fh)
            except (ValueError, UnicodeDecodeError) as exc:
                warnings.append(f"Game at index {index} could not be parsed: {exc}")
                break

            if game is None:
                break

            if game.errors:
                warnings.append(
                    f"Game at index {index} had {len(game.errors)} parse error(s) "
                    f"and was skipped: {game.errors[0]}"
                )
                index += 1
                continue

            games.append(LoadedGame(index=index, game=game, headers=dict(game.headers)))
            index += 1

    return PgnLoadResult(games=games, warnings=warnings)
