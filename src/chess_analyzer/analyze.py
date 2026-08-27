"""Orquestrador de análise integrando Stockfish, classificação de lances e persistência SQLite."""

from dataclasses import dataclass

import chess

from chess_analyzer.classify import PositionEvaluation, classify_move
from chess_analyzer.db import (
    get_connection,
    get_evaluation,
    get_game_starting_fen,
    init_db,
    save_evaluation,
)
from chess_analyzer.engine import StockfishEngine


@dataclass(frozen=True)
class AnalyzeStats:
    """Estatísticas do processo de análise de partidas."""

    total_games: int
    analyzed_games: int
    analyzed_moves: int


def _get_or_evaluate_fen(
    db_path: str,
    fen: str,
    engine: StockfishEngine,
    target_depth: int,
) -> PositionEvaluation:
    """Recupera a avaliação de uma posição do cache ou avalia via engine persistindo o resultado.

    Nota de Design (Fase 1):
    Persiste a avaliação com depth=target_depth e engine.cache_key. Conforme documentado
    em engine.py, depth=12 conclui rotineiramente em ~6ms a ~50ms e move_time_limit (2.0s)
    atua como safety valve.
    """
    cached = get_evaluation(
        db_path,
        fen,
        min_depth=target_depth,
        engine_key=engine.cache_key,
    )
    if cached is not None:
        return PositionEvaluation(white_cp=cached[0], mate_for_white=cached[1])

    pos_eval = engine.evaluate(chess.Board(fen))
    save_evaluation(
        db_path=db_path,
        fen=fen,
        depth=target_depth,
        eval_cp=pos_eval.white_cp,
        eval_mate=pos_eval.mate_for_white,
        engine_key=engine.cache_key,
    )
    return pos_eval


def analyze_games(
    db_path: str,
    engine: StockfishEngine,
    target_depth: int = 12,
) -> AnalyzeStats:
    """Analisa todas as partidas pendentes com lances sem classificação no banco de dados.

    Utiliza o modelo Two-Phase Execution por partida:
    - Fase 1: Avalia FENs e calcula métricas em memória, com persistência atômica individual
      no cache de evaluations em caso de miss.
    - Fase 2: Atualização atômica em lote da tabela moves para a partida inteira.
    """
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT game_id
            FROM moves
            WHERE category IS NULL
            ORDER BY game_id ASC;
            """
        )
        game_ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    total_games = len(game_ids)
    analyzed_games = 0
    analyzed_moves = 0

    for game_id in game_ids:
        conn = get_connection(db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, ply, fen_after
                FROM moves
                WHERE game_id = ?
                ORDER BY ply ASC;
                """,
                (game_id,),
            )
            moves = cur.fetchall()
        finally:
            conn.close()

        if not moves:
            continue

        starting_fen = get_game_starting_fen(db_path, game_id)

        # Avaliação da posição inicial da partida (para compor o eval_before do ply 1)
        eval_before = _get_or_evaluate_fen(
            db_path=db_path,
            fen=starting_fen,
            engine=engine,
            target_depth=target_depth,
        )

        moves_updates: list[tuple[str, int | None, int | None, float, float, float, int]] = []

        # Fase 1: Avaliação dos lances e classificação em memória
        for move_id, ply, fen_after in moves:
            eval_after = _get_or_evaluate_fen(
                db_path=db_path,
                fen=fen_after,
                engine=engine,
                target_depth=target_depth,
            )

            # Derivação do jogador pela paridade do ply (ímpar = Brancas, par = Pretas)
            player = chess.WHITE if ply % 2 != 0 else chess.BLACK

            classification = classify_move(
                eval_before=eval_before,
                eval_after=eval_after,
                player=player,
            )

            moves_updates.append(
                (
                    classification.category.value,
                    eval_after.white_cp,
                    eval_after.mate_for_white,
                    classification.delta_win_prob,
                    classification.win_prob_before,
                    classification.win_prob_after,
                    move_id,
                )
            )

            # O eval_after na ótica absoluta das Brancas torna-se o eval_before do próximo lance
            eval_before = eval_after

        # Fase 2: Write-back atômico em lote da partida
        conn = get_connection(db_path)
        try:
            with conn:
                conn.executemany(
                    """
                    UPDATE moves SET
                        category = ?,
                        eval_cp = ?,
                        eval_mate = ?,
                        delta_win_prob = ?,
                        win_prob_before = ?,
                        win_prob_after = ?
                    WHERE id = ?;
                    """,
                    moves_updates,
                )
        finally:
            conn.close()

        analyzed_games += 1
        analyzed_moves += len(moves_updates)

    return AnalyzeStats(
        total_games=total_games,
        analyzed_games=analyzed_games,
        analyzed_moves=analyzed_moves,
    )
