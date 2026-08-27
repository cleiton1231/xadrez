"""Testes das melhorias de retomada: FEN customizado, popularidade, reindexação segura."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import chess
import pytest
import zstandard
from typer.testing import CliRunner

from chess_analyzer.analyze import analyze_games
from chess_analyzer.classify import PositionEvaluation
from chess_analyzer.cli import app
from chess_analyzer.db import get_connection, init_db, save_games
from chess_analyzer.engine import StockfishEngine
from chess_analyzer.pgn_import import parse_pgn_file
from chess_analyzer.puzzles import get_puzzles_by_theme, index_puzzles
from tests.test_puzzles import make_zst_fixture

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample_puzzles.csv"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def get_fixture_path(filename: str) -> str:
    return str(Path(__file__).parent / "fixtures" / filename)


def test_import_custom_fen_pgn() -> None:
    games = list(parse_pgn_file(get_fixture_path("custom_fen.pgn")))
    assert len(games) == 1
    game = games[0]
    assert game.white == "FenPlayerW"
    assert game.black == "FenPlayerB"
    assert "4P3" in game.starting_fen
    assert len(game.moves) == 2


def test_analyze_uses_custom_starting_fen(tmp_path: Path) -> None:
    db_path = str(tmp_path / "custom_fen.db")
    games = list(parse_pgn_file(get_fixture_path("custom_fen.pgn")))
    save_games(games, db_path)

    mock_engine = MagicMock(spec=StockfishEngine)
    mock_engine.depth = 12
    mock_engine.cache_key = "mock-engine"
    custom_start = games[0].starting_fen
    mock_engine.evaluate.side_effect = [
        PositionEvaluation(white_cp=0, mate_for_white=None),
        PositionEvaluation(white_cp=10, mate_for_white=None),
        PositionEvaluation(white_cp=5, mate_for_white=None),
    ]

    stats = analyze_games(db_path, mock_engine, target_depth=12)
    assert stats.analyzed_moves == 2

    first_board_arg = mock_engine.evaluate.call_args_list[0][0][0]
    expected_board = chess.Board(custom_start)
    assert first_board_arg.board_fen() == expected_board.board_fen()
    assert first_board_arg.turn == expected_board.turn
    assert "4P3" in first_board_arg.fen()


def test_get_puzzles_by_theme_returns_popularity(zst_path: Path, db_path: str) -> None:
    index_puzzles(zst_path, db_path)
    puzzles = get_puzzles_by_theme(db_path, "middlegame", limit=5)
    assert puzzles
    for puzzle in puzzles:
        assert "popularity" in puzzle
        assert puzzle["popularity"] > 0

    ratings = [p["popularity"] for p in puzzles]
    assert ratings == sorted(ratings, reverse=True)


def test_index_puzzles_preserves_data_on_failure(zst_path: Path, db_path: str) -> None:
    stats1 = index_puzzles(zst_path, db_path)
    assert stats1.inserted == 20

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM puzzles;")
        before_count = cur.fetchone()[0]
    finally:
        conn.close()

    broken = zst_path.with_name("broken.csv.zst")
    broken.write_bytes(b"not-a-valid-zst-stream")

    with pytest.raises(zstandard.ZstdError):
        index_puzzles(broken, db_path, force=True)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM puzzles;")
        after_count = cur.fetchone()[0]
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='puzzles_staging';"
        )
        assert cur.fetchone() is None
    finally:
        conn.close()

    assert after_count == before_count == 20


def test_download_puzzle_dataset_cleans_part_file_on_failure(tmp_path: Path) -> None:
    from chess_analyzer.puzzles import download_puzzle_dataset

    def _raise_urlerror(*_args: object, **_kwargs: object) -> None:
        raise OSError("network down")

    with patch("chess_analyzer.puzzles.urllib.request.urlopen", side_effect=_raise_urlerror):
        with pytest.raises(RuntimeError, match="Falha no download"):
            download_puzzle_dataset(str(tmp_path), url="http://example.com/fake.csv.zst")

    assert not (tmp_path / "fake.csv.part").exists()


def test_cli_train_rejects_invalid_count(runner: CliRunner, tmp_path: Path) -> None:
    db_path = str(tmp_path / "empty.db")
    init_db(db_path)
    result = runner.invoke(app, ["train", "Player", "--db", db_path, "--count", "0"])
    assert result.exit_code == 1
    assert "count deve ser >= 1" in result.stderr


def test_cli_puzzles_index_and_status(runner: CliRunner, tmp_path: Path) -> None:
    db_path = str(tmp_path / "puzzles_cli.db")
    zst_path = make_zst_fixture(tmp_path, FIXTURE_CSV)

    index_result = runner.invoke(
        app,
        ["puzzles", "index", "--file", str(zst_path), "--db", db_path],
    )
    assert index_result.exit_code == 0
    assert "Indexação concluída" in index_result.stdout

    status_result = runner.invoke(app, ["puzzles", "status", "--db", db_path])
    assert status_result.exit_code == 0
    assert "puzzle_count" in status_result.stdout
    assert "puzzles_in_db" in status_result.stdout


@pytest.fixture
def zst_path(tmp_path: Path) -> Path:
    return make_zst_fixture(tmp_path, FIXTURE_CSV)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "improvements.db")
