"""Módulo de agregação estatística de partidas de xadrez por cor, abertura e fase do jogo."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import chess

from chess_analyzer.db import get_connection, init_db

OPENING_PLY_LIMIT: int = 20
ENDGAME_MATERIAL_THRESHOLD: int = 26

PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}


class GamePhase(StrEnum):
    """Fases do jogo de xadrez."""

    OPENING = "OPENING"
    MIDDLEGAME = "MIDDLEGAME"
    ENDGAME = "ENDGAME"


@dataclass(frozen=True)
class CategoryCount:
    """Contagem de lances por categoria de precisão/qualidade."""

    best: int = 0
    excellent: int = 0
    good: int = 0
    inaccuracy: int = 0
    mistake: int = 0
    blunder: int = 0
    total: int = 0


@dataclass(frozen=True)
class AggregatedStats:
    """Estatísticas agregadas para um determinado agrupamento (cor, ECO, fase do jogo)."""

    group_key: str
    group_type: str
    category_counts: CategoryCount
    avg_delta_win_prob: float
    total_moves: int


def count_material(fen: str) -> int:
    """Calcula a soma de material no tabuleiro excluindo os reis."""
    board = chess.Board(fen)
    total = 0
    for piece in board.piece_map().values():
        if piece.piece_type != chess.KING:
            total += PIECE_VALUES.get(piece.piece_type, 0)
    return total


def classify_game_phase(ply: int, fen: str) -> GamePhase:
    """Classifica a fase do jogo de um lance com ordem de decisão estrita:

    1. ENDGAME se material total <= ENDGAME_MATERIAL_THRESHOLD (26)
    2. OPENING se ply <= OPENING_PLY_LIMIT (20)
    3. MIDDLEGAME para todos os demais casos
    """
    material = count_material(fen)
    if material <= ENDGAME_MATERIAL_THRESHOLD:
        return GamePhase.ENDGAME
    if ply <= OPENING_PLY_LIMIT:
        return GamePhase.OPENING
    return GamePhase.MIDDLEGAME


def _build_aggregated_stats(
    grouped_rows: dict[str, dict[str, tuple[int, float]]],
    group_type: str,
) -> list[AggregatedStats]:
    """Constrói lista de AggregatedStats calculando a média ponderada exata de delta_win_prob."""
    results: list[AggregatedStats] = []
    for group_key, cat_data in grouped_rows.items():
        best = cat_data.get("BEST", (0, 0.0))[0]
        excellent = cat_data.get("EXCELLENT", (0, 0.0))[0]
        good = cat_data.get("GOOD", (0, 0.0))[0]
        inaccuracy = cat_data.get("INACCURACY", (0, 0.0))[0]
        mistake = cat_data.get("MISTAKE", (0, 0.0))[0]
        blunder = cat_data.get("BLUNDER", (0, 0.0))[0]
        total = best + excellent + good + inaccuracy + mistake + blunder

        total_delta_sum = sum(cnt * avg for cnt, avg in cat_data.values())
        avg_delta = total_delta_sum / total if total > 0 else 0.0

        results.append(
            AggregatedStats(
                group_key=group_key,
                group_type=group_type,
                category_counts=CategoryCount(
                    best=best,
                    excellent=excellent,
                    good=good,
                    inaccuracy=inaccuracy,
                    mistake=mistake,
                    blunder=blunder,
                    total=total,
                ),
                avg_delta_win_prob=avg_delta,
                total_moves=total,
            )
        )
    return results


def stats_by_color(db_path: str, player_name: str) -> list[AggregatedStats]:
    """Agrega estatísticas de lances do jogador agrupados por cor jogada (white / black)."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                CASE
                    WHEN g.white = :player AND m.ply % 2 != 0 THEN 'white'
                    WHEN g.black = :player AND m.ply % 2  = 0 THEN 'black'
                END AS color_played,
                m.category,
                COUNT(*)              AS cnt,
                AVG(m.delta_win_prob) AS avg_delta
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE m.category IS NOT NULL
              AND (
                  (g.white = :player AND m.ply % 2 != 0)
               OR (g.black = :player AND m.ply % 2  = 0)
              )
            GROUP BY color_played, m.category;
            """,
            {"player": player_name},
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    grouped: dict[str, dict[str, tuple[int, float]]] = {}
    for color_played, category, cnt, avg_delta in rows:
        if color_played not in grouped:
            grouped[color_played] = {}
        grouped[color_played][category] = (cnt, avg_delta if avg_delta is not None else 0.0)

    return _build_aggregated_stats(grouped, group_type="color")


def stats_by_opening(db_path: str, player_name: str) -> list[AggregatedStats]:
    """Agrega estatísticas de lances do jogador agrupados por código de abertura ECO."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                g.eco,
                m.category,
                COUNT(*)              AS cnt,
                AVG(m.delta_win_prob) AS avg_delta
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE m.category IS NOT NULL
              AND g.eco IS NOT NULL
              AND (
                  (g.white = :player AND m.ply % 2 != 0)
               OR (g.black = :player AND m.ply % 2  = 0)
              )
            GROUP BY g.eco, m.category;
            """,
            {"player": player_name},
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    grouped: dict[str, dict[str, tuple[int, float]]] = {}
    for eco, category, cnt, avg_delta in rows:
        if eco not in grouped:
            grouped[eco] = {}
        grouped[eco][category] = (cnt, avg_delta if avg_delta is not None else 0.0)

    return _build_aggregated_stats(grouped, group_type="eco")


def stats_by_game_phase(db_path: str, player_name: str) -> list[AggregatedStats]:
    """Agrega estatísticas de lances do jogador por fase do jogo (OPENING, MIDDLEGAME, ENDGAME)."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT m.ply, m.fen_after, m.category, m.delta_win_prob
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE m.category IS NOT NULL
              AND (
                  (g.white = :player AND m.ply % 2 != 0)
               OR (g.black = :player AND m.ply % 2  = 0)
              );
            """,
            {"player": player_name},
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    # Agrupamento Python-side por GamePhase
    phase_category_counts: dict[str, dict[str, list[float]]] = {}
    for ply, fen_after, category, delta_win_prob in rows:
        phase = classify_game_phase(ply, fen_after).value
        if phase not in phase_category_counts:
            phase_category_counts[phase] = {}
        if category not in phase_category_counts[phase]:
            phase_category_counts[phase][category] = []
        phase_category_counts[phase][category].append(
            delta_win_prob if delta_win_prob is not None else 0.0
        )

    grouped: dict[str, dict[str, tuple[int, float]]] = {}
    for phase, cat_dict in phase_category_counts.items():
        grouped[phase] = {}
        for category, deltas in cat_dict.items():
            cnt = len(deltas)
            avg_delta = sum(deltas) / cnt if cnt > 0 else 0.0
            grouped[phase][category] = (cnt, avg_delta)

    return _build_aggregated_stats(grouped, group_type="game_phase")
