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


def game_phase(ply: int, fen_after: str) -> str:
    if ply <= OPENING_PLY_CUTOFF:
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
