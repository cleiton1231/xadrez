"""Testes unitários e de integração para o módulo analyze.py (TDD)."""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chess_analyzer.analyze import AnalyzeStats, analyze_games
from chess_analyzer.classify import PositionEvaluation
from chess_analyzer.db import get_connection, get_evaluation, init_db, save_games
from chess_analyzer.engine import StockfishEngine
from chess_analyzer.pgn_import import ParsedGame, ParsedMove, parse_pgn_file
from tests.conftest import STOCKFISH_PATH, requires_stockfish

_EVAL_INSERT = (
    "INSERT INTO evaluations (fen, depth, engine_key, eval_cp, eval_mate) "
    "VALUES (?, ?, ?, ?, ?)"
)


def _mock_engine() -> MagicMock:
    mock = MagicMock(spec=StockfishEngine)
    mock.depth = 12
    mock.cache_key = "mock-engine"
    return mock


def get_fixture_path(filename: str) -> str:
    """Retorna o caminho absoluto para um arquivo de fixture."""
    return os.path.join(os.path.dirname(__file__), "fixtures", filename)


@pytest.fixture
def temp_db(tmp_path: Path) -> str:
    """Cria um banco SQLite temporário inicializado."""
    db_path = str(tmp_path / "test_chess.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def sample_game() -> ParsedGame:
    """Cria uma partida sintética curta de 2 lances (1. e4 e5)."""
    move1 = ParsedMove(
        ply=1,
        san="e4",
        fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    )
    move2 = ParsedMove(
        ply=2,
        san="e5",
        fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    )
    return ParsedGame(
        white="PlayerW",
        black="PlayerB",
        result="1-0",
        moves=[move1, move2],
        date="2026.08.21",
    )


def test_analyze_cache_miss_invokes_engine_and_saves_evaluation(
    temp_db: str,
    sample_game: ParsedGame,
) -> None:
    """Cache miss deve chamar o engine para posições e persistir em evaluations."""
    save_games([sample_game], temp_db)

    mock_engine = _mock_engine()
    # Retornos para: FEN inicial, FEN move 1, FEN move 2
    mock_engine.evaluate.side_effect = [
        PositionEvaluation(white_cp=20, mate_for_white=None),   # Inicial
        PositionEvaluation(white_cp=35, mate_for_white=None),   # 1. e4
        PositionEvaluation(white_cp=30, mate_for_white=None),   # 1... e5
    ]

    stats = analyze_games(temp_db, mock_engine, target_depth=12)

    assert isinstance(stats, AnalyzeStats)
    assert stats.total_games == 1
    assert stats.analyzed_games == 1
    assert stats.analyzed_moves == 2
    assert mock_engine.evaluate.call_count == 3

    # Verifica se os FENs foram persistidos na tabela evaluations
    eval_init = get_evaluation(
        temp_db,
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        min_depth=12,
        engine_key="mock-engine",
    )
    assert eval_init == (20, None, 12)

    eval_m1 = get_evaluation(
        temp_db,
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        min_depth=12,
        engine_key="mock-engine",
    )
    assert eval_m1 == (35, None, 12)


def test_analyze_cache_hit_bypasses_engine(temp_db: str, sample_game: ParsedGame) -> None:
    """Posições já avaliadas em evaluations com depth >= target_depth devem dar cache hit."""
    save_games([sample_game], temp_db)

    # Pré-popula o cache evaluations com os 4 campos FEN canônicos
    conn = get_connection(temp_db)
    with conn:
        conn.execute(
            _EVAL_INSERT,
            ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -", 12, "mock-engine", 15, None),
        )
        conn.execute(
            _EVAL_INSERT,
            ("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq -", 12, "mock-engine", 40, None),
        )
        conn.execute(
            _EVAL_INSERT,
            (
                "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
                12,
                "mock-engine",
                35,
                None,
            ),
        )
    conn.close()

    mock_engine = _mock_engine()

    stats = analyze_games(temp_db, mock_engine, target_depth=12)

    assert stats.analyzed_games == 1
    assert stats.analyzed_moves == 2
    assert mock_engine.evaluate.call_count == 0


def test_analyze_perspective_and_classification_values(
    temp_db: str,
    sample_game: ParsedGame,
) -> None:
    """Verifica se eval_cp/mate estão na ótica das Brancas e se delta/category
    usam a ótica do jogador.
    """
    save_games([sample_game], temp_db)

    mock_engine = _mock_engine()
    # Posição inicial: +20cp para Brancas (WinProb(20) ~ 51.84%)
    # Após 1. e4 (Brancas): +150cp -> WinProb(150) ~ 63.46% -> ΔW = 51.84 - 63.46 = -11.62 -> BEST
    # Após 1... e5 (Pretas): -200cp para Brancas (+200cp para Pretas)
    #   Antes do lance 2 (ótica Pretas): eval_before = -150cp -> WinProb(-150) ~ 36.54%
    #   Depois do lance 2 (ótica Pretas): eval_after = +200cp -> WinProb(200) ~ 67.62%
    #   ΔW = 36.54 - 67.62 = -31.08 -> BEST
    mock_engine.evaluate.side_effect = [
        PositionEvaluation(white_cp=20, mate_for_white=None),
        PositionEvaluation(white_cp=150, mate_for_white=None),
        PositionEvaluation(white_cp=-200, mate_for_white=None),
    ]

    analyze_games(temp_db, mock_engine, target_depth=12)

    conn = get_connection(temp_db)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ply, eval_cp, eval_mate, category, delta_win_prob, win_prob_before, win_prob_after
        FROM moves
        ORDER BY ply ASC
        """
    )
    rows = cur.fetchall()
    conn.close()

    assert len(rows) == 2

    # Move 1 (Brancas, ply 1)
    ply1, cp1, mate1, cat1, delta1, wp_bef1, wp_aft1 = rows[0]
    assert ply1 == 1
    assert cp1 == 150  # Ótica absoluta das Brancas
    assert mate1 is None
    assert cat1 == "BEST"
    assert wp_bef1 == pytest.approx(51.84, abs=0.1)
    assert wp_aft1 == pytest.approx(63.46, abs=0.1)
    assert delta1 == pytest.approx(wp_bef1 - wp_aft1, abs=0.001)

    # Move 2 (Pretas, ply 2)
    ply2, cp2, mate2, cat2, delta2, wp_bef2, wp_aft2 = rows[1]
    assert ply2 == 2
    assert cp2 == -200  # Ótica absoluta das Brancas
    assert mate2 is None
    assert cat2 == "BEST"
    # Da perspectiva das Pretas, antes era -150cp (wp ~ 36.54%), depois ficou +200cp (wp ~ 67.62%)
    assert wp_bef2 == pytest.approx(36.54, abs=0.1)
    assert wp_aft2 == pytest.approx(67.62, abs=0.1)
    assert delta2 == pytest.approx(wp_bef2 - wp_aft2, abs=0.001)


def test_analyze_failure_resilience_preserves_evals_without_partial_moves(
    temp_db: str,
    sample_game: ParsedGame,
) -> None:
    """Falha durante a avaliação não corrompe tabela moves (tudo NULL) mas preserva evaluations."""
    save_games([sample_game], temp_db)

    mock_engine = _mock_engine()
    # Avalia posição inicial com sucesso, avalia move 1 com sucesso, falha no move 2
    mock_engine.evaluate.side_effect = [
        PositionEvaluation(white_cp=20, mate_for_white=None),
        PositionEvaluation(white_cp=35, mate_for_white=None),
        RuntimeError("Stockfish crashed unexpected UCI error"),
    ]

    with pytest.raises(RuntimeError, match="Stockfish crashed"):
        analyze_games(temp_db, mock_engine, target_depth=12)

    conn = get_connection(temp_db)
    cur = conn.cursor()

    # moves deve continuar intacto (100% NULL nas colunas calculadas)
    cur.execute("SELECT category, eval_cp, delta_win_prob FROM moves")
    moves_rows = cur.fetchall()
    for cat, cp, delta in moves_rows:
        assert cat is None
        assert cp is None
        assert delta is None

    # evaluations deve conter os FENs avaliados antes do crash
    cur.execute("SELECT fen, eval_cp FROM evaluations ORDER BY id ASC")
    eval_rows = cur.fetchall()
    conn.close()

    assert len(eval_rows) == 2
    # Posição inicial e ply 1 foram persistidos
    assert eval_rows[0][1] == 20
    assert eval_rows[1][1] == 35


def test_analyze_idempotency_skips_fully_analyzed_games(
    temp_db: str,
    sample_game: ParsedGame,
) -> None:
    """Executar a análise duas vezes consecutivas não reprocessa nem invoca o engine na 2ª vez."""
    save_games([sample_game], temp_db)

    mock_engine = _mock_engine()
    mock_engine.evaluate.side_effect = [
        PositionEvaluation(white_cp=20, mate_for_white=None),
        PositionEvaluation(white_cp=35, mate_for_white=None),
        PositionEvaluation(white_cp=30, mate_for_white=None),
    ]

    # Primeira execução: analisa 1 partida
    stats1 = analyze_games(temp_db, mock_engine, target_depth=12)
    assert stats1.analyzed_games == 1
    assert stats1.analyzed_moves == 2
    assert mock_engine.evaluate.call_count == 3

    # Segunda execução: tudo já analisado
    stats2 = analyze_games(temp_db, mock_engine, target_depth=12)
    assert stats2.total_games == 0
    assert stats2.analyzed_games == 0
    assert stats2.analyzed_moves == 0
    assert mock_engine.evaluate.call_count == 3  # Nenhuma chamada adicional


@requires_stockfish
def test_analyze_real_stockfish_end_to_end(temp_db: str) -> None:
    """Teste de ponta a ponta sem mock, rodando o Stockfish real com fixture PGN."""
    assert STOCKFISH_PATH is not None
    games = list(parse_pgn_file(get_fixture_path("lichess_real.pgn")))
    assert len(games) == 1
    save_games(games, temp_db)

    with StockfishEngine(STOCKFISH_PATH, depth=12) as engine:
        stats = analyze_games(temp_db, engine, target_depth=12)

    assert stats.total_games == 1
    assert stats.analyzed_games == 1
    assert stats.analyzed_moves == 3

    conn = get_connection(temp_db)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ply, san, category, eval_cp, eval_mate, delta_win_prob,
               win_prob_before, win_prob_after
        FROM moves
        ORDER BY ply ASC
        """
    )
    rows = cur.fetchall()
    conn.close()

    assert len(rows) == 3
    for _ply, _san, cat, cp, mate, delta, wp_bef, wp_aft in rows:
        assert cat in {"BEST", "EXCELLENT", "GOOD", "INACCURACY", "MISTAKE", "BLUNDER"}
        assert (cp is not None) or (mate is not None)
        assert 0.0 <= wp_bef <= 100.0
        assert 0.0 <= wp_aft <= 100.0
        assert delta == pytest.approx(wp_bef - wp_aft, abs=0.01)
