import os

import pytest

from chess_analyzer.pgn_import import parse_pgn_file


def get_fixture_path(filename: str) -> str:
    return os.path.join(os.path.dirname(__file__), "fixtures", filename)


def test_import_real_lichess_pgn() -> None:
    path = get_fixture_path("lichess_real.pgn")
    games = list(parse_pgn_file(path))
    assert len(games) == 1
    game = games[0]
    assert game.white == "Player1"
    assert game.black == "Player2"
    assert game.result == "1-0"
    assert game.event == "Rated Blitz game"
    assert game.site == "https://lichess.org/test"
    assert game.date == "2023.01.01"
    assert game.white_elo == 1500
    assert game.black_elo == 1400
    assert game.time_control == "300+0"
    assert game.eco == "C20"

    assert len(game.moves) == 3
    assert game.moves[0].ply == 1
    assert game.moves[0].san == "e4"
    assert game.moves[1].ply == 2
    assert game.moves[1].san == "e5"
    assert game.moves[2].ply == 3
    assert game.moves[2].san == "Bc4"


def test_import_real_chesscom_pgn() -> None:
    path = get_fixture_path("chesscom_real.pgn")
    games = list(parse_pgn_file(path))
    assert len(games) == 1
    game = games[0]
    assert game.white == "Player3"
    assert game.black == "Player4"
    assert game.result == "0-1"
    assert game.event == "Live Chess"
    assert game.site == "Chess.com"
    assert game.date == "2023.01.02"
    assert game.white_elo == 1600
    assert game.black_elo == 1700
    assert game.time_control == "180+2"
    assert game.eco == "A00"

    assert len(game.moves) == 3


def test_import_missing_optional_headers_become_none() -> None:
    path = get_fixture_path("missing_optional.pgn")
    games = list(parse_pgn_file(path))
    assert len(games) == 1
    game = games[0]
    assert game.white == "Player5"
    assert game.black == "Player6"
    assert game.result == "1/2-1/2"
    assert game.event is None
    assert game.site is None
    assert game.date is None
    assert game.white_elo is None
    assert game.black_elo is None
    assert game.time_control is None
    assert game.eco is None


def test_import_multi_game_pgn_success() -> None:
    path = get_fixture_path("multi_game.pgn")
    games = list(parse_pgn_file(path))
    assert len(games) == 3
    assert games[0].white == "W1"
    assert games[1].white == "W2"
    assert games[2].white == "W3"


def test_import_skips_invalid_game_in_multi_file(caplog: pytest.LogCaptureFixture) -> None:
    path = get_fixture_path("multi_game_invalid_middle.pgn")
    games = list(parse_pgn_file(path))
    # Should yield games 1 and 3 (from W1 and W3)
    assert len(games) == 2
    assert games[0].white == "W1"
    assert games[1].white == "W3"

    # Check warning
    warnings = [rec.message for rec in caplog.records if rec.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Missing mandatory header White" in warnings[0]
    w_msg = warnings[0].lower()
    assert "jogo 2" in w_msg or "game 2" in w_msg or "index 1" in w_msg


def test_import_empty_file_yields_no_games() -> None:
    path = get_fixture_path("empty.pgn")
    games = list(parse_pgn_file(path))
    assert len(games) == 0


def test_import_malformed_pgn_raises_clear_error() -> None:
    path = get_fixture_path("malformed.pgn")
    with pytest.raises(ValueError, match="não reconhecível como PGN"):
        list(parse_pgn_file(path))


def test_import_binary_file_raises_clear_error() -> None:
    path = get_fixture_path("binary.pgn")
    with pytest.raises(ValueError, match="não reconhecível como PGN"):
        list(parse_pgn_file(path))


def test_import_ignores_nags_and_comments() -> None:
    path = get_fixture_path("nags_comments.pgn")
    games = list(parse_pgn_file(path))
    assert len(games) == 1
    game = games[0]
    assert len(game.moves) == 3
    assert game.moves[0].san == "e4"
    assert game.moves[1].san == "e5"
    assert game.moves[2].san == "Nf3"


def test_import_ignores_nested_variations() -> None:
    path = get_fixture_path("nested_variations.pgn")
    games = list(parse_pgn_file(path))
    assert len(games) == 1
    game = games[0]
    assert len(game.moves) == 3
    assert game.moves[0].san == "e4"
    assert game.moves[1].san == "e5"
    assert game.moves[2].san == "Nf3"


def test_import_unfinished_result_asterisk() -> None:
    path = get_fixture_path("unfinished.pgn")
    games = list(parse_pgn_file(path))
    assert len(games) == 1
    assert games[0].result == "*"
