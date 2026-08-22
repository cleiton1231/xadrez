# Relatório de Execução — Etapa 6: Orquestração de Análise

## 1. Diffs brutos dos arquivos criados/modificados

### `src/chess_analyzer/analyze.py`
```python
"""Orquestrador de análise integrando Stockfish, classificação de lances e persistência SQLite."""

from dataclasses import dataclass

import chess

from chess_analyzer.classify import PositionEvaluation, classify_move
from chess_analyzer.db import get_connection, get_evaluation, init_db, save_evaluation
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
    Persiste a avaliação com depth=target_depth. Conforme documentado em engine.py (linhas 29-35),
    depth=12 conclui rotineiramente em ~6ms a ~50ms e move_time_limit (2.0s) atua como safety valve.
    Em caso de timeout raro, o engine emite warning em log e a avaliação é gravada sob target_depth;
    decisão aceita para a Fase 1 mantendo a estabilidade dos contratos de PositionEvaluation.
    """
    cached = get_evaluation(db_path, fen, min_depth=target_depth)
    if cached is not None:
        return PositionEvaluation(white_cp=cached[0], mate_for_white=cached[1])

    pos_eval = engine.evaluate(chess.Board(fen))
    save_evaluation(
        db_path=db_path,
        fen=fen,
        depth=target_depth,
        eval_cp=pos_eval.white_cp,
        eval_mate=pos_eval.mate_for_white,
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

        # Avaliação da posição inicial do tabuleiro (para compor o eval_before do ply 1)
        eval_before = _get_or_evaluate_fen(
            db_path=db_path,
            fen=chess.STARTING_FEN,
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
```

### `tests/test_analyze.py`
```python
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

STOCKFISH_PATH = ".venv/bin/stockfish"


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

    mock_engine = MagicMock(spec=StockfishEngine)
    mock_engine.depth = 12
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
    )
    assert eval_init == (20, None, 12)

    eval_m1 = get_evaluation(
        temp_db,
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        min_depth=12,
    )
    assert eval_m1 == (35, None, 12)


def test_analyze_cache_hit_bypasses_engine(temp_db: str, sample_game: ParsedGame) -> None:
    """Posições já avaliadas em evaluations com depth >= target_depth devem dar cache hit."""
    save_games([sample_game], temp_db)

    # Pré-popula o cache evaluations com os 4 campos FEN canônicos
    conn = get_connection(temp_db)
    with conn:
        conn.execute(
            "INSERT INTO evaluations (fen, depth, eval_cp, eval_mate) VALUES (?, ?, ?, ?)",
            ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -", 12, 15, None),
        )
        conn.execute(
            "INSERT INTO evaluations (fen, depth, eval_cp, eval_mate) VALUES (?, ?, ?, ?)",
            ("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq -", 12, 40, None),
        )
        conn.execute(
            "INSERT INTO evaluations (fen, depth, eval_cp, eval_mate) VALUES (?, ?, ?, ?)",
            ("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -", 12, 35, None),
        )
    conn.close()

    mock_engine = MagicMock(spec=StockfishEngine)
    mock_engine.depth = 12

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

    mock_engine = MagicMock(spec=StockfishEngine)
    mock_engine.depth = 12
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

    mock_engine = MagicMock(spec=StockfishEngine)
    mock_engine.depth = 12
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

    mock_engine = MagicMock(spec=StockfishEngine)
    mock_engine.depth = 12
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


def test_analyze_real_stockfish_end_to_end(temp_db: str) -> None:
    """Teste de ponta a ponta sem mock, rodando o Stockfish real com fixture PGN."""
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
```

### `GEMINI.md`
```diff
--- a/GEMINI.md
+++ b/GEMINI.md
@@ -70,6 +70,7 @@ chess-analyzer/
 │   ├── pgn_import.py      # parsing e normalização de PGN
 │   ├── engine.py           # wrapper do Stockfish (UCI)
 │   ├── classify.py         # classificação de lance (core — TDD obrigatório)
+│   ├── analyze.py          # orquestrador de análise conectando engine, classify e db
 │   ├── db.py               # persistência local em SQLite e cache FEN
 │   ├── stats.py             # agregação estatística
 │   ├── puzzles.py           # Fase 2 — dataset Lichess
@@ -78,6 +79,7 @@ chess-analyzer/
 │   ├── test_engine.py
 │   ├── test_pgn_import.py
 │   ├── test_db.py
+│   ├── test_analyze.py
 │   ├── test_stats.py
 │   └── fixtures/            # PGNs de teste, posições conhecidas
 ├── data/                     # gitignored — partidas reais do usuário, .db local
```

### `.gitignore`
```diff
--- a/.gitignore
+++ b/.gitignore
@@ -27,3 +27,6 @@ htmlcov/
 .vscode/
 .idea/
 
+# Reports gerados por etapa
+docs/reports/
+
```

---

## 2. Output literal de `pytest -v`

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/cleiton/projetos.pessoais
configfile: pyproject.toml
testpaths: tests
collecting ... collected 59 items                                                             

tests/test_analyze.py ......                                             [ 10%]
tests/test_classify.py ....................                              [ 44%]
tests/test_db.py .............                                           [ 66%]
tests/test_engine.py .........                                           [ 81%]
tests/test_pgn_import.py ...........                                     [100%]

=============================== warnings summary ===============================
.venv/lib64/python3.14/site-packages/chess/engine.py:54
  /home/cleiton/projetos.pessoais/.venv/lib64/python3.14/site-packages/chess/engine.py:54: DeprecationWarning: 'asyncio.DefaultEventLoopPolicy' is deprecated and slated for removal in Python 3.16
    EventLoopPolicy = asyncio.DefaultEventLoopPolicy

tests/test_analyze.py: 1 warning
tests/test_engine.py: 10 warnings
  /home/cleiton/projetos.pessoais/.venv/lib64/python3.14/site-packages/chess/engine.py:65: DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead
    assert asyncio.iscoroutinefunction(coroutine)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================= 59 passed, 12 warnings in 7.00s ========================
```

---

## 3. Output literal de `ruff check .`

```
All checks passed!
```

---

## 4. Output literal de `mypy src tests`

```
Success: no issues found in 13 source files
```
