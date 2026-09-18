from dataclasses import dataclass
from importlib import resources


@dataclass(frozen=True)
class EcoEntry:
    eco: str
    name: str
    moves: tuple[str, ...]


UNKNOWN_OPENING = EcoEntry(eco="A00", name="Uncommon Opening", moves=())


def load_eco_table() -> list[EcoEntry]:
    data_text = resources.files("chesswhisper.data").joinpath("eco.tsv").read_text(encoding="utf-8")
    lines = data_text.strip().splitlines()
    entries = []
    for line in lines[1:]:  # skip header
        eco, name, moves_str = line.split("\t")
        entries.append(EcoEntry(eco=eco, name=name, moves=tuple(moves_str.split())))
    return entries


def match_opening(san_moves: list[str], table: list[EcoEntry]) -> EcoEntry:
    best: EcoEntry = UNKNOWN_OPENING
    for entry in table:
        n = len(entry.moves)
        if n == 0 or n > len(san_moves):
            continue
        if tuple(san_moves[:n]) == entry.moves and n > len(best.moves):
            best = entry
    return best
