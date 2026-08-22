"""Testes do módulo de treino direcionado (Fase 2) — TDD RED-GREEN-REFACTOR."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chess_analyzer.cli import app
from chess_analyzer.db import get_connection, init_db
from chess_analyzer.puzzles import (
    PuzzleItem,
    detect_weakest_phase,
    generate_training_session,
    get_player_elo,
    index_puzzles,
)
from chess_analyzer.stats import AggregatedStats, CategoryCount, GamePhase
from tests.test_puzzles import make_zst_fixture

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample_puzzles.csv"
runner = CliRunner()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Cria e inicializa um banco de testes com puzzles indexados e partidas."""
    db_file = str(tmp_path / "test_training.db")
    init_db(db_file)

    # Indexa os 20 puzzles da fixture
    zst_file = make_zst_fixture(tmp_path, FIXTURE_CSV)
    index_puzzles(zst_file, db_file)

    # Insere partidas de exemplo para o jogador 'PlayerA'
    conn = get_connection(db_file)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO games (game_hash, white, black, result, white_elo, black_elo)
                VALUES ('hash1', 'PlayerA', 'PlayerB', '1-0', 1600, 1550),
                       ('hash2', 'PlayerC', 'PlayerA', '0-1', 1650, 1620);
                """
            )
            game1_id = 1
            game2_id = 2

            f1 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
            f2 = "r1bq1rk1/ppp2ppp/2n2n2/3pp3/1bB1P3/2NP1N2/PPP2PPP/R1BQK2R w KQ - 4 7"
            f3 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2"
            f4 = "r1bq1rk1/ppp2ppp/2n2n2/3pp3/1bB1P3/2NP1N2/PPP2PPP/R1BQK2R b KQ - 4 7"

            cur.execute(
                """
                INSERT INTO moves (game_id, ply, san, fen_after, category, delta_win_prob)
                VALUES
                    (:g1, 1, 'e4', :f1, 'GOOD', 0.5),
                    (:g1, 21, 'Nf3', :f2, 'BLUNDER', 15.0),
                    (:g2, 2, 'e5', :f3, 'EXCELLENT', 0.2),
                    (:g2, 22, 'd5', :f4, 'MISTAKE', 8.0);
                """,
                {"g1": game1_id, "g2": game2_id, "f1": f1, "f2": f2, "f3": f3, "f4": f4},
            )
    finally:
        conn.close()

    return db_file


# ── Test 1: Detecção da fase mais fraca por perda média ──────────────────────


def test_detect_weakest_phase_highest_delta_win() -> None:
    """detect_weakest_phase deve selecionar a fase com maior avg_delta_win_prob."""
    opening_stats = AggregatedStats(
        group_key="OPENING",
        group_type="game_phase",
        category_counts=CategoryCount(good=5, blunder=0, total=5),
        avg_delta_win_prob=1.2,
        total_moves=5,
    )
    middlegame_stats = AggregatedStats(
        group_key="MIDDLEGAME",
        group_type="game_phase",
        category_counts=CategoryCount(good=2, blunder=3, total=5),
        avg_delta_win_prob=12.5,
        total_moves=5,
    )
    endgame_stats = AggregatedStats(
        group_key="ENDGAME",
        group_type="game_phase",
        category_counts=CategoryCount(good=4, blunder=0, total=4),
        avg_delta_win_prob=2.0,
        total_moves=4,
    )

    detected = detect_weakest_phase([opening_stats, middlegame_stats, endgame_stats])
    assert detected is not None
    phase, stat = detected
    assert phase == GamePhase.MIDDLEGAME
    assert stat.avg_delta_win_prob == 12.5


# ── Test 2: Desempate por taxa de blunder ────────────────────────────────────


def test_detect_weakest_phase_tiebreaker_blunder_rate() -> None:
    """Em caso de avg_delta_win_prob idêntico, desempate deve ser pela taxa de blunder."""
    opening_stats = AggregatedStats(
        group_key="OPENING",
        group_type="game_phase",
        category_counts=CategoryCount(blunder=1, total=10),
        avg_delta_win_prob=5.0,
        total_moves=10,
    )
    endgame_stats = AggregatedStats(
        group_key="ENDGAME",
        group_type="game_phase",
        category_counts=CategoryCount(blunder=4, total=10),
        avg_delta_win_prob=5.0,
        total_moves=10,
    )

    detected = detect_weakest_phase([opening_stats, endgame_stats])
    assert detected is not None
    phase, stat = detected
    assert phase == GamePhase.ENDGAME
    assert stat.category_counts.blunder == 4


# ── Test 3: get_player_elo retorna (elo, sample_size) ─────────────────────────


def test_get_player_elo_from_games(db_path: str) -> None:
    """get_player_elo deve retornar média arredondada e contagem de partidas."""
    # PlayerA: 1 jogo como White (1600) e 1 como Black (1620) -> média 1610, 2 jogos
    elo_info = get_player_elo(db_path, "PlayerA")
    assert elo_info is not None
    elo, sample_size = elo_info
    assert elo == 1610
    assert sample_size == 2

    # Jogador inexistente -> None
    assert get_player_elo(db_path, "NonExistentPlayer") is None


# ── Test 4: Geração de sessão e transformação de FEN (aplica lance oponente) ──


def test_generate_training_session_pushes_opponent_move(db_path: str) -> None:
    """generate_training_session deve aplicar Moves[0] ao FEN para obter o training_fen."""
    session = generate_training_session(db_path, "PlayerA", count=3)
    assert session is not None
    assert session.weakest_phase == GamePhase.MIDDLEGAME
    assert session.target_theme == "middlegame"
    assert session.player_elo == 1610
    assert session.elo_sample_size == 2
    assert len(session.puzzles) > 0

    for item in session.puzzles:
        assert isinstance(item, PuzzleItem)
        assert item.fen_before != item.training_fen
        # O training_fen deve ter o lance do oponente já executado
        assert len(item.solution_uci) >= 1
        assert len(item.solution_san) == len(item.solution_uci)


# ── Test 5: Formatação da solução em SAN legível ──────────────────────────────


def test_generate_training_session_formats_san_solution(db_path: str) -> None:
    """generate_training_session deve converter todos os lances de solução para SAN válido."""
    session = generate_training_session(db_path, "PlayerA", count=5)
    assert session is not None
    for item in session.puzzles:
        assert item.opponent_move_san != ""
        for san_move in item.solution_san:
            assert san_move != ""
            # Não deve conter espaços dentro de um único lance SAN
            assert " " not in san_move


# ── Test 6: Override de fase via forced_phase ─────────────────────────────────


def test_generate_training_session_forced_phase(db_path: str) -> None:
    """forced_phase deve sobrepor a detecção automática e buscar puzzles da fase forçada."""
    session = generate_training_session(
        db_path,
        "PlayerA",
        count=2,
        forced_phase=GamePhase.OPENING,
    )
    assert session is not None
    assert session.weakest_phase == GamePhase.OPENING
    assert session.target_theme == "opening"


# ── Test 7: Tratamento gracioso de dados vazios ───────────────────────────────


def test_generate_training_session_empty_data(tmp_path: Path) -> None:
    """generate_training_session deve retornar None se não houver dados analisados."""
    empty_db = str(tmp_path / "empty.db")
    init_db(empty_db)
    session = generate_training_session(empty_db, "UnknownPlayer")
    assert session is None


# ── Test 8: Defensividade contra FEN malformado ───────────────────────────────


def test_generate_training_session_defensive_malformed_fen(tmp_path: Path) -> None:
    """Posições com FEN inválido no banco de puzzles devem ser ignoradas sem crash."""
    db_file = str(tmp_path / "malformed.db")
    init_db(db_file)
    conn = get_connection(db_file)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO games (game_hash, white, black, result, white_elo)
                VALUES ('h1', 'Hero', 'Villain', '1-0', 1500);
                """
            )
            f_start = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
            cur.execute(
                """
                INSERT INTO moves (game_id, ply, san, fen_after, category, delta_win_prob)
                VALUES (1, 1, 'e4', :fen, 'BLUNDER', 20.0);
                """,
                {"fen": f_start},
            )
            # Puzzle com FEN corrompido
            cur.execute(
                """
                INSERT INTO puzzles (
                    puzzle_id, fen, moves, rating, rating_deviation, popularity, nb_plays, themes
                )
                VALUES (
                    'bad_fen_1', 'INVALID_FEN_STRING_123', 'e2e4 e7e5', 1500, 80, 90, 100, 'opening'
                );
                """
            )
            cur.execute(
                """
                INSERT INTO puzzle_themes (puzzle_id, theme)
                VALUES ('bad_fen_1', 'opening');
                """
            )
    finally:
        conn.close()

    session = generate_training_session(db_file, "Hero", count=5)
    assert session is not None
    # Deve ter pulado o puzzle inválido sem levantar exceção
    assert len(session.puzzles) == 0


# ── Test 9: Comando CLI 'chess-analyzer train' (Tabela e JSON) ────────────────


def test_cli_train_command_table_and_json(db_path: str) -> None:
    """CLI train deve exibir tabela formatada por padrão e JSON estruturado com flag --json."""
    # Teste 1: Execução em modo tabela
    result_table = runner.invoke(app, ["train", "PlayerA", "--db", db_path, "--count", "3"])
    assert result_table.exit_code == 0
    assert "🎯 Treino Direcionado para:" in result_table.stdout
    assert "PlayerA" in result_table.stdout
    assert "MIDDLEGAME" in result_table.stdout
    assert "Elo estimado: 1610 (baseado em 2 partidas)" in result_table.stdout

    # Teste 2: Execução em modo JSON
    result_json = runner.invoke(
        app,
        ["train", "PlayerA", "--db", db_path, "--count", "3", "--json"],
    )
    assert result_json.exit_code == 0
    payload = json.loads(result_json.stdout)
    assert payload["player"] == "PlayerA"
    assert payload["weakest_phase"] == "MIDDLEGAME"
    assert payload["target_theme"] == "middlegame"
    assert payload["diagnosis"]["player_elo"] == 1610
    assert payload["diagnosis"]["elo_sample_size"] == 2
    assert payload["diagnosis"]["requested_count"] == 3
    assert isinstance(payload["puzzles"], list)

    # Teste 3: Jogador sem dados analisados
    result_unknown = runner.invoke(app, ["train", "UnknownPlayer", "--db", db_path])
    assert result_unknown.exit_code == 1
