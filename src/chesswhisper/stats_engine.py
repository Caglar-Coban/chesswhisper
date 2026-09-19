import math

import chess

INACCURACY_CP = 50
MISTAKE_CP = 100
BLUNDER_CP = 300
OPENING_PLY_CUTOFF = 20
ENDGAME_MATERIAL_THRESHOLD = 20

_PIECE_VALUES = {chess.QUEEN: 9, chess.ROOK: 5, chess.BISHOP: 3, chess.KNIGHT: 3}


def classify_move(cp_loss: int) -> str:
    if cp_loss >= BLUNDER_CP:
        return "blunder"
    if cp_loss >= MISTAKE_CP:
        return "mistake"
    if cp_loss >= INACCURACY_CP:
        return "inaccuracy"
    return "fine"


def _non_pawn_material(board: chess.Board) -> int:
    total = 0
    for piece_type, value in _PIECE_VALUES.items():
        total += value * (
            len(board.pieces(piece_type, chess.WHITE))
            + len(board.pieces(piece_type, chess.BLACK))
        )
    return total


def game_phase(ply: int, fen_after: str, force_material_based: bool = False) -> str:
    if not force_material_based and ply <= OPENING_PLY_CUTOFF:
        return "opening"
    board = chess.Board(fen_after)
    if _non_pawn_material(board) <= ENDGAME_MATERIAL_THRESHOLD:
        return "endgame"
    return "middlegame"


def win_percent(cp: int) -> float:
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_accuracy(wp_before: float, wp_after: float) -> float:
    win_diff = max(0.0, wp_before - wp_after)
    if win_diff == 0:
        return 100.0
    raw = 103.1668 * math.exp(-0.04354 * win_diff) - 3.1669
    return max(0.0, min(100.0, raw))


from collections import Counter, defaultdict
from dataclasses import dataclass, field

from chesswhisper.openings import match_opening

PHASES = ("opening", "middlegame", "endgame")


@dataclass
class ErrorCounts:
    inaccuracy: int = 0
    mistake: int = 0
    blunder: int = 0

    def add(self, classification: str) -> None:
        if classification == "inaccuracy":
            self.inaccuracy += 1
        elif classification == "mistake":
            self.mistake += 1
        elif classification == "blunder":
            self.blunder += 1

    def merge(self, other: "ErrorCounts") -> None:
        self.inaccuracy += other.inaccuracy
        self.mistake += other.mistake
        self.blunder += other.blunder


@dataclass
class PlayerGameStats:
    acpl: float
    accuracy: float
    errors_by_phase: dict
    move_count: int = 0


@dataclass
class TimeManagementStats:
    available: bool
    low_time_error_rate: float | None = None
    normal_time_error_rate: float | None = None


LOW_TIME_THRESHOLD_SECONDS = 30


@dataclass
class _TimeBuckets:
    low_total: int = 0
    low_errors: int = 0
    normal_total: int = 0
    normal_errors: int = 0

    def merge(self, other: "_TimeBuckets") -> None:
        self.low_total += other.low_total
        self.low_errors += other.low_errors
        self.normal_total += other.normal_total
        self.normal_errors += other.normal_errors

    def to_stats(self) -> TimeManagementStats:
        if self.low_total == 0 and self.normal_total == 0:
            return TimeManagementStats(available=False)
        low_rate = (self.low_errors / self.low_total) if self.low_total else None
        normal_rate = (self.normal_errors / self.normal_total) if self.normal_total else None
        return TimeManagementStats(
            available=True, low_time_error_rate=low_rate, normal_time_error_rate=normal_rate
        )


def _time_buckets_for_color(evals, color) -> _TimeBuckets:
    buckets = _TimeBuckets()
    for ev in evals:
        if ev.color != color or ev.clock_seconds is None:
            continue
        cp_loss = max(0, ev.cp_before - ev.cp_after)
        is_error = classify_move(cp_loss) != "fine"
        if ev.clock_seconds < LOW_TIME_THRESHOLD_SECONDS:
            buckets.low_total += 1
            if is_error:
                buckets.low_errors += 1
        else:
            buckets.normal_total += 1
            if is_error:
                buckets.normal_errors += 1
    return buckets


@dataclass
class GameReport:
    game_index: int
    your_color: str
    opponent: str
    result: str
    your_stats: PlayerGameStats
    eco: str
    opening_name: str
    time_management: TimeManagementStats
    time_buckets: _TimeBuckets = field(default_factory=_TimeBuckets)
    top_mistakes: list = field(default_factory=list)


@dataclass
class OpeningStats:
    eco: str
    name: str
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    unknown: int = 0


@dataclass
class AggregateReport:
    player: str
    games: list
    overall_acpl: float
    overall_accuracy: float
    error_totals: dict
    openings: list
    time_management: TimeManagementStats


def detect_player(loaded_games) -> str:
    if not loaded_games:
        raise ValueError("Cannot detect player from an empty game list")

    counts = Counter()
    for lg in loaded_games:
        counts[lg.headers.get("White", "")] += 1
        counts[lg.headers.get("Black", "")] += 1
    counts.pop("", None)

    if not counts:
        raise ValueError("No player names found in PGN headers; pass --player explicitly")

    (top_name, top_count), *rest = counts.most_common()
    if rest and rest[0][1] == top_count:
        raise ValueError(
            f"Cannot auto-detect player: multiple names tied at {top_count} games. "
            "Pass --player explicitly."
        )
    if top_count < 1:
        raise ValueError("No repeated player name found; pass --player explicitly")
    return top_name


def _player_stats_for_color(evals, color, ply_to_phase) -> tuple[PlayerGameStats, list]:
    errors_by_phase = {p: ErrorCounts() for p in PHASES}
    cp_losses = []
    accuracies = []
    flagged = []

    for ev in evals:
        if ev.color != color:
            continue
        cp_loss = max(0, ev.cp_before - ev.cp_after)
        cp_losses.append(cp_loss)
        accuracies.append(move_accuracy(win_percent(ev.cp_before), win_percent(ev.cp_after)))

        classification = classify_move(cp_loss)
        if classification != "fine":
            phase = ply_to_phase[ev.ply]
            errors_by_phase[phase].add(classification)
            flagged.append((cp_loss, ev))

    acpl = sum(cp_losses) / len(cp_losses) if cp_losses else 0.0
    accuracy = sum(accuracies) / len(accuracies) if accuracies else 100.0
    stats = PlayerGameStats(
        acpl=acpl, accuracy=accuracy, errors_by_phase=errors_by_phase, move_count=len(cp_losses)
    )

    flagged.sort(key=lambda pair: pair[0], reverse=True)
    top_mistakes = [ev for _, ev in flagged[:5]]
    return stats, top_mistakes


def build_game_report(loaded, evals, player_name: str, eco_table) -> GameReport:
    white = loaded.headers.get("White", "")
    black = loaded.headers.get("Black", "")
    if player_name not in (white, black):
        raise ValueError(
            f"Player {player_name!r} not found in game {loaded.index} "
            f"(White={white!r}, Black={black!r})"
        )
    result_header = loaded.headers.get("Result", "*")

    your_color = "white" if player_name == white else "black"
    opponent = black if your_color == "white" else white

    if result_header == "1-0":
        result = "win" if your_color == "white" else "loss"
    elif result_header == "0-1":
        result = "win" if your_color == "black" else "loss"
    elif result_header == "1/2-1/2":
        result = "draw"
    else:
        result = "unknown"

    is_custom_start = loaded.headers.get("SetUp") == "1" or "FEN" in loaded.headers
    ply_to_phase = {
        ev.ply: game_phase(ev.ply, ev.fen_after, force_material_based=is_custom_start)
        for ev in evals
    }

    your_bool_color = your_color == "white"
    your_stats, top_mistakes = _player_stats_for_color(evals, your_bool_color, ply_to_phase)

    if is_custom_start:
        opening_eco, opening_name = "N/A", "Custom starting position"
    else:
        san_moves = [ev.move_san for ev in evals]
        opening = match_opening(san_moves, eco_table)
        opening_eco, opening_name = opening.eco, opening.name

    time_buckets = _time_buckets_for_color(evals, your_bool_color)

    return GameReport(
        game_index=loaded.index,
        your_color=your_color,
        opponent=opponent,
        result=result,
        your_stats=your_stats,
        eco=opening_eco,
        opening_name=opening_name,
        time_management=time_buckets.to_stats(),
        time_buckets=time_buckets,
        top_mistakes=top_mistakes,
    )


def build_aggregate_report(player: str, game_reports: list) -> AggregateReport:
    weighted_acpl_sum = 0.0
    weighted_accuracy_sum = 0.0
    total_moves = 0
    error_totals = {p: ErrorCounts() for p in PHASES}
    opening_map: dict = {}
    combined_time_buckets = _TimeBuckets()

    for gr in game_reports:
        move_count = gr.your_stats.move_count
        weighted_acpl_sum += gr.your_stats.acpl * move_count
        weighted_accuracy_sum += gr.your_stats.accuracy * move_count
        total_moves += move_count
        for phase in PHASES:
            error_totals[phase].merge(gr.your_stats.errors_by_phase[phase])
        combined_time_buckets.merge(gr.time_buckets)

        key = gr.eco
        if key not in opening_map:
            opening_map[key] = OpeningStats(eco=gr.eco, name=gr.opening_name)
        entry = opening_map[key]
        entry.games += 1
        if gr.result == "win":
            entry.wins += 1
        elif gr.result == "loss":
            entry.losses += 1
        elif gr.result == "draw":
            entry.draws += 1
        else:
            entry.unknown += 1

    overall_acpl = weighted_acpl_sum / total_moves if total_moves else 0.0
    overall_accuracy = weighted_accuracy_sum / total_moves if total_moves else 100.0

    return AggregateReport(
        player=player,
        games=game_reports,
        overall_acpl=overall_acpl,
        overall_accuracy=overall_accuracy,
        error_totals=error_totals,
        openings=sorted(opening_map.values(), key=lambda o: o.games, reverse=True),
        time_management=combined_time_buckets.to_stats(),
    )
