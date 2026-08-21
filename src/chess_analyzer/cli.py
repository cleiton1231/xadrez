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
