import json
import re

import chess

from chesswhisper.web.boards import board_cards, format_eval


def _fen_after(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.fen()


def _entry(**overrides):
    entry = {
        "game_index": 2, "opponent": "Bob", "your_color": "black", "ply": 2, "move_number": 1,
        "san": "f6", "uci": "f7f6", "best_uci": "e7e5", "best_san": "e5",
        "fen_before": _fen_after("e4"), "cp_before": -30, "cp_after": -420, "cp_loss": 390,
        "classification": "blunder",
    }
    entry.update(overrides)
    return entry


def _a1_position(svg):
    match = re.search(r'<rect x="(\d+)" y="(\d+)"[^>]*class="square [a-z]+ a1"', svg)
    return int(match.group(1)), int(match.group(2))


def test_card_captions_come_from_the_entry():
    [card] = board_cards(json.dumps([_entry()]))

    assert card.game_number == 3
    assert card.opponent == "Bob"
    assert card.move_label == "1... f6"
    assert card.classification == "blunder"
    assert card.best_san == "e5"
    assert (card.eval_before, card.eval_after) == ("−0.3", "−4.2")


def test_white_moves_are_labelled_without_the_ellipsis():
    [card] = board_cards(json.dumps([_entry(your_color="white", ply=1, san="e4", uci="e2e4",
                                            best_uci=None, best_san=None, fen_before=chess.STARTING_FEN)]))

    assert card.move_label == "1. e4"


def test_board_draws_played_move_red_and_engine_move_green():
    [card] = board_cards(json.dumps([_entry()]))

    assert card.svg.startswith("<svg")
    assert "#c0392b" in card.svg
    assert "#2e8b57" in card.svg


def test_only_the_played_arrow_when_engine_agrees_or_has_no_move():
    [same] = board_cards(json.dumps([_entry(best_uci="f7f6", best_san="f6")]))
    [none] = board_cards(json.dumps([_entry(best_uci=None, best_san=None)]))

    for card in (same, none):
        assert "#c0392b" in card.svg
        assert "#2e8b57" not in card.svg
        assert card.best_san is None


def test_board_is_seen_from_the_players_side():
    [black] = board_cards(json.dumps([_entry()]))
    [white] = board_cards(json.dumps([_entry(your_color="white")]))

    # a1 sits bottom-left from White's side and top-right from Black's.
    white_x, white_y = _a1_position(white.svg)
    black_x, black_y = _a1_position(black.svg)
    assert white_y > black_y
    assert white_x < black_x


def test_missing_or_unreadable_data_gives_no_cards():
    assert board_cards(None) == []
    assert board_cards("") == []
    assert board_cards("[]") == []
    assert board_cards("not json") == []
    assert board_cards('{"not": "a list"}') == []
    assert board_cards(json.dumps([_entry(fen_before="not a fen")])) == []
    assert board_cards(json.dumps([_entry(uci="zz99")])) == []
    assert board_cards(json.dumps([{"san": "e4"}])) == []


def test_format_eval_shows_pawns_and_mates_from_the_players_side():
    assert format_eval(120) == "+1.2"
    assert format_eval(-340) == "−3.4"
    assert format_eval(0) == "+0.0"
    assert format_eval(99_997) == "#3"
    assert format_eval(-99_998) == "−#2"
