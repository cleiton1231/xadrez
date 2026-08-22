# Relatório da Etapa 8 — `cli.py` (Integração CLI via Typer)

## 1. Diff / Conteúdo Completo dos Arquivos Criados e Modificados

### `src/chess_analyzer/cli.py` (Criado)

```python
"""Interface de linha de comando (CLI) do Chess Performance Analyzer via Typer."""

from __future__ import annotations

import json
from dataclasses import asdict
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import chess.engine
import typer
from rich.console import Console
from rich.table import Table

from chess_analyzer.analyze import analyze_games
from chess_analyzer.db import get_connection, init_db, save_games
from chess_analyzer.engine import StockfishEngine
from chess_analyzer.pgn_import import parse_pgn_file
from chess_analyzer.stats import (
    AggregatedStats,
    stats_by_color,
    stats_by_game_phase,
    stats_by_opening,
)


class StatsDimension(StrEnum):
    """Dimensões de agregação disponíveis para o comando stats."""

    ALL = "all"
    COLOR = "color"
    OPENING = "opening"
    PHASE = "phase"


app = typer.Typer(
    name="chess-analyzer",
    help="Análise agregada de histórico de partidas de xadrez com Stockfish.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


def _render_stats_table(title: str, key_header: str, stats_list: list[AggregatedStats]) -> None:
    """Renderiza uma tabela formatada no console com estatísticas agregadas."""
    table = Table(title=title)
    table.add_column(key_header, style="cyan")
    table.add_column("Lances", justify="right")
    table.add_column("Best", justify="right", style="green")
    table.add_column("Exc", justify="right", style="green")
    table.add_column("Good", justify="right")
    table.add_column("Inac", justify="right", style="yellow")
    table.add_column("Mist", justify="right", style="magenta")
    table.add_column("Blund", justify="right", style="red")
    table.add_column("Perda Média (ΔWin%)", justify="right", style="bold")

    for s in stats_list:
        c = s.category_counts
        table.add_row(
            s.group_key,
            str(s.total_moves),
            str(c.best),
            str(c.excellent),
            str(c.good),
            str(c.inaccuracy),
            str(c.mistake),
            str(c.blunder),
            f"{s.avg_delta_win_prob:.2f}%",
        )
    console.print(table)


@app.command("import")
def import_cmd(
    pgn_path: Annotated[
        Path,
        typer.Argument(
            help="Caminho para o arquivo PGN a ser importado.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    db_path: Annotated[
        str,
        typer.Option(
            "--db",
            "-d",
            help="Caminho do banco de dados SQLite local.",
        ),
    ] = "data/chess_analyzer.db",
) -> None:
    """Importa partidas de um arquivo PGN para o banco SQLite local."""
    try:
        games_iter = parse_pgn_file(str(pgn_path))
        stats = save_games(games_iter, db_path)
    except ValueError as e:
        err_console.print(f"[bold red]Erro ao importar PGN:[/bold red] {e}")
        raise typer.Exit(1) from e
    except Exception as e:
        err_console.print(f"[bold red]Erro inesperado na importação:[/bold red] {e}")
        raise typer.Exit(1) from e

    console.print("[bold green]Importação concluída com sucesso![/bold green]")
    console.print(
        f"Total processado: {stats.total} | "
        f"Inseridas: {stats.inserted} | "
        f"Ignoradas/Duplicadas: {stats.skipped}"
    )


@app.command("analyze")
def analyze_cmd(
    db_path: Annotated[
        str,
        typer.Option(
            "--db",
            "-d",
            help="Caminho do banco de dados SQLite local.",
        ),
    ] = "data/chess_analyzer.db",
    engine_path: Annotated[
        str,
        typer.Option(
            "--engine-path",
            "-e",
            help="Caminho para o executável do Stockfish.",
        ),
    ] = ".venv/bin/stockfish",
    depth: Annotated[
        int,
        typer.Option(
            "--depth",
            help="Profundidade de busca do Stockfish por posição.",
        ),
    ] = 12,
) -> None:
    """Analisa todas as posições pendentes no banco usando Stockfish."""
    try:
        engine = StockfishEngine(path=engine_path, depth=depth)
        with engine:
            stats = analyze_games(db_path=db_path, engine=engine, target_depth=depth)
    except (FileNotFoundError, chess.engine.EngineError, chess.engine.EngineTerminatedError) as e:
        err_console.print(
            f"[bold red]Erro:[/bold red] Não foi possível iniciar o Stockfish em '{engine_path}'. "
            f"Verifique se o binário existe ou use --engine-path. ({e})"
        )
        raise typer.Exit(1) from e
    except Exception as e:
        err_console.print(f"[bold red]Erro durante a análise:[/bold red] {e}")
        raise typer.Exit(1) from e

    console.print("[bold green]Análise concluída com sucesso![/bold green]")
    console.print(
        f"Partidas analisadas: {stats.analyzed_games}/{stats.total_games} | "
        f"Lances avaliados: {stats.analyzed_moves}"
    )


@app.command("stats")
def stats_cmd(
    player: Annotated[
        str,
        typer.Argument(
            help="Nome do jogador para agregação das estatísticas.",
        ),
    ],
    db_path: Annotated[
        str,
        typer.Option(
            "--db",
            "-d",
            help="Caminho do banco de dados SQLite local.",
        ),
    ] = "data/chess_analyzer.db",
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Exporta as estatísticas agregadas em formato JSON estruturado.",
        ),
    ] = False,
    by: Annotated[
        StatsDimension,
        typer.Option(
            "--by",
            help="Dimensão de agregação: 'color', 'opening', 'phase' ou 'all'.",
        ),
    ] = StatsDimension.ALL,
) -> None:
    """Exibe estatísticas agregadas por cor, abertura e fase do jogo."""
    init_db(db_path)

    # Verificação de lances pendentes para o jogador
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM moves m
            JOIN games g ON g.id = m.game_id
            WHERE m.category IS NULL
              AND (
                  (g.white = :player AND m.ply % 2 != 0)
               OR (g.black = :player AND m.ply % 2 = 0)
              );
            """,
            {"player": player},
        )
        pending_count = cur.fetchone()[0]
    finally:
        conn.close()

    if pending_count > 0:
        err_console.print(
            f"[yellow]Aviso: Existem {pending_count} lances pendentes de análise para o "
            f"jogador '{player}'. Execute 'chess-analyzer analyze' para análise completa.[/yellow]"
        )

    color_stats = (
        stats_by_color(db_path, player)
        if by in (StatsDimension.ALL, StatsDimension.COLOR)
        else []
    )
    opening_stats = (
        stats_by_opening(db_path, player)
        if by in (StatsDimension.ALL, StatsDimension.OPENING)
        else []
    )
    phase_stats = (
        stats_by_game_phase(db_path, player)
        if by in (StatsDimension.ALL, StatsDimension.PHASE)
        else []
    )

    all_empty = not color_stats and not opening_stats and not phase_stats

    if json_output:
        total_moves = 0
        if color_stats:
            total_moves = sum(s.total_moves for s in color_stats)
        elif opening_stats:
            total_moves = sum(s.total_moves for s in opening_stats)
        elif phase_stats:
            total_moves = sum(s.total_moves for s in phase_stats)

        payload: dict[str, Any] = {
            "player": player,
            "total_analyzed_moves": total_moves,
        }
        if by in (StatsDimension.ALL, StatsDimension.COLOR):
            payload["color"] = [asdict(s) for s in color_stats]
        if by in (StatsDimension.ALL, StatsDimension.OPENING):
            payload["opening"] = [asdict(s) for s in opening_stats]
        if by in (StatsDimension.ALL, StatsDimension.PHASE):
            payload["game_phase"] = [asdict(s) for s in phase_stats]

        print(json.dumps(payload, indent=2))
        return

    if all_empty:
        msg = (
            f"[bold yellow]Nenhuma partida analisada encontrada para o "
            f"jogador '{player}'.[/bold yellow]"
        )
        console.print(msg)
        console.print(
            "Importe partidas com 'chess-analyzer import' e execute 'chess-analyzer analyze'."
        )
        return

    if color_stats:
        _render_stats_table("Estatísticas por Cor", "Cor", color_stats)
    if opening_stats:
        _render_stats_table("Estatísticas por Abertura (ECO)", "ECO", opening_stats)
    if phase_stats:
        _render_stats_table("Estatísticas por Fase do Jogo", "Fase", phase_stats)
```

### `tests/test_cli.py` (Criado)

```python
"""Testes de integração para a interface CLI (cli.py)."""

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chess_analyzer.cli import app
from chess_analyzer.db import get_connection

STOCKFISH_PATH = ".venv/bin/stockfish"


def get_fixture_path(filename: str) -> str:
    """Retorna o caminho absoluto para um arquivo de fixture."""
    return os.path.join(os.path.dirname(__file__), "fixtures", filename)


@pytest.fixture
def runner() -> CliRunner:
    """Instância do CliRunner para invocações de comando."""
    return CliRunner()


@pytest.fixture
def temp_db(tmp_path: Path) -> str:
    """Cria o caminho para um banco de dados temporário."""
    return str(tmp_path / "test_cli.db")


# 1. test_cli_import_valid_pgn
def test_cli_import_valid_pgn(runner: CliRunner, temp_db: str) -> None:
    """Import de arquivo PGN válido deve retornar exit code 0 e persistir dados."""
    pgn_path = get_fixture_path("lichess_real.pgn")
    result = runner.invoke(app, ["import", pgn_path, "--db", temp_db])

    assert result.exit_code == 0
    assert "Importação concluída" in result.stdout

    conn = get_connection(temp_db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM games;")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT COUNT(*) FROM moves;")
        assert cur.fetchone()[0] == 3
    finally:
        conn.close()


# 2. test_cli_import_missing_file_exit_code_2
def test_cli_import_missing_file_exit_code_2(runner: CliRunner, temp_db: str) -> None:
    """Tentativa de importar arquivo inexistente deve falhar com exit code 2 (validação Typer)."""
    result = runner.invoke(app, ["import", "/caminho/inexistente/games.pgn", "--db", temp_db])
    assert result.exit_code == 2


# 3. test_cli_import_corrupted_file_exit_code_1
def test_cli_import_corrupted_file_exit_code_1(runner: CliRunner, temp_db: str) -> None:
    """Import de arquivo com lixo binário deve capturar ValueError e sair com exit code 1."""
    pgn_path = get_fixture_path("binary.pgn")
    result = runner.invoke(app, ["import", pgn_path, "--db", temp_db])

    assert result.exit_code == 1
    assert "Erro ao importar PGN" in result.stderr


# 4. test_cli_analyze_flow
def test_cli_analyze_flow(runner: CliRunner, temp_db: str) -> None:
    """Analyze com Stockfish real deve classificar lances pendentes com exit code 0."""
    pgn_path = get_fixture_path("lichess_real.pgn")
    runner.invoke(app, ["import", pgn_path, "--db", temp_db])

    result = runner.invoke(
        app,
        ["analyze", "--db", temp_db, "--engine-path", STOCKFISH_PATH, "--depth", "10"],
    )
    assert result.exit_code == 0
    assert "Análise concluída" in result.stdout

    conn = get_connection(temp_db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM moves WHERE category IS NOT NULL;")
        assert cur.fetchone()[0] == 3
    finally:
        conn.close()


# 5. test_cli_analyze_missing_engine_exit_code_1
def test_cli_analyze_missing_engine_exit_code_1(runner: CliRunner, temp_db: str) -> None:
    """Analyze com Stockfish inexistente deve sair com exit code 1 e mensagem explicativa."""
    result = runner.invoke(
        app,
        ["analyze", "--db", temp_db, "--engine-path", "/caminho/inexistente/stockfish"],
    )
    assert result.exit_code == 1
    assert "Não foi possível iniciar o Stockfish" in result.stderr


# 6. test_cli_stats_table_output
def test_cli_stats_table_output(runner: CliRunner, temp_db: str) -> None:
    """Stats no modo padrão deve renderizar tabelas ricas com dados analisados."""
    pgn_path = get_fixture_path("lichess_real.pgn")
    runner.invoke(app, ["import", pgn_path, "--db", temp_db])
    runner.invoke(
        app,
        ["analyze", "--db", temp_db, "--engine-path", STOCKFISH_PATH, "--depth", "10"],
    )

    result = runner.invoke(app, ["stats", "Player1", "--db", temp_db])
    assert result.exit_code == 0
    assert "Estatísticas por Cor" in result.stdout
    assert "Estatísticas por Abertura" in result.stdout
    assert "Estatísticas por Fase do Jogo" in result.stdout


# 7. test_cli_stats_json_output
def test_cli_stats_json_output(runner: CliRunner, temp_db: str) -> None:
    """Stats com --json deve retornar JSON estruturado com as 3 dimensões (all)."""
    pgn_path = get_fixture_path("lichess_real.pgn")
    runner.invoke(app, ["import", pgn_path, "--db", temp_db])
    runner.invoke(
        app,
        ["analyze", "--db", temp_db, "--engine-path", STOCKFISH_PATH, "--depth", "10"],
    )

    result = runner.invoke(app, ["stats", "Player1", "--db", temp_db, "--json"])
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert data["player"] == "Player1"
    assert "color" in data
    assert "opening" in data
    assert "game_phase" in data
    assert len(data["color"]) > 0


# 8. test_cli_stats_json_filtered_by
def test_cli_stats_json_filtered_by(runner: CliRunner, temp_db: str) -> None:
    """Stats com --json e --by color deve retornar JSON contendo apenas a chave 'color'."""
    pgn_path = get_fixture_path("lichess_real.pgn")
    runner.invoke(app, ["import", pgn_path, "--db", temp_db])
    runner.invoke(
        app,
        ["analyze", "--db", temp_db, "--engine-path", STOCKFISH_PATH, "--depth", "10"],
    )

    result = runner.invoke(app, ["stats", "Player1", "--db", temp_db, "--json", "--by", "color"])
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert "color" in data
    assert "opening" not in data
    assert "game_phase" not in data


# 9. test_cli_stats_invalid_by_option_exit_code_2
def test_cli_stats_invalid_by_option_exit_code_2(runner: CliRunner, temp_db: str) -> None:
    """Opção inválida para --by deve falhar com exit code 2 via validação automática do Enum."""
    result = runner.invoke(app, ["stats", "Player1", "--db", temp_db, "--by", "invalido"])
    assert result.exit_code == 2


# 10. test_cli_stats_warning_in_stderr_for_json
def test_cli_stats_warning_in_stderr_for_json(runner: CliRunner, temp_db: str) -> None:
    """Lances pendentes devem emitir aviso em stderr mantendo stdout como JSON válido e limpo."""
    pgn_path = get_fixture_path("lichess_real.pgn")
    # Importa mas NÃO analisa
    runner.invoke(app, ["import", pgn_path, "--db", temp_db])

    result = runner.invoke(app, ["stats", "Player1", "--db", temp_db, "--json"])
    assert result.exit_code == 0

    # stderr contém o aviso
    assert "pendentes de análise" in result.stderr

    # stdout é JSON 100% válido
    data = json.loads(result.stdout)
    assert data["player"] == "Player1"


# 11. test_cli_stats_nonexistent_player
def test_cli_stats_nonexistent_player(runner: CliRunner, temp_db: str) -> None:
    """Consulta para jogador inexistente deve retornar exit code 0 e mensagem amigável."""
    pgn_path = get_fixture_path("lichess_real.pgn")
    runner.invoke(app, ["import", pgn_path, "--db", temp_db])
    runner.invoke(
        app,
        ["analyze", "--db", temp_db, "--engine-path", STOCKFISH_PATH, "--depth", "10"],
    )

    result = runner.invoke(app, ["stats", "JogadorFantasma", "--db", temp_db])
    assert result.exit_code == 0
    assert "Nenhuma partida analisada encontrada" in result.stdout


# 12. test_cli_end_to_end_dod_fase1
def test_cli_end_to_end_dod_fase1(runner: CliRunner, temp_db: str) -> None:
    """Pipeline completo de ponta a ponta da Fase 1 (import -> analyze -> stats)."""
    pgn_path = get_fixture_path("lichess_real.pgn")

    # Passo 1: Import
    res_import = runner.invoke(app, ["import", pgn_path, "--db", temp_db])
    assert res_import.exit_code == 0

    # Passo 2: Analyze com Stockfish real
    res_analyze = runner.invoke(
        app,
        ["analyze", "--db", temp_db, "--engine-path", STOCKFISH_PATH, "--depth", "10"],
    )
    assert res_analyze.exit_code == 0

    # Passo 3: Stats
    res_stats = runner.invoke(app, ["stats", "Player1", "--db", temp_db, "--json"])
    assert res_stats.exit_code == 0

    data = json.loads(res_stats.stdout)
    assert data["player"] == "Player1"
    assert data["total_analyzed_moves"] > 0
```

### `GEMINI.md` (Modificado)

```diff
diff --git a/GEMINI.md b/GEMINI.md
index 720ec5d..dfffd28 100644
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
@@ -79,6 +80,7 @@ chess-analyzer/
 │   ├── test_engine.py
 │   ├── test_pgn_import.py
 │   ├── test_db.py
+│   ├── test_analyze.py
 │   ├── test_stats.py
 │   └── fixtures/            # PGNs de teste, posições conhecidas
 ├── data/                     # gitignored — partidas reais do usuário, .db local
@@ -143,8 +145,8 @@ foi confiabilidade do agente, não falta de ferramenta.
 
 ## 8. Definição de pronto (DoD) por fase
 
-**Fase 1:** roda `chess-analyzer import partidas.pgn` e
-`chess-analyzer stats`, retorna números reais de um PGN de teste conhecido,
+**Fase 1:** roda `chess-analyzer import partidas.pgn`, `chess-analyzer analyze` e
+`chess-analyzer stats <jogador>`, retorna números reais de um PGN de teste conhecido,
 com testes unitários cobrindo classificação e agregação passando via
 `pytest`, sem mock do Stockfish nos testes de integração (testes de
 classificação podem mockar eval, mas pelo menos um teste de ponta a ponta
```

---

## 2. Output Literal de `pytest -v`

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/cleiton/projetos.pessoais
configfile: pyproject.toml
testpaths: tests
collecting ... collected 80 items

tests/test_analyze.py ......                                             [  7%]
tests/test_classify.py ....................                              [ 32%]
tests/test_cli.py ............                                           [ 47%]
tests/test_db.py .............                                           [ 63%]
tests/test_engine.py .........                                           [ 75%]
tests/test_pgn_import.py ...........                                     [ 88%]
tests/test_stats.py .........                                            [100%]

=============================== warnings summary ===============================
.venv/lib64/python3.14/site-packages/chess/engine.py:54
  /home/cleiton/projetos.pessoais/.venv/lib64/python3.14/site-packages/chess/engine.py:54: DeprecationWarning: 'asyncio.DefaultEventLoopPolicy' is deprecated and slated for removal in Python 3.16
    EventLoopPolicy = asyncio.DefaultEventLoopPolicy

tests/test_analyze.py: 1 warning
tests/test_cli.py: 7 warnings
tests/test_engine.py: 10 warnings
  /home/cleiton/projetos.pessoais/.venv/lib64/python3.14/site-packages/chess/engine.py:65: DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead
    assert asyncio.iscoroutinefunction(coroutine)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================= 80 passed, 19 warnings in 9.91s ========================
```

---

## 3. Output Literal de `ruff check .`

```
All checks passed!
```

---

## 4. Output Literal de `mypy src tests`

```
Success: no issues found in 17 source files
```
