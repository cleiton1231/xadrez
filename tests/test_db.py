import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from chess_analyzer.db import (
    ImportStats,
    calculate_game_hash,
    get_connection,
    get_evaluation,
    init_db,
    normalize_fen,
    save_evaluation,
    save_games,
)
from chess_analyzer.pgn_import import ParsedGame, ParsedMove


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Retorna um caminho temporário para o banco de dados de teste."""
    return str(tmp_path / "test_chess.db")


def create_sample_game(
    white: str = "PlayerA",
    black: str = "PlayerB",
    result: str = "1-0",
    date: str | None = "2023.01.01",
    moves: list[ParsedMove] | None = None,
    **kwargs: object,
) -> ParsedGame:
    """Cria um ParsedGame de exemplo para uso nos testes."""
    if moves is None:
        moves = [
            ParsedMove(
                ply=1,
                san="e4",
                fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
            ),
            ParsedMove(
                ply=2,
                san="e5",
                fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",
            ),
        ]
    return ParsedGame(
        white=white,
        black=black,
        result=result,
        moves=moves,
        date=date,
        event=kwargs.get("event", "Sample Event"),  # type: ignore[arg-type]
        site=kwargs.get("site", "https://example.com"),  # type: ignore[arg-type]
        white_elo=kwargs.get("white_elo", 1500),  # type: ignore[arg-type]
        black_elo=kwargs.get("black_elo", 1500),  # type: ignore[arg-type]
        time_control=kwargs.get("time_control", "300+0"),  # type: ignore[arg-type]
        eco=kwargs.get("eco", "C20"),  # type: ignore[arg-type]
        variant=kwargs.get("variant", None),  # type: ignore[arg-type]
    )


def test_init_db_creates_tables_and_user_version(db_path: str) -> None:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA user_version;")
        user_version = cur.fetchone()[0]
        assert user_version == 1

        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        assert {"games", "moves", "evaluations"}.issubset(tables)

        cur.execute("SELECT name FROM sqlite_master WHERE type='index';")
        indices = {row[0] for row in cur.fetchall()}
        assert {
            "idx_games_white",
            "idx_games_black",
            "idx_games_eco",
            "idx_games_date",
            "idx_moves_game_id",
            "idx_moves_fen",
            "idx_moves_category",
            "idx_evaluations_fen",
        }.issubset(indices)
    finally:
        conn.close()


def test_foreign_keys_pragma_enforced_on_new_connection(db_path: str) -> None:
    init_db(db_path)
    new_conn = get_connection(db_path)
    try:
        cur = new_conn.cursor()
        cur.execute("PRAGMA foreign_keys;")
        fk_status = cur.fetchone()[0]
        assert fk_status == 1

        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                "INSERT INTO moves (game_id, ply, san, fen_after) VALUES (?, ?, ?, ?)",
                (99999, 1, "e4", "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3"),
            )
            new_conn.commit()
    finally:
        new_conn.close()


def test_insert_single_game_with_moves_and_normalized_fen(db_path: str) -> None:
    init_db(db_path)
    game = create_sample_game()
    stats = save_games([game], db_path)
    assert stats == ImportStats(total=1, inserted=1, skipped=0)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, game_hash, white, black, result, eco FROM games")
        game_rows = cur.fetchall()
        assert len(game_rows) == 1
        game_id, game_hash, white, black, result, eco = game_rows[0]
        assert white == "PlayerA"
        assert black == "PlayerB"
        assert result == "1-0"
        assert eco == "C20"
        assert game_hash == calculate_game_hash(game)

        cur.execute(
            "SELECT game_id, ply, san, fen_after FROM moves WHERE game_id = ? ORDER BY ply",
            (game_id,),
        )
        move_rows = cur.fetchall()
        assert len(move_rows) == 2
        assert move_rows[0] == (
            game_id,
            1,
            "e4",
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3",
        )
        assert move_rows[1] == (
            game_id,
            2,
            "e5",
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6",
        )
    finally:
        conn.close()


def test_insert_game_with_optional_fields_none(db_path: str) -> None:
    init_db(db_path)
    game = ParsedGame(
        white="PlayerX",
        black="PlayerY",
        result="0-1",
        moves=[],
        event=None,
        site=None,
        date=None,
        white_elo=None,
        black_elo=None,
        time_control=None,
        eco=None,
        variant=None,
    )
    stats = save_games([game], db_path)
    assert stats == ImportStats(total=1, inserted=1, skipped=0)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT event, site, date, white_elo, black_elo, time_control, eco, variant FROM games"
        )
        row = cur.fetchone()
        assert row == (None, None, None, None, None, None, None, None)
    finally:
        conn.close()


def test_insert_game_with_star_result(db_path: str) -> None:
    init_db(db_path)
    game = create_sample_game(result="*")
    stats = save_games([game], db_path)
    assert stats == ImportStats(total=1, inserted=1, skipped=0)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT result FROM games")
        row = cur.fetchone()
        assert row == ("*",)
    finally:
        conn.close()


def test_insert_game_with_zero_moves(db_path: str) -> None:
    init_db(db_path)
    game = create_sample_game(moves=[])
    stats = save_games([game], db_path)
    assert stats == ImportStats(total=1, inserted=1, skipped=0)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM games")
        game_id = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM moves WHERE game_id = ?", (game_id,))
        count = cur.fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_idempotent_import_skips_duplicate_game(db_path: str) -> None:
    init_db(db_path)
    game = create_sample_game()
    stats1 = save_games([game], db_path)
    assert stats1 == ImportStats(total=1, inserted=1, skipped=0)

    stats2 = save_games([game], db_path)
    assert stats2 == ImportStats(total=1, inserted=0, skipped=1)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM games")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT COUNT(*) FROM moves")
        assert cur.fetchone()[0] == 2
    finally:
        conn.close()


def test_distinct_games_same_players_same_date_both_inserted(db_path: str) -> None:
    init_db(db_path)
    moves1 = [
        ParsedMove(
            ply=1,
            san="e4",
            fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        ),
        ParsedMove(
            ply=2,
            san="e5",
            fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",
        ),
    ]
    moves2 = [
        ParsedMove(
            ply=1,
            san="d4",
            fen_after="rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq d3 0 1",
        ),
        ParsedMove(
            ply=2,
            san="d5",
            fen_after="rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq d6 0 2",
        ),
    ]
    game1 = create_sample_game(
        white="Magnus", black="Hikaru", date="2023.05.01", result="1-0", moves=moves1
    )
    game2 = create_sample_game(
        white="Magnus", black="Hikaru", date="2023.05.01", result="1-0", moves=moves2
    )

    stats = save_games([game1, game2], db_path)
    assert stats == ImportStats(total=2, inserted=2, skipped=0)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT game_hash FROM games")
        hashes = [row[0] for row in cur.fetchall()]
        assert len(hashes) == 2
        assert hashes[0] != hashes[1]
    finally:
        conn.close()


def test_streaming_import_multiple_games(db_path: str) -> None:
    init_db(db_path)

    def game_generator() -> Iterator[ParsedGame]:
        for i in range(5):
            yield create_sample_game(
                white=f"Player_{i}",
                black=f"Opponent_{i}",
                date=f"2023.01.0{i + 1}",
            )

    stats = save_games(game_generator(), db_path, batch_size=2)
    assert stats == ImportStats(total=5, inserted=5, skipped=0)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM games")
        assert cur.fetchone()[0] == 5
        cur.execute("SELECT COUNT(*) FROM moves")
        assert cur.fetchone()[0] == 10
    finally:
        conn.close()


def test_batch_transaction_atomicity_and_rollback(db_path: str) -> None:
    init_db(db_path)

    def faulty_generator() -> Iterator[ParsedGame]:
        # Lote 1 (batch_size=2): estes 2 devem ser commitados com sucesso
        yield create_sample_game(white="G1", black="O1")
        yield create_sample_game(white="G2", black="O2")
        # Lote 2: primeiro jogo válido, mas em seguida o gerador falha
        yield create_sample_game(white="G3", black="O3")
        raise RuntimeError("Simulated crash in stream during batch 2")

    with pytest.raises(RuntimeError, match="Simulated crash"):
        save_games(faulty_generator(), db_path, batch_size=2)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT white FROM games ORDER BY id")
        saved_whites = [row[0] for row in cur.fetchall()]
        # Lote 1 persistido ("G1", "G2"), lote 2 descartado ("G3" não gravado)
        assert saved_whites == ["G1", "G2"]
    finally:
        conn.close()


def test_cascade_delete_game_removes_moves(db_path: str) -> None:
    init_db(db_path)
    game = create_sample_game()
    save_games([game], db_path)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM games")
        game_id = cur.fetchone()[0]
        cur.execute("DELETE FROM games WHERE id = ?", (game_id,))
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM moves WHERE game_id = ?", (game_id,))
        count = cur.fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_evaluations_cache_crud_and_depth_unique(db_path: str) -> None:
    init_db(db_path)
    fen_full = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
    fen_norm = normalize_fen(fen_full)

    # Salva profundidade 12
    save_evaluation(db_path, fen_full, depth=12, eval_cp=35, eval_mate=None)
    # Salva profundidade 20 para o mesmo FEN
    save_evaluation(db_path, fen_full, depth=20, eval_cp=42, eval_mate=None)

    # Consulta com min_depth=10 -> deve retornar a avaliação de maior profundidade (depth 20)
    eval_res = get_evaluation(db_path, fen_full, min_depth=10)
    assert eval_res == (42, None, 20)

    # Consulta com min_depth=15 -> retorna depth 20
    eval_res2 = get_evaluation(db_path, fen_full, min_depth=15)
    assert eval_res2 == (42, None, 20)

    # Consulta com min_depth=25 -> retorna None pois max depth armazenada é 20
    eval_res3 = get_evaluation(db_path, fen_full, min_depth=25)
    assert eval_res3 is None

    # Atualiza depth 12 existente sem erro
    save_evaluation(db_path, fen_full, depth=12, eval_cp=38, eval_mate=None)
    eval_res_d12 = get_evaluation(db_path, fen_full, min_depth=12)
    assert eval_res_d12 == (42, None, 20)  # maior depth ainda é 20

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT depth, eval_cp FROM evaluations WHERE fen = ? ORDER BY depth",
            (fen_norm,),
        )
        rows = cur.fetchall()
        assert rows == [(12, 38), (20, 42)]
    finally:
        conn.close()


def test_special_characters_and_encoding_persistence(db_path: str) -> None:
    init_db(db_path)
    game = create_sample_game(
        white="José Raúl Capablanca",
        black="Alekhine, Alexandre",
        event="Torneio Internacional de São Paulo ♟️",
        site="São Paulo, Brasil",
    )
    save_games([game], db_path)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT white, black, event, site FROM games")
        row = cur.fetchone()
        assert row == (
            "José Raúl Capablanca",
            "Alekhine, Alexandre",
            "Torneio Internacional de São Paulo ♟️",
            "São Paulo, Brasil",
        )
    finally:
        conn.close()
