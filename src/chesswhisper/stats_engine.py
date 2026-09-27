import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import chess

from chesswhisper.chances import Chance, ChanceStats, chance_stats, opponent_chances
from chesswhisper.endgames import EndgameEntry, EndgameStats, endgame_entry, endgame_stats
from chesswhisper.form import RecentForm, game_datetime, recent_form
from chesswhisper.motifs import MOTIFS, OTHER, error_motif
from chesswhisper.openings import match_opening
from chesswhisper.theory import TheoryExit, TheoryStats, book_prefixes, theory_exit, theory_stats

INACCURACY_CP = 50
MISTAKE_CP = 100
BLUNDER_CP = 300
OPENING_PLY_CUTOFF = 20
ENDGAME_MATERIAL_THRESHOLD = 20

_PIECE_VALUES = {chess.QUEEN: 9, chess.ROOK: 5, chess.BISHOP: 3, chess.KNIGHT: 3}


# Evaluations are capped at +/-10 pawns before measuring a move's loss, as
# Lichess does: past that the game is decided either way, and mate scores
# (encoded as +/-100_000) would otherwise turn "missed a mate but still a
# queen up" into a ~99,000 cp loss that swamps the whole game's ACPL.
EVAL_CLAMP_CP = 1000


def _clamp_eval(cp: int) -> int:
    return max(-EVAL_CLAMP_CP, min(EVAL_CLAMP_CP, cp))


def cp_loss(ev) -> int:
    """Centipawns the mover gave up with this move, from clamped evaluations."""
    return max(0, _clamp_eval(ev.cp_before) - _clamp_eval(ev.cp_after))


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
        is_error = classify_move(cp_loss(ev)) != "fine"
        if ev.clock_seconds < LOW_TIME_THRESHOLD_SECONDS:
            buckets.low_total += 1
            if is_error:
                buckets.low_errors += 1
        else:
            buckets.normal_total += 1
            if is_error:
                buckets.normal_errors += 1
    return buckets


@dataclass(frozen=True)
class MoveRecord:
    """One of the player's moves, as the error-pattern breakdowns need it."""

    ply: int
    move_number: int
    eval_before: int  # clamped, from the player's side
    cp_loss: int
    classification: str
    # For mistakes and blunders: the tactic behind it (see motifs.py) and
    # whether the player allowed it or missed it; None otherwise.
    motif: str | None = None
    motif_side: str | None = None


TIME_CLASSES = ("bullet", "blitz", "rapid", "classical", "correspondence", "unknown")


def time_class(time_control: str | None) -> str:
    """Lichess's speed category for a PGN TimeControl header.

    The estimated duration is the base time plus 40 increments: under 3
    minutes is bullet, under 8 blitz, under 25 rapid, else classical. "-" and
    chess.com's daily "1/86400" form are correspondence.
    """
    if not time_control:
        return "unknown"
    if time_control == "-" or "/" in time_control:
        return "correspondence"
    base, _, increment = time_control.partition("+")
    try:
        estimated = int(base) + 40 * int(increment or 0)
    except ValueError:
        return "unknown"
    if estimated < 180:
        return "bullet"
    if estimated < 480:
        return "blitz"
    if estimated < 1500:
        return "rapid"
    return "classical"


def _elo(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


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
    time_class: str = "unknown"
    your_elo: int | None = None
    opponent_elo: int | None = None
    moves: list[MoveRecord] = field(default_factory=list)
    white: str = ""
    black: str = ""
    # Every ply's evaluation, for replaying the game (see timeline.py).
    evals: list = field(default_factory=list)
    # Where the game left opening theory (see theory.py); None for set-up positions.
    theory: TheoryExit | None = None
    # The opponent's mistakes and blunders and whether they were punished (see chances.py).
    chances: list[Chance] = field(default_factory=list)
    # The first endgame position and how it went (see endgames.py); None without an endgame.
    endgame: EndgameEntry | None = None
    # When the game was played, from its PGN headers (see form.py); None if they don't say.
    played_at: datetime | None = None


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
class SegmentStats:
    """Results and move quality over a subset of the games."""

    label: str
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    acpl: float = 0.0
    accuracy: float = 100.0


@dataclass
class PatternBucket:
    """How often the player's moves in one bucket were mistakes or blunders."""

    label: str
    moves: int = 0
    errors: int = 0

    @property
    def error_rate(self) -> float | None:
        return self.errors / self.moves if self.moves else None


MOVE_RANGES = (("1-10", 1, 10), ("11-20", 11, 20), ("21-30", 21, 30), ("31-40", 31, 40), ("41+", 41, None))
EVAL_STATES = ("winning", "equal", "losing")
# Two pawns either way: past that the player is clearly better or worse.
DECISIVE_EVAL_CP = 200
OPPONENT_STRENGTHS = ("stronger", "similar", "weaker")
SIMILAR_RATING_GAP = 100
_ERROR_CLASSES = ("mistake", "blunder")


@dataclass
class MotifStat:
    """How many of the player's mistakes and blunders came from one motif, by side."""

    motif: str
    allowed: int = 0
    missed: int = 0
    unsided: int = 0  # "other": no recognizable tactic either way

    @property
    def total(self) -> int:
        return self.allowed + self.missed + self.unsided


@dataclass
class AggregateReport:
    player: str
    games: list
    overall_acpl: float
    overall_accuracy: float
    error_totals: dict
    openings: list
    time_management: TimeManagementStats
    by_time_class: list[SegmentStats] = field(default_factory=list)
    by_opponent_strength: list[SegmentStats] = field(default_factory=list)
    move_ranges: list[PatternBucket] = field(default_factory=list)
    eval_states: list[PatternBucket] = field(default_factory=list)
    motifs: list[MotifStat] = field(default_factory=list)
    opening_theory: list[TheoryStats] = field(default_factory=list)
    chances: ChanceStats = field(default_factory=lambda: ChanceStats(total=0, punished=0, missed_by_motif=[]))
    endgames: list[EndgameStats] = field(default_factory=list)
    recent_form: RecentForm | None = None


def detect_player(loaded_games) -> str:
    if not loaded_games:
        raise ValueError("Cannot detect player from an empty game list")

    counts: Counter[str] = Counter()
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


def _move_number(ev) -> int:
    if ev.fen_before:
        return chess.Board(ev.fen_before).fullmove_number
    return (ev.ply + 1) // 2


def _move_records(evals, color) -> list[MoveRecord]:
    records = []
    for index, ev in enumerate(evals):
        if ev.color != color:
            continue
        classification = classify_move(cp_loss(ev))
        motif = side = None
        if classification in _ERROR_CLASSES:
            next_ev = evals[index + 1] if index + 1 < len(evals) else None
            motif, side = error_motif(ev, next_ev)
        records.append(
            MoveRecord(
                ply=ev.ply,
                move_number=_move_number(ev),
                eval_before=_clamp_eval(ev.cp_before),
                cp_loss=cp_loss(ev),
                classification=classification,
                motif=motif,
                motif_side=side,
            )
        )
    return records


def _player_stats_for_color(evals, color, ply_to_phase) -> tuple[PlayerGameStats, list]:
    errors_by_phase = {p: ErrorCounts() for p in PHASES}
    cp_losses = []
    accuracies = []
    flagged = []

    for ev in evals:
        if ev.color != color:
            continue
        loss = cp_loss(ev)
        cp_losses.append(loss)
        accuracies.append(move_accuracy(win_percent(ev.cp_before), win_percent(ev.cp_after)))

        classification = classify_move(loss)
        if classification != "fine":
            phase = ply_to_phase[ev.ply]
            errors_by_phase[phase].add(classification)
            flagged.append((loss, ev))

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
    white_elo, black_elo = _elo(loaded.headers.get("WhiteElo")), _elo(loaded.headers.get("BlackElo"))

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
        time_class=time_class(loaded.headers.get("TimeControl")),
        your_elo=white_elo if your_bool_color else black_elo,
        opponent_elo=black_elo if your_bool_color else white_elo,
        moves=_move_records(evals, your_bool_color),
        white=white,
        black=black,
        evals=list(evals),
        theory=theory_exit(evals, your_bool_color, book_prefixes(eco_table), custom_start=is_custom_start),
        chances=opponent_chances(evals, your_bool_color),
        endgame=endgame_entry(evals, your_bool_color, result, custom_start=is_custom_start),
        played_at=game_datetime(loaded.headers),
    )


def _segment(label: str, reports: list) -> SegmentStats:
    total_moves = sum(r.your_stats.move_count for r in reports)
    return SegmentStats(
        label=label,
        games=len(reports),
        wins=sum(r.result == "win" for r in reports),
        losses=sum(r.result == "loss" for r in reports),
        draws=sum(r.result == "draw" for r in reports),
        acpl=sum(r.your_stats.acpl * r.your_stats.move_count for r in reports) / total_moves if total_moves else 0.0,
        accuracy=(
            sum(r.your_stats.accuracy * r.your_stats.move_count for r in reports) / total_moves
            if total_moves else 100.0
        ),
    )


def _opponent_strength(report) -> str | None:
    if report.your_elo is None or report.opponent_elo is None:
        return None
    gap = report.opponent_elo - report.your_elo
    if gap >= SIMILAR_RATING_GAP:
        return "stronger"
    if gap <= -SIMILAR_RATING_GAP:
        return "weaker"
    return "similar"


def _eval_state(eval_before: int) -> str:
    if eval_before >= DECISIVE_EVAL_CP:
        return "winning"
    if eval_before <= -DECISIVE_EVAL_CP:
        return "losing"
    return "equal"


def _move_range(move_number: int) -> str:
    for label, low, high in MOVE_RANGES:
        if move_number >= low and (high is None or move_number <= high):
            return label
    return MOVE_RANGES[0][0]


def _pattern_buckets(labels, moves, bucket_of) -> list[PatternBucket]:
    buckets = {label: PatternBucket(label=label) for label in labels}
    for move in moves:
        bucket = buckets[bucket_of(move)]
        bucket.moves += 1
        if move.classification in _ERROR_CLASSES:
            bucket.errors += 1
    return list(buckets.values())


def _breakdowns(game_reports: list) -> dict:
    by_time = [
        _segment(label, [r for r in game_reports if r.time_class == label])
        for label in TIME_CLASSES
    ]
    by_strength = [
        _segment(label, [r for r in game_reports if _opponent_strength(r) == label])
        for label in OPPONENT_STRENGTHS
    ]
    moves = [move for r in game_reports for move in r.moves]
    return {
        "by_time_class": [seg for seg in by_time if seg.games],
        "by_opponent_strength": [seg for seg in by_strength if seg.games],
        "move_ranges": _pattern_buckets([label for label, _, _ in MOVE_RANGES], moves,
                                        lambda m: _move_range(m.move_number)),
        "eval_states": _pattern_buckets(EVAL_STATES, moves, lambda m: _eval_state(m.eval_before)),
    }


def motif_stats(game_reports: list) -> list[MotifStat]:
    """Motif counts over the player's mistakes and blunders: most frequent first, "other" last."""
    stats: dict[str, MotifStat] = {}
    for report in game_reports:
        for move in report.moves:
            if move.motif is None:
                continue
            stat = stats.setdefault(move.motif, MotifStat(move.motif))
            if move.motif_side == "allowed":
                stat.allowed += 1
            elif move.motif_side == "missed":
                stat.missed += 1
            else:
                stat.unsided += 1
    order = {motif: index for index, motif in enumerate((*MOTIFS, OTHER))}
    return sorted(stats.values(), key=lambda stat: (stat.motif == OTHER, -stat.total, order[stat.motif]))


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
        motifs=motif_stats(game_reports),
        opening_theory=theory_stats(game_reports),
        chances=chance_stats(game_reports),
        endgames=endgame_stats(game_reports),
        recent_form=recent_form(game_reports),
        **_breakdowns(game_reports),
    )


CRITICAL_MOVES_LIMIT = 12
_CRITICAL_CLASSES = ("blunder", "mistake")


def _best_san(fen_before: str, best_uci: str | None) -> str | None:
    if best_uci is None:
        return None
    try:
        board = chess.Board(fen_before)
        move = chess.Move.from_uci(best_uci)
    except ValueError:
        return None
    if move not in board.legal_moves:
        return None
    return board.san(move)


def _motif_fields(report, ply: int) -> dict:
    move = next((m for m in report.moves if m.ply == ply), None)
    missed_chance = any(c.reply_ply == ply and not c.punished for c in report.chances)
    return {
        "motif": move.motif if move else None,
        "motif_side": move.motif_side if move else None,
        "missed_chance": missed_chance,
    }


def critical_moves(game_reports: list, limit: int = CRITICAL_MOVES_LIMIT) -> list[dict]:
    """The analysis's costliest blunders and mistakes, with what a board diagram needs.

    Draws from each game's top_mistakes (already the player's flagged moves,
    worst first), keeps blunders and mistakes that carry their starting
    position, and ranks them across all games by centipawn loss.
    """
    candidates = []
    for report in game_reports:
        for ev in report.top_mistakes:
            if ev.fen_before is None or ev.move_uci is None:
                continue
            loss = cp_loss(ev)
            classification = classify_move(loss)
            if classification in _CRITICAL_CLASSES:
                candidates.append((loss, report, ev, classification))
    candidates.sort(key=lambda c: (-c[0], c[1].game_index, c[2].ply))

    return [
        {
            "game_index": report.game_index,
            "opponent": report.opponent,
            "your_color": report.your_color,
            "ply": ev.ply,
            # From the position, not the ply: games set up from a FEN start mid-game.
            "move_number": chess.Board(ev.fen_before).fullmove_number,
            "san": ev.move_san,
            "uci": ev.move_uci,
            "best_uci": ev.best_move_uci,
            "best_san": _best_san(ev.fen_before, ev.best_move_uci),
            "best_line": ev.best_line_uci,
            "fen_before": ev.fen_before,
            "cp_before": ev.cp_before,
            "cp_after": ev.cp_after,
            "cp_loss": loss,
            "classification": classification,
            **_motif_fields(report, ev.ply),
        }
        for loss, report, ev, classification in candidates[:limit]
    ]
