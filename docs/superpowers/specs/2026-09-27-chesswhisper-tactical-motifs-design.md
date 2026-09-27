# Tactical motifs (0.6.0)

## Goal

Reports say *when* a player goes wrong (phase, move range, clock) but not
*what kind* of mistake it is. This release labels each mistake and blunder
with the tactic behind it, so a report can say "7 of your 18 mistakes left
a piece hanging" and the next release can filter puzzles by motif.

Everything is computed locally from data each analysis already has; no new
engine calls and no LLM.

## What a mistake's motif means

Every analyzed move stores the position before it, the move played, the
engine's preferred move, and the evaluations before and after. The next
ply's entry stores the engine's preferred reply in the position the
mistake created, which is the refutation. So each mistake can be seen from
two sides:

- **Allowed**: what the refutation does to the player (the opponent's
  tactic the mistake permitted), judged in the position after the mistake.
- **Missed**: what the engine's preferred move would have done (the
  player's tactic they did not play), judged in the position before it.

A mistake gets one motif and one side: the allowed motif if one is found,
else the missed one, else `other` (positional or too deep to name
statically).

## Motifs

Checked in this order for a move on a board (the first match wins):

1. `back_rank`: the move mates with a rook or queen on the defending king's
   back rank, with the king on that rank.
2. `mate`: the move mates.
3. `promotion`: the move promotes.
4. `fork`: after the move, the moved piece (not the king) attacks at least
   two enemy pieces that are the king or worth more than it (or are
   undefended and worth at least a minor piece), and it is safe: it cannot
   be taken for free or by a cheaper piece.
5. `pin`: after the move, the moved piece, safe as above, pins an enemy
   piece worth at least a minor piece to its king.
6. `hanging`: the move captures a piece worth more than the capturer, or
   one no enemy piece defends. For the allowed side, a recapture on the
   square where the player just captured, taking no more than the player
   took, is an ordinary exchange and does not count.
7. `mate` again, as a fallback: nothing above matched but the evaluation
   says the side playing the move has a forced mate (mate scores are stored
   as 100000 minus the distance). A rook left hanging that loses by force
   is still `hanging`.

Checked against the bundled demo games with real Stockfish; the rules for
king forks, unsafe pinners and the late mate fallback came from labels
that were wrong there.

Piece values: pawn 1, knight 3, bishop 3, rook 5, queen 9.

## Components

- `src/chesswhisper/motifs.py`: `tactic_motif(fen, move_uci, forced_mate)`
  and `error_motif(ev, next_ev) -> (motif, side)`, pure python-chess.
- `stats_engine`: `MoveRecord` gains `motif` and `motif_side` for mistakes
  and blunders; `AggregateReport.motifs` holds `MotifStat(motif, allowed,
  missed)` rows, most frequent first; critical moves carry `motif` and
  `motif_side`.
- `insights`: a `motif` finding when one motif and side accounts for at
  least 5 mistakes and 30% of them.
- `report_writer`: a "Tactical patterns" section (motif, allowed, missed)
  after the error patterns, shown when there are mistakes.
- LLM prompt: the same counts.
- Web: each critical-move board shows its motif ("Allowed: fork").

Records saved before this release have no motif on their critical moves;
the boards simply show none.

## Testing

Motif detection per motif on hand-built positions, for both sides, the
exchange exception, the forced-mate hint, priority when several match,
a move that is not legal in the position, and a last-ply mistake with no
refutation. Aggregation counts, the finding thresholds, the report
section in both languages, the prompt text, and a board card label.
