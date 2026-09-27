import chess

from chesswhisper.openings import load_eco_table
from chesswhisper.repertoire import MAX_PLIES, build_tree, explore

ECO = load_eco_table()


def _timeline(sans, color="white", result="win", white="Alice", black="Bob", errors=(), start_fen=None):
    board = chess.Board()
    moves = []
    for ply, san in enumerate(sans, start=1):
        move = board.parse_san(san)
        moves.append({
            "ply": ply, "san": san, "uci": move.uci(), "color": "white" if board.turn else "black",
            "classification": "mistake" if ply in errors else None,
        })
        board.push(move)
    return {
        "white": white, "black": black, "your_color": color, "result": result,
        "start_fen": start_fen or chess.STARTING_FEN, "moves": moves,
    }


def _uci(*sans):
    board, out = chess.Board(), []
    for san in sans:
        move = board.parse_san(san)
        out.append(move.uci())
        board.push(move)
    return out


def test_the_start_lists_first_moves_by_how_often_they_were_played():
    games = [
        _timeline(["e4", "e5"], result="win", black="B1"),
        _timeline(["e4", "c5"], result="loss", black="B2"),
        _timeline(["d4", "d5"], result="draw", black="B3"),
    ]
    position = explore(build_tree(games, "white"), "white", [], ECO)

    assert position.games == 3
    assert position.player_to_move
    assert [(m.san, m.games, m.score_pct) for m in position.moves] == [("e4", 2, 50.0), ("d4", 1, 50.0)]
    assert all(m.in_book for m in position.moves)


def test_a_line_leads_to_its_position_with_the_opponents_replies():
    games = [_timeline(["e4", "e5", "Nf3"], black="B1"), _timeline(["e4", "c5", "Nf3"], black="B2")]

    position = explore(build_tree(games, "white"), "white", _uci("e4"), ECO)

    assert position.line_san == ["e4"]
    assert position.fen == chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1").fen()
    assert not position.player_to_move
    assert {m.san for m in position.moves} == {"e5", "c5"}
    # Error rates are only given for the player's own moves.
    assert all(m.error_pct is None for m in position.moves)
    assert position.opening is not None


def test_error_rate_and_book_status_of_the_players_moves():
    games = [
        _timeline(["e4", "e5", "Qh5"], black="B1", errors=(3,)),
        _timeline(["e4", "e5", "Qh5"], black="B2"),
        _timeline(["e4", "e5", "Nf3"], black="B3"),
    ]
    position = explore(build_tree(games, "white"), "white", _uci("e4", "e5"), ECO)

    by_san = {m.san: m for m in position.moves}
    assert by_san["Qh5"].error_pct == 50.0
    assert by_san["Nf3"].error_pct == 0.0
    assert by_san["Nf3"].in_book


def test_only_the_chosen_colour_counts_and_duplicates_once():
    game = _timeline(["e4", "e5"])
    black_game = _timeline(["d4", "Nf6"], color="black", white="Carol", black="Alice")
    tree = build_tree([game, dict(game), black_game], "white")

    assert explore(tree, "white", [], ECO).games == 1
    black = explore(build_tree([game, black_game], "black"), "black", [], ECO)
    assert [m.san for m in black.moves] == ["d4"]
    assert not black.player_to_move


def test_games_from_a_set_position_are_left_out_and_depth_is_capped():
    odd = _timeline(["e4"], start_fen="8/8/8/8/8/8/4K3/4k3 w - - 0 1")
    long = _timeline(["Nf3", "Nf6", "Ng1", "Ng8"] * 8)
    tree = build_tree([odd, long], "white")

    assert explore(tree, "white", [], ECO).games == 1
    line = long["moves"]
    assert explore(tree, "white", [m["uci"] for m in line[:MAX_PLIES]], ECO).moves == []


def test_a_line_the_games_never_reached_gives_none():
    tree = build_tree([_timeline(["e4", "e5"])], "white")

    assert explore(tree, "white", _uci("d4"), ECO) is None


def test_unknown_results_count_as_games_without_a_score():
    position = explore(build_tree([_timeline(["e4"], result="unknown")], "white"), "white", [], ECO)

    assert position.moves[0].games == 1
    assert position.moves[0].score_pct is None
