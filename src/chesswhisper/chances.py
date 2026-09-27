"""The opponent's mistakes and blunders, and whether the player made them pay.

After an opponent's mistake or blunder, the player's very next move decides
whether the gift is kept. A reply that is itself a mistake or blunder gives
most of it back: a missed chance. The tactic the engine wanted instead
(see motifs.py) says what there was to find.
"""

from __future__ import annotations

from dataclasses import dataclass

from chesswhisper.motifs import MATE_THRESHOLD, MOTIFS, OTHER, tactic_motif

_ERROR_CLASSES = ("mistake", "blunder")


@dataclass(frozen=True)
class Chance:
    ply: int  # the opponent's error
    classification: str  # "mistake" | "blunder"
    gain: int  # centipawns the opponent gave away
    reply_ply: int
    reply_loss: int  # centipawns the player's reply gave back
    punished: bool
    motif: str  # the tactic in the engine's reply, or "other"
    reply_san: str


def opponent_chances(evals, player_color: bool) -> list[Chance]:
    # Imported here: stats_engine imports this module.
    from chesswhisper.stats_engine import classify_move, cp_loss

    chances = []
    for index, ev in enumerate(evals[:-1]):
        if ev.color == player_color:
            continue
        gain = cp_loss(ev)
        classification = classify_move(gain)
        if classification not in _ERROR_CLASSES:
            continue
        reply = evals[index + 1]
        if reply.color != player_color:
            continue
        reply_loss = cp_loss(reply)
        punished = classify_move(reply_loss) not in _ERROR_CLASSES
        motif = None
        if not punished and reply.best_move_uci and reply.best_move_uci != reply.move_uci:
            motif = tactic_motif(reply.fen_before, reply.best_move_uci, forced_mate=reply.cp_before >= MATE_THRESHOLD)
        chances.append(Chance(
            ply=ev.ply,
            classification=classification,
            gain=gain,
            reply_ply=reply.ply,
            reply_loss=reply_loss,
            punished=punished,
            motif=motif or OTHER,
            reply_san=reply.move_san,
        ))
    return chances


@dataclass(frozen=True)
class ChanceStats:
    total: int
    punished: int
    missed_by_motif: list[tuple[str, int]]  # most missed first, "other" last

    @property
    def punish_rate(self) -> float | None:
        return self.punished / self.total if self.total else None


def chance_stats(game_reports: list) -> ChanceStats:
    chances = [chance for report in game_reports for chance in report.chances]
    missed: dict[str, int] = {}
    for chance in chances:
        if not chance.punished:
            missed[chance.motif] = missed.get(chance.motif, 0) + 1
    order = {motif: index for index, motif in enumerate((*MOTIFS, OTHER))}
    ranked = sorted(missed.items(), key=lambda item: (item[0] == OTHER, -item[1], order[item[0]]))
    return ChanceStats(total=len(chances), punished=sum(1 for c in chances if c.punished), missed_by_motif=ranked)
