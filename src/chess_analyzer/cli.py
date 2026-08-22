"""Interface de linha de comando (CLI) do Chess Performance Analyzer via Typer."""

from __future__ import annotations

import json
import os
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
from chess_analyzer.puzzles import (
    LICHESS_PUZZLE_URL,
    download_puzzle_dataset,
    generate_training_session,
    index_puzzles,
)
from chess_analyzer.stats import (
    AggregatedStats,
    GamePhase,
    stats_by_color,
    stats_by_game_phase,
    stats_by_opening,
)

_DEFAULT_DB_PATH: str = os.environ.get("CHESS_ANALYZER_DB", "data/chess_analyzer.db")




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
    ] = _DEFAULT_DB_PATH,
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
    ] = _DEFAULT_DB_PATH,
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
    ] = _DEFAULT_DB_PATH,
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


@app.command("train")
def train_cmd(
    player: Annotated[
        str,
        typer.Argument(
            help="Nome do jogador para gerar a sessão de treino direcionada.",
        ),
    ],
    count: Annotated[
        int,
        typer.Option(
            "--count",
            "-n",
            help="Quantidade de puzzles a gerar na sessão.",
        ),
    ] = 10,
    phase: Annotated[
        str | None,
        typer.Option(
            "--phase",
            "-p",
            help="Força o treino em uma fase específica: 'opening', 'middlegame' ou 'endgame'.",
        ),
    ] = None,
    rating_window: Annotated[
        int,
        typer.Option(
            "--rating-window",
            "-w",
            help="Margem de variação de rating em torno do Elo do jogador.",
        ),
    ] = 200,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Exporta a sessão de treino em formato JSON.",
        ),
    ] = False,
    db_path: Annotated[
        str,
        typer.Option(
            "--db",
            "-d",
            help="Caminho do banco de dados SQLite local.",
        ),
    ] = _DEFAULT_DB_PATH,
) -> None:
    """Gera sessão de treino personalizada baseada nas fraquezas do jogador."""
    forced_phase_enum: GamePhase | None = None
    if phase:
        try:
            forced_phase_enum = GamePhase(phase.upper())
        except ValueError as err:
            err_console.print(
                f"[bold red]Fase inválida '{phase}'. "
                f"Opções: opening, middlegame, endgame.[/bold red]"
            )
            raise typer.Exit(1) from err

    session = generate_training_session(
        db_path=db_path,
        player_name=player,
        count=count,
        rating_window=rating_window,
        forced_phase=forced_phase_enum,
    )

    if session is None:
        err_console.print(
            f"[yellow]Não foi possível gerar treino para '{player}'. "
            f"Verifique se há partidas analisadas ou se há puzzles indexados.[/yellow]"
        )
        raise typer.Exit(1)

    if json_output:
        payload = {
            "player": session.player_name,
            "weakest_phase": session.weakest_phase.value,
            "target_theme": session.target_theme,
            "diagnosis": {
                "avg_delta_win_prob": round(session.avg_delta_win_prob, 2),
                "blunder_count": session.blunder_count,
                "total_moves_in_phase": session.total_moves_in_phase,
                "player_elo": session.player_elo,
                "elo_sample_size": session.elo_sample_size,
                "requested_count": session.requested_count,
                "delivered_count": len(session.puzzles),
            },
            "puzzles": [asdict(p) for p in session.puzzles],
        }
        if len(session.puzzles) < session.requested_count:
            payload["warning"] = (
                f"Apenas {len(session.puzzles)} de {session.requested_count} puzzles "
                f"encontrados na faixa de rating."
            )
        print(json.dumps(payload, indent=2))
        return

    # Renderização no console via Rich Table
    console.print(
        f"\n[bold green]🎯 Treino Direcionado para:[/bold green] "
        f"[bold cyan]{session.player_name}[/bold cyan]"
    )
    diag = (
        f"Fase mais fraca identificada: [bold magenta]{session.weakest_phase.value}[/bold magenta] "
        f"(Perda média: [bold red]{session.avg_delta_win_prob:.2f}% ΔWin%[/bold red], "
        f"[red]{session.blunder_count} Blunders[/red] em {session.total_moves_in_phase} lances)"
    )
    console.print(diag)
    if session.player_elo:
        sample_label = (
            f"baseado em {session.elo_sample_size} partida"
            + ("s" if session.elo_sample_size > 1 else "")
        )
        elo_str = f"{session.player_elo} ({sample_label})"
    else:
        elo_str = "N/A"
    console.print(
        f"Elo estimado: [cyan]{elo_str}[/cyan] | "
        f"Tema dos puzzles: [cyan]{session.target_theme}[/cyan]\n"
    )

    if not session.puzzles:
        console.print(
            "[yellow]Nenhum puzzle encontrado para o tema e faixa "
            "de rating correspondentes.[/yellow]"
        )
        return

    if len(session.puzzles) < session.requested_count:
        console.print(
            f"[yellow]Aviso: Apenas {len(session.puzzles)} de {session.requested_count} puzzles "
            f"encontrados na faixa de rating calibrada.[/yellow]\n"
        )


    table = Table(title=f"Puzzles Selecionados ({len(session.puzzles)} exercícios)")
    table.add_column("#", justify="right", style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Rating", justify="right", style="green")
    table.add_column("Lance Oponente", style="yellow")
    table.add_column("Solução", style="bold green")
    table.add_column("FEN de Treino")

    for i, p in enumerate(session.puzzles, 1):
        sol_str = " ".join(p.solution_san)
        table.add_row(
            str(i),
            p.puzzle_id,
            str(p.rating),
            p.opponent_move_san,
            sol_str,
            p.training_fen,
        )
    console.print(table)


# ── Grupo de comandos: puzzles ─────────────────────────────────────────────────


puzzles_app = typer.Typer(
    name="puzzles",
    help="Gerencia o dataset local de puzzles do Lichess.",
    no_args_is_help=True,
)
app.add_typer(puzzles_app, name="puzzles")


@puzzles_app.command("index")
def puzzles_index_cmd(
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            "-f",
            help="Caminho para o arquivo lichess_db_puzzle.csv.zst local.",
        ),
    ] = None,
    download: Annotated[
        bool,
        typer.Option("--download", help="Baixa o dataset antes de indexar."),
    ] = False,
    db_path: Annotated[
        str,
        typer.Option("--db", "-d", help="Caminho do banco de dados SQLite local."),
    ] = _DEFAULT_DB_PATH,
    batch_size: Annotated[
        int,
        typer.Option("--batch-size", help="Tamanho do lote transacional de inserção."),
    ] = 5000,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-indexa mesmo que SHA-256 seja idêntico."),
    ] = False,
) -> None:
    """Indexa o dataset de puzzles do Lichess no banco SQLite local."""
    if download:
        console.print(f"Baixando dataset de {LICHESS_PUZZLE_URL} ...")
        try:
            zst_path = download_puzzle_dataset(dest_dir="data")
        except (OSError, RuntimeError) as e:
            err_console.print(f"[bold red]Erro no download:[/bold red] {e}")
            raise typer.Exit(1) from e
        console.print(f"[green]Download concluído:[/green] {zst_path}")
    elif file is not None:
        zst_path = file
    else:
        err_console.print(
            "[bold red]Informe --file <caminho> ou use --download.[/bold red]"
        )
        raise typer.Exit(1)

    if not zst_path.exists():
        err_console.print(f"[bold red]Arquivo não encontrado:[/bold red] {zst_path}")
        raise typer.Exit(1)

    console.print(f"Indexando puzzles de [cyan]{zst_path}[/cyan] ...")
    try:
        stats = index_puzzles(
            zst_path=zst_path,
            db_path=db_path,
            batch_size=batch_size,
            force=force,
        )
    except Exception as e:
        err_console.print(f"[bold red]Erro na indexação:[/bold red] {e}")
        raise typer.Exit(1) from e

    if stats.skipped:
        console.print(
            "[yellow]Dataset já indexado com este arquivo (SHA-256 idêntico).[/yellow]\n"
            "Use --force para re-indexar."
        )
        return

    console.print(
        f"[bold green]Indexação concluída![/bold green] "
        f"Puzzles inseridos: {stats.inserted:,}"
    )


@puzzles_app.command("status")
def puzzles_status_cmd(
    db_path: Annotated[
        str,
        typer.Option("--db", "-d", help="Caminho do banco de dados SQLite local."),
    ] = _DEFAULT_DB_PATH,
) -> None:
    """Exibe metadados da indexação de puzzles atual."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM puzzle_index_meta ORDER BY key;")
        rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM puzzles;")
        count_row = cur.fetchone()
    except Exception:
        console.print("[yellow]Nenhum dataset indexado ainda.[/yellow]")
        return
    finally:
        conn.close()

    if not rows:
        console.print("[yellow]Nenhum dataset indexado ainda.[/yellow]")
        return

    table = Table(title="Status do Dataset de Puzzles")
    table.add_column("Chave", style="cyan")
    table.add_column("Valor")
    for key, value in rows:
        table.add_row(key, value)
    table.add_row("puzzles_in_db", str(count_row[0]) if count_row else "0")
    console.print(table)
