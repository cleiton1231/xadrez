# Relatório da Etapa 7 — `stats.py` (Agregação Estatística)

## 1. Diff / Conteúdo Completo dos Arquivos Criados e Modificados

### `src/chess_analyzer/stats.py` (Criado)

```python
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
```

### `tests/test_stats.py` (Criado)

```python
"""Testes unitários e de integração para o módulo stats.py (TDD)."""

from pathlib import Path

import chess
import pytest

from chess_analyzer.db import get_connection, init_db
from chess_analyzer.stats import (
    CategoryCount,
    GamePhase,
    classify_game_phase,
    count_material,
    stats_by_color,
    stats_by_game_phase,
    stats_by_opening,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> str:
    """Cria um banco SQLite temporário inicializado."""
    db_path = str(tmp_path / "test_stats.db")
    init_db(db_path)
    return db_path


def test_stats_by_color_with_unequal_counts_weighted_average(temp_db: str) -> None:
    """Valida agregação por cor e cálculo estrito de média ponderada de delta_win_prob."""
    conn = get_connection(temp_db)
    try:
        cur = conn.cursor()
        # Partida 1: Alice joga de Brancas
        cur.execute(
            """
            INSERT INTO games (game_hash, white, black, result)
            VALUES ('hash1', 'Alice', 'Bob', '1-0')
            RETURNING id;
            """
        )
        game1_id = cur.fetchone()[0]

        # Lances da Alice de Brancas (ply ímpar: 1, 3, 5)
        # 2x BEST (delta=0.0), 1x BLUNDER (delta=30.0) -> média ponderada = (0+0+30)/3 = 10.0
        # (se usasse média das médias seria (0.0 + 30.0)/2 = 15.0)
        cur.executemany(
            """
            INSERT INTO moves (game_id, ply, san, fen_after, category, delta_win_prob)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            [
                (game1_id, 1, "e4", "fen1", "BEST", 0.0),
                (game1_id, 3, "Nf3", "fen3", "BEST", 0.0),
                (game1_id, 5, "Bc4", "fen5", "BLUNDER", 30.0),
            ],
        )

        # Partida 2: Alice joga de Pretas
        cur.execute(
            """
            INSERT INTO games (game_hash, white, black, result)
            VALUES ('hash2', 'Charlie', 'Alice', '0-1')
            RETURNING id;
            """
        )
        game2_id = cur.fetchone()[0]

        # Lances da Alice de Pretas (ply par: 2, 4, 6)
        # 1x GOOD (delta=3.0), 2x INACCURACY (delta=6.0) -> média ponderada = (3.0 + 12.0)/3 = 5.0
        # (se usasse média das médias seria (3.0 + 6.0)/2 = 4.5)
        cur.executemany(
            """
            INSERT INTO moves (game_id, ply, san, fen_after, category, delta_win_prob)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            [
                (game2_id, 2, "c5", "fen2", "GOOD", 3.0),
                (game2_id, 4, "d6", "fen4", "INACCURACY", 6.0),
                (game2_id, 6, "Nc6", "fen6", "INACCURACY", 6.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    results = stats_by_color(temp_db, "Alice")
    assert len(results) == 2

    # Map por group_key para asserções independentes de ordem
    stats_map = {s.group_key: s for s in results}

    white_stats = stats_map["white"]
    assert white_stats.group_type == "color"
    assert white_stats.total_moves == 3
    assert white_stats.avg_delta_win_prob == pytest.approx(10.0, rel=1e-3)
    assert white_stats.category_counts == CategoryCount(best=2, blunder=1, total=3)

    black_stats = stats_map["black"]
    assert black_stats.group_type == "color"
    assert black_stats.total_moves == 3
    assert black_stats.avg_delta_win_prob == pytest.approx(5.0, rel=1e-3)
    assert black_stats.category_counts == CategoryCount(good=1, inaccuracy=2, total=3)


def test_stats_by_opening(temp_db: str) -> None:
    """Valida agregação por abertura (ECO code)."""
    conn = get_connection(temp_db)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO games (game_hash, white, black, result, eco)
            VALUES ('h1', 'Alice', 'Bob', '1-0', 'B20')
            RETURNING id;
            """
        )
        g1_id = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO games (game_hash, white, black, result, eco)
            VALUES ('h2', 'Charlie', 'Alice', '0-1', 'C50')
            RETURNING id;
            """
        )
        g2_id = cur.fetchone()[0]

        # Lances de Alice
        cur.executemany(
            """
            INSERT INTO moves (game_id, ply, san, fen_after, category, delta_win_prob)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            [
                (g1_id, 1, "e4", "fen1", "BEST", 0.0),
                (g1_id, 3, "Nf3", "fen3", "MISTAKE", 14.0),
                (g2_id, 2, "e5", "fen2", "GOOD", 4.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    results = stats_by_opening(temp_db, "Alice")
    assert len(results) == 2

    stats_map = {s.group_key: s for s in results}

    b20 = stats_map["B20"]
    assert b20.group_type == "eco"
    assert b20.total_moves == 2
    assert b20.avg_delta_win_prob == pytest.approx(7.0, rel=1e-3)
    assert b20.category_counts == CategoryCount(best=1, mistake=1, total=2)

    c50 = stats_map["C50"]
    assert c50.group_type == "eco"
    assert c50.total_moves == 1
    assert c50.avg_delta_win_prob == pytest.approx(4.0, rel=1e-3)
    assert c50.category_counts == CategoryCount(good=1, total=1)


def test_stats_by_opening_excludes_null_eco(temp_db: str) -> None:
    """Partidas sem ECO (ECO IS NULL) devem ser excluídas da agregação por abertura."""
    conn = get_connection(temp_db)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO games (game_hash, white, black, result, eco)
            VALUES ('h1', 'Alice', 'Bob', '1-0', NULL)
            RETURNING id;
            """
        )
        g1_id = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO games (game_hash, white, black, result, eco)
            VALUES ('h2', 'Alice', 'Charlie', '1-0', 'A00')
            RETURNING id;
            """
        )
        g2_id = cur.fetchone()[0]

        cur.executemany(
            """
            INSERT INTO moves (game_id, ply, san, fen_after, category, delta_win_prob)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            [
                (g1_id, 1, "e4", "fen1", "BEST", 0.0),
                (g2_id, 1, "a3", "fen2", "GOOD", 3.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    results = stats_by_opening(temp_db, "Alice")
    assert len(results) == 1
    assert results[0].group_key == "A00"
    assert results[0].total_moves == 1
    assert results[0].category_counts == CategoryCount(good=1, total=1)


def test_stats_by_game_phase_aggregation(temp_db: str) -> None:
    """Valida agregação por fase do jogo (OPENING, MIDDLEGAME, ENDGAME)."""
    # FENs sintéticas calibradas:
    # 1. Posição inicial (material 78)
    fen_opening = chess.STARTING_FEN
    # 2. Posição de meio-jogo (material ~48 > 26)
    fen_middlegame = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 6 5"
    # 3. Posição de final (apenas 2 torres e 2 reis = 10 pts <= 26)
    fen_endgame = "8/8/4k3/8/8/4K3/4R3/4r3 w - - 0 1"

    conn = get_connection(temp_db)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO games (game_hash, white, black, result)
            VALUES ('h1', 'Alice', 'Bob', '1-0')
            RETURNING id;
            """
        )
        g1_id = cur.fetchone()[0]

        cur.executemany(
            """
            INSERT INTO moves (game_id, ply, san, fen_after, category, delta_win_prob)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            [
                # ply=5, material 78 -> OPENING
                (g1_id, 5, "Nf3", fen_opening, "BEST", 0.0),
                # ply=31, material 48 -> MIDDLEGAME
                (g1_id, 31, "d4", fen_middlegame, "GOOD", 4.0),
                # ply=45, material 10 -> ENDGAME
                (g1_id, 45, "Re1", fen_endgame, "MISTAKE", 16.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    results = stats_by_game_phase(temp_db, "Alice")
    assert len(results) == 3

    stats_map = {s.group_key: s for s in results}

    assert "OPENING" in stats_map
    assert stats_map["OPENING"].total_moves == 1
    assert stats_map["OPENING"].category_counts == CategoryCount(best=1, total=1)
    assert stats_map["OPENING"].avg_delta_win_prob == pytest.approx(0.0)

    assert "MIDDLEGAME" in stats_map
    assert stats_map["MIDDLEGAME"].total_moves == 1
    assert stats_map["MIDDLEGAME"].category_counts == CategoryCount(good=1, total=1)
    assert stats_map["MIDDLEGAME"].avg_delta_win_prob == pytest.approx(4.0)

    assert "ENDGAME" in stats_map
    assert stats_map["ENDGAME"].total_moves == 1
    assert stats_map["ENDGAME"].category_counts == CategoryCount(mistake=1, total=1)
    assert stats_map["ENDGAME"].avg_delta_win_prob == pytest.approx(16.0)


def test_stats_ignores_unclassified_null_category_moves(temp_db: str) -> None:
    """Lances não classificados (category IS NULL) devem ser ignorados."""
    conn = get_connection(temp_db)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO games (game_hash, white, black, result)
            VALUES ('h1', 'Alice', 'Bob', '1-0')
            RETURNING id;
            """
        )
        g1_id = cur.fetchone()[0]

        cur.executemany(
            """
            INSERT INTO moves (game_id, ply, san, fen_after, category, delta_win_prob)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            [
                (g1_id, 1, "e4", "fen1", "BEST", 0.0),
                (g1_id, 3, "Nf3", "fen3", None, None),
                (g1_id, 5, "Bc4", "fen5", "GOOD", 3.0),
                (g1_id, 7, "d3", "fen7", None, None),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    results = stats_by_color(temp_db, "Alice")
    assert len(results) == 1
    assert results[0].total_moves == 2
    assert results[0].category_counts == CategoryCount(best=1, good=1, total=2)
    assert results[0].avg_delta_win_prob == pytest.approx(1.5)


def test_stats_empty_database_returns_empty_list(temp_db: str) -> None:
    """Banco vazio deve retornar listas vazias sem exceção."""
    assert stats_by_color(temp_db, "Alice") == []
    assert stats_by_opening(temp_db, "Alice") == []
    assert stats_by_game_phase(temp_db, "Alice") == []


def test_stats_nonexistent_player_returns_empty_list(temp_db: str) -> None:
    """Jogador sem partidas no banco deve retornar listas vazias sem exceção."""
    conn = get_connection(temp_db)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO games (game_hash, white, black, result)
            VALUES ('h1', 'Bob', 'Charlie', '1-0')
            RETURNING id;
            """
        )
        g1_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO moves (game_id, ply, san, fen_after, category, delta_win_prob)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (g1_id, 1, "e4", "fen1", "BEST", 0.0),
        )
        conn.commit()
    finally:
        conn.close()

    assert stats_by_color(temp_db, "Alice") == []
    assert stats_by_opening(temp_db, "Alice") == []
    assert stats_by_game_phase(temp_db, "Alice") == []


def test_classify_game_phase_boundaries_and_order() -> None:
    """Valida a ordem de decisão estrita e os thresholds de classify_game_phase."""
    starting_fen = chess.STARTING_FEN
    # Material = 78
    assert count_material(starting_fen) == 78

    # a) ply=10, material 78 -> OPENING
    assert classify_game_phase(10, starting_fen) == GamePhase.OPENING

    # b) ply=20, material 78 -> OPENING (limite exato)
    assert classify_game_phase(20, starting_fen) == GamePhase.OPENING

    # c) ply=21, material 78 -> MIDDLEGAME
    assert classify_game_phase(21, starting_fen) == GamePhase.MIDDLEGAME

    # d) ply=5, material 18 (2 Damas = 18 pts) -> ENDGAME (material <= 26 prevalece sobre ply <= 20)
    queen_fen = "8/8/4k3/8/8/4K3/4Q3/4q3 w - - 0 1"
    assert count_material(queen_fen) == 18
    assert classify_game_phase(5, queen_fen) == GamePhase.ENDGAME

    # e) ply=35, material 26 -> ENDGAME (limite exato de material)
    # 2 Damas (18) + 1 Torre (5) + 3 Peões (3) = 26 pts
    fen_26 = "8/1p1p1p2/4k3/8/8/4K3/4Q3/4q1r1 w - - 0 1"
    assert count_material(fen_26) == 26
    assert classify_game_phase(35, fen_26) == GamePhase.ENDGAME

    # f) ply=35, material 27 -> MIDDLEGAME (limite exato + 1)
    # 2 Damas (18) + 1 Torre (5) + 4 Peões (4) = 27 pts
    fen_27 = "8/1p1p1p1p/4k3/8/8/4K3/4Q3/4q1r1 w - - 0 1"
    assert count_material(fen_27) == 27
    assert classify_game_phase(35, fen_27) == GamePhase.MIDDLEGAME


def test_weighted_average_calculation_scenarios(temp_db: str) -> None:
    """Testa o cálculo ponderado de avg_delta_win_prob em múltiplos cenários sintéticos."""
    conn = get_connection(temp_db)
    try:
        cur = conn.cursor()
        # Cenário A: Grupo "B20" com 2x BLUNDER (30.0 avg) e 1x BEST (0.0 avg) -> média = 20.0
        cur.execute(
            """
            INSERT INTO games (game_hash, white, black, result, eco)
            VALUES ('hA', 'Alice', 'Bob', '1-0', 'B20')
            RETURNING id;
            """
        )
        ga_id = cur.fetchone()[0]
        cur.executemany(
            """
            INSERT INTO moves (game_id, ply, san, fen_after, category, delta_win_prob)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            [
                (ga_id, 1, "e4", "fen1", "BLUNDER", 25.0),
                (ga_id, 3, "Nf3", "fen3", "BLUNDER", 35.0),
                (ga_id, 5, "Bc4", "fen5", "BEST", 0.0),
            ],
        )

        # Cenário B: Grupo "C50" com 1x GOOD (4.0) e 3x MISTAKE (12.0, 16.0, 20.0 -> avg 16.0)
        # Média ponderada = (1*4.0 + 3*16.0)/4 = 52.0 / 4 = 13.0
        cur.execute(
            """
            INSERT INTO games (game_hash, white, black, result, eco)
            VALUES ('hB', 'Alice', 'Charlie', '1-0', 'C50')
            RETURNING id;
            """
        )
        gb_id = cur.fetchone()[0]
        cur.executemany(
            """
            INSERT INTO moves (game_id, ply, san, fen_after, category, delta_win_prob)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            [
                (gb_id, 1, "e4", "fen1", "GOOD", 4.0),
                (gb_id, 3, "Nf3", "fen3", "MISTAKE", 12.0),
                (gb_id, 5, "Bc4", "fen5", "MISTAKE", 16.0),
                (gb_id, 7, "d3", "fen7", "MISTAKE", 20.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    results = stats_by_opening(temp_db, "Alice")
    stats_map = {s.group_key: s for s in results}

    # Cenário A
    assert stats_map["B20"].avg_delta_win_prob == pytest.approx(20.0, rel=1e-3)
    # Cenário B
    assert stats_map["C50"].avg_delta_win_prob == pytest.approx(13.0, rel=1e-3)
```

---

## 2. Output Literal de `pytest -v`

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/cleiton/projetos.pessoais
configfile: pyproject.toml
testpaths: tests
collecting ... collected 68 items

tests/test_analyze.py ......                                             [  8%]
tests/test_classify.py ....................                              [ 38%]
tests/test_db.py .............                                           [ 57%]
tests/test_engine.py .........                                           [ 70%]
tests/test_pgn_import.py ...........                                     [ 86%]
tests/test_stats.py .........                                            [100%]

=============================== warnings summary ===============================
.venv/lib64/python3.14/site-packages/chess/engine.py:54
  /home/cleiton/projetos.pessoais/.venv/lib64/python3.14/site-packages/chess/engine.py:54: DeprecationWarning: 'asyncio.DefaultEventLoopPolicy' is deprecated and slated for removal in Python 3.16
    EventLoopPolicy = asyncio.DefaultEventLoopPolicy

tests/test_analyze.py: 1 warning
tests/test_engine.py: 10 warnings
  /home/cleiton/projetos.pessoais/.venv/lib64/python3.14/site-packages/chess/engine.py:65: DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead
    assert asyncio.iscoroutinefunction(coroutine)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================= 68 passed, 12 warnings in 7.15s ========================
```

---

## 3. Output Literal de `ruff check`

```
All checks passed!
```

---

## 4. Output Literal de `mypy`

```
Success: no issues found in 15 source files
```
