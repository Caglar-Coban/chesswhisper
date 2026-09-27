"""Name the tactic behind a mistake: what it allowed, or what it missed.

Everything here is static inspection with python-chess of moves the engine
already chose: the refutation (the engine's best reply in the position the
mistake created, stored on the next ply) and the engine's preferred move in
the position the mistake was played from. No extra engine calls.
"""

from __future__ import annotations

import chess

# Mate scores are stored as 100000 minus the distance to mate (see engine_analyzer).
MATE_THRESHOLD = 99_000

# Checked in this order; the first that matches names the move.
MOTIFS = ("back_rank", "mate", "promotion", "fork", "pin", "hanging")
OTHER = "other"
SIDES = ("allowed", "missed")

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}
MINOR_PIECE_VALUE = 3


def _parse(fen: str | None, move_uci: str | None) -> tuple[chess.Board, chess.Move] | None:
    if not fen or not move_uci:
        return None
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        return None
    if move not in board.legal_moves:
        return None
    return board, move


def _value(board: chess.Board, square: chess.Square) -> int:
    piece = board.piece_at(square)
    return PIECE_VALUES[piece.piece_type] if piece else 0


def _is_back_rank_mate(after: chess.Board, move: chess.Move) -> bool:
    mover_piece = after.piece_at(move.to_square)
    if mover_piece is None or mover_piece.piece_type not in (chess.ROOK, chess.QUEEN):
        return False
    king = after.king(after.turn)
    if king is None:
        return False
    back_rank = 0 if after.turn == chess.WHITE else 7
    return chess.square_rank(king) == back_rank and chess.square_rank(move.to_square) == back_rank


def _can_be_taken_for_free(after: chess.Board, square: chess.Square) -> bool:
    """Whether the side to move can capture the piece on *square* without losing it back."""
    owner = not after.turn
    return after.is_attacked_by(after.turn, square) and not after.is_attacked_by(owner, square)


def _is_safe(after: chess.Board, square: chess.Square) -> bool:
    """Whether the piece that just moved to *square* can stay there: not free, not won by a cheaper piece."""
    if _can_be_taken_for_free(after, square):
        return False
    value = _value(after, square)
    # The king is left out: it can only take an undefended piece, which the check above covers.
    return not any(
        piece.piece_type != chess.KING and PIECE_VALUES[piece.piece_type] < value
        for piece in (after.piece_at(attacker) for attacker in after.attackers(after.turn, square))
        if piece is not None
    )


def _is_fork(after: chess.Board, move: chess.Move) -> bool:
    square = move.to_square
    forker_value = _value(after, square)
    enemy = after.turn
    # A king "attacking" two pieces is too common to name, and it is worth nothing here.
    if after.piece_type_at(square) == chess.KING or not _is_safe(after, square):
        return False
    targets = 0
    for target in after.attacks(square):
        piece = after.piece_at(target)
        if piece is None or piece.color != enemy:
            continue
        value = PIECE_VALUES[piece.piece_type]
        undefended = not after.is_attacked_by(enemy, target)
        if piece.piece_type == chess.KING or value > forker_value or (undefended and value >= MINOR_PIECE_VALUE):
            targets += 1
    return targets >= 2


def _is_pin(after: chess.Board, move: chess.Move) -> bool:
    enemy = after.turn
    if not _is_safe(after, move.to_square):
        return False
    for square in chess.SquareSet(after.occupied_co[enemy]):
        piece = after.piece_at(square)
        if piece is None or piece.piece_type == chess.KING or PIECE_VALUES[piece.piece_type] < MINOR_PIECE_VALUE:
            continue
        if after.is_pinned(enemy, square) and move.to_square in after.pin(enemy, square):
            return True
    return False


def _wins_material(board: chess.Board, move: chess.Move, after: chess.Board) -> bool:
    if not board.is_capture(move) or board.is_en_passant(move):
        return False
    captured = _value(board, move.to_square)
    capturer = _value(board, move.from_square)
    if captured > capturer:
        return True
    return captured > 0 and not after.is_attacked_by(after.turn, move.to_square)


def tactic_motif(fen: str | None, move_uci: str | None, forced_mate: bool = False) -> str | None:
    """The motif of playing *move_uci* in *fen*, or None for a quiet or unrecognized move."""
    parsed = _parse(fen, move_uci)
    if parsed is None:
        return None
    board, move = parsed
    after = board.copy(stack=False)
    after.push(move)

    if after.is_checkmate():
        return "back_rank" if _is_back_rank_mate(after, move) else "mate"
    if move.promotion is not None:
        return "promotion"
    if _is_fork(after, move):
        return "fork"
    if _is_pin(after, move):
        return "pin"
    if _wins_material(board, move, after):
        return "hanging"
    # A forced mate further down the line, when the move itself shows no simpler tactic.
    if forced_mate:
        return "mate"
    return None


def _is_plain_recapture(ev, refutation_uci: str) -> bool:
    """The refutation just takes back on the square where the player captured, for no more."""
    parsed = _parse(ev.fen_before, ev.move_uci)
    reply = _parse(ev.fen_after, refutation_uci)
    if parsed is None or reply is None:
        return False
    board, move = parsed
    after, refutation = reply
    if not board.is_capture(move) or refutation.to_square != move.to_square:
        return False
    return _value(after, refutation.to_square) <= _value(board, move.to_square)


def error_motif(ev, next_ev) -> tuple[str, str | None]:
    """(motif, "allowed" | "missed") for a mistake, or ("other", None) if none is recognized.

    *next_ev* is the following ply's evaluation, whose best move is the
    refutation; it is ignored unless it starts from the position *ev* left.
    """
    if next_ev is not None and next_ev.fen_before == ev.fen_after and next_ev.best_move_uci:
        refutation = next_ev.best_move_uci
        motif = tactic_motif(ev.fen_after, refutation, forced_mate=ev.cp_after <= -MATE_THRESHOLD)
        if motif == "hanging" and _is_plain_recapture(ev, refutation):
            motif = None
        if motif is not None:
            return motif, "allowed"

    if ev.best_move_uci and ev.best_move_uci != ev.move_uci:
        forced = ev.cp_before >= MATE_THRESHOLD and ev.cp_after < MATE_THRESHOLD
        motif = tactic_motif(ev.fen_before, ev.best_move_uci, forced_mate=forced)
        if motif is not None:
            return motif, "missed"
    return OTHER, None
