"""Módulo de treino direcionado com dataset de puzzles do Lichess (Fase 2).

Pipeline: download atômico → SHA-256 → verificação idempotência → streaming → batch insert.
"""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chess
import zstandard

from chess_analyzer.db import (
    get_connection,
    get_puzzle_index_sha256,
    init_db,
    save_puzzle_index_meta,
)
from chess_analyzer.stats import AggregatedStats, GamePhase

LICHESS_PUZZLE_URL: str = "https://database.lichess.org/lichess_db_puzzle.csv.zst"


@dataclass(frozen=True)
class IndexStats:
    """Resultado da operação de indexação de puzzles."""

    total: int
    inserted: int
    skipped: bool  # True se abortado por SHA-256 idêntico (sem re-indexação)


@dataclass(frozen=True)
class PuzzleItem:
    """Item individual de uma sessão de treino."""

    puzzle_id: str
    fen_before: str           # FEN original (antes do lance do oponente)
    opponent_move_uci: str    # Lance do oponente (moves[0])
    opponent_move_san: str    # Lance do oponente em SAN
    training_fen: str         # FEN da posição de treino apresentada ao jogador
    solution_uci: list[str]   # Sequência da solução em lances UCI (moves[1:])
    solution_san: list[str]   # Sequência da solução em SAN legível
    rating: int               # Rating do puzzle
    popularity: int           # Popularidade no Lichess
    themes: list[str]         # Temas associados
    game_url: str | None      # URL de origem no Lichess


@dataclass(frozen=True)
class TrainingSession:
    """Sessão de treino completa direcionada para as fraquezas do jogador."""

    player_name: str
    weakest_phase: GamePhase
    target_theme: str         # 'opening', 'middlegame' ou 'endgame'
    avg_delta_win_prob: float
    blunder_count: int
    total_moves_in_phase: int
    player_elo: int | None
    elo_sample_size: int
    requested_count: int
    puzzles: list[PuzzleItem]


def compute_file_sha256(path: Path) -> str:
    """Calcula SHA-256 do arquivo em chunks de 64 KB sem carregar na memória inteira."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def download_puzzle_dataset(dest_dir: str, url: str = LICHESS_PUZZLE_URL) -> Path:
    """Baixa o arquivo .csv.zst para dest_dir de forma atômica.

    Protocolo:
    1. Verifica espaço em disco (mínimo 3.5 GB livres).
    2. Baixa para arquivo temporário `<filename>.part`.
    3. Valida bytes baixados contra Content-Length.
    4. Path.rename() atômico para o nome final.

    Não re-baixa se o arquivo final já existir.

    Raises:
        OSError: se espaço em disco for insuficiente.
        RuntimeError: se bytes baixados divergirem do Content-Length.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    filename = url.rsplit("/", 1)[-1]
    target = dest / filename
    part = target.with_suffix(".part")

    if target.exists():
        return target

    # ~270MB .zst + até 3GB banco → mínimo 3.5 GB de margem
    _MIN_FREE_BYTES = 3_500 * 1024 * 1024
    free_bytes = shutil.disk_usage(dest).free
    if free_bytes < _MIN_FREE_BYTES:
        free_gb = free_bytes / (1024 ** 3)
        raise OSError(
            f"Espaço em disco insuficiente: {free_gb:.1f} GB livres, "
            f"mínimo necessário: 3.5 GB. Libere espaço e tente novamente."
        )

    with urllib.request.urlopen(url) as response:  # noqa: S310
        content_length = response.headers.get("Content-Length")
        total = int(content_length) if content_length else 0
        downloaded = 0
        with part.open("wb") as f:
            while chunk := response.read(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\rBaixando... {pct:.1f}%", end="", flush=True)
    print()

    if total and downloaded != total:
        part.unlink(missing_ok=True)
        raise RuntimeError(
            f"Download incompleto: esperado {total} bytes, recebidos {downloaded}. "
            f"Arquivo temporário removido. Tente novamente."
        )

    part.rename(target)
    return target


def iter_puzzles(zst_path: Path) -> Iterator[dict[str, Any]]:
    """Itera sobre puzzles do CSV comprimido em streaming, sem materializar na memória.

    Cada item é um dict com as chaves do CSV (PuzzleId, FEN, Moves, ...).
    O campo Themes é normalizado para lowercase.
    """
    dctx = zstandard.ZstdDecompressor()
    with zst_path.open("rb") as fh:
        with dctx.stream_reader(fh) as reader:
            text_stream = io.TextIOWrapper(reader, encoding="utf-8")
            csv_reader = csv.DictReader(text_stream)
            for row in csv_reader:
                row["Themes"] = row["Themes"].lower().strip()
                yield row


def index_puzzles(
    zst_path: Path,
    db_path: str,
    batch_size: int = 5000,
    force: bool = False,
) -> IndexStats:
    """Indexa puzzles do arquivo .csv.zst no banco SQLite local.

    Idempotência via SHA-256:
    - SHA-256 do arquivo bate com puzzle_index_meta → abort (IndexStats.skipped=True).
    - SHA-256 diverge (dataset atualizado) ou force=True → re-indexa do zero.
    - Tabelas vazias → indexa diretamente.

    Usa lotes transacionais de `batch_size` puzzles para limitar uso de memória
    e garantir progresso parcial em caso de falha.
    """
    init_db(db_path)

    file_sha256 = compute_file_sha256(zst_path)

    if not force:
        registered_sha256 = get_puzzle_index_sha256(db_path)
        if registered_sha256 == file_sha256:
            return IndexStats(total=0, inserted=0, skipped=True)

    _clear_puzzle_tables(db_path)

    conn = get_connection(db_path)
    conn.isolation_level = None

    total = 0
    inserted = 0
    in_transaction = False
    uncommitted_count = 0

    try:
        for row in iter_puzzles(zst_path):
            if not in_transaction:
                conn.execute("BEGIN IMMEDIATE;")
                in_transaction = True

            themes_str = row["Themes"]
            themes = [t for t in themes_str.split() if t]

            daily_date_raw = row.get("DailyDate", "").strip()
            daily_date: int | None = int(daily_date_raw) if daily_date_raw else None

            opening_tags_raw = row.get("OpeningTags", "").strip()
            opening_tags: str | None = opening_tags_raw if opening_tags_raw else None

            game_url_raw = row.get("GameUrl", "").strip()
            game_url: str | None = game_url_raw if game_url_raw else None

            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO puzzles (
                    puzzle_id, fen, moves, rating, rating_deviation,
                    popularity, nb_plays, themes, game_url, opening_tags, daily_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(puzzle_id) DO NOTHING;
                """,
                (
                    row["PuzzleId"],
                    row["FEN"],
                    row["Moves"],
                    int(row["Rating"]),
                    int(row["RatingDeviation"]),
                    int(row["Popularity"]),
                    int(row["NbPlays"]),
                    themes_str,
                    game_url,
                    opening_tags,
                    daily_date,
                ),
            )

            if themes:
                cur.executemany(
                    """
                    INSERT INTO puzzle_themes (puzzle_id, theme)
                    VALUES (?, ?)
                    ON CONFLICT(puzzle_id, theme) DO NOTHING;
                    """,
                    [(row["PuzzleId"], theme) for theme in themes],
                )

            total += 1
            inserted += 1
            uncommitted_count += 1

            if uncommitted_count >= batch_size:
                conn.execute("COMMIT;")
                in_transaction = False
                uncommitted_count = 0

        if in_transaction:
            conn.execute("COMMIT;")
            in_transaction = False

    except Exception:
        if in_transaction:
            conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()

    save_puzzle_index_meta(
        db_path=db_path,
        source_url=LICHESS_PUZZLE_URL,
        file_sha256=file_sha256,
        puzzle_count=inserted,
    )

    return IndexStats(total=total, inserted=inserted, skipped=False)


def get_puzzles_by_theme(
    db_path: str,
    theme: str,
    limit: int = 50,
    min_rating: int = 0,
    max_rating: int = 3000,
) -> list[dict[str, Any]]:
    """Retorna puzzles filtrados por tema, ordenados por popularidade descendente.

    Usa idx_puzzle_themes_theme para acesso eficiente.
    Confirmar plano de execução via EXPLAIN QUERY PLAN na Etapa 7.
    """
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.puzzle_id, p.fen, p.moves, p.rating, p.themes, p.opening_tags
            FROM puzzles p
            JOIN puzzle_themes pt ON p.puzzle_id = pt.puzzle_id
            WHERE pt.theme = ?
              AND p.rating >= ?
              AND p.rating <= ?
            ORDER BY p.popularity DESC
            LIMIT ?;
            """,
            (theme.lower(), min_rating, max_rating, limit),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "puzzle_id": r[0],
            "fen": r[1],
            "moves": r[2],
            "rating": r[3],
            "themes": r[4],
            "opening_tags": r[5],
        }
        for r in rows
    ]


def _clear_puzzle_tables(db_path: str) -> None:
    """Remove todos os dados de puzzles para re-indexação limpa."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM puzzle_themes;")
            conn.execute("DELETE FROM puzzles;")
            conn.execute("DELETE FROM puzzle_index_meta;")
    finally:
        conn.close()


def detect_weakest_phase(
    phase_stats: list[AggregatedStats],
) -> tuple[GamePhase, AggregatedStats] | None:
    """Determina a fase mais fraca do jogador a partir das estatísticas agregadas.

    Critério:
    1. Maior perda média de probabilidade de vitória (avg_delta_win_prob).
    2. Desempate por maior taxa de blunder (blunders / total_moves).
    Retorna None se a lista de estatísticas estiver vazia.
    """
    if not phase_stats:
        return None

    def phase_weakness_key(s: AggregatedStats) -> tuple[float, float]:
        blunder_rate = (
            s.category_counts.blunder / s.total_moves if s.total_moves > 0 else 0.0
        )
        return (s.avg_delta_win_prob, blunder_rate)

    weakest = max(phase_stats, key=phase_weakness_key)
    return GamePhase(weakest.group_key), weakest


def get_player_elo(db_path: str, player_name: str) -> tuple[int, int] | None:
    """Calcula o Elo médio e o tamanho da amostra (partidas) do jogador em games."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                AVG(
                    CASE
                        WHEN white = :player AND white_elo > 0
                        THEN white_elo
                        WHEN black = :player AND black_elo > 0
                        THEN black_elo
                        ELSE NULL
                    END
                ),
                COUNT(
                    CASE
                        WHEN white = :player AND white_elo > 0 THEN 1
                        WHEN black = :player AND black_elo > 0 THEN 1
                        ELSE NULL
                    END
                )
            FROM games
            WHERE (white = :player AND white_elo > 0)
               OR (black = :player AND black_elo > 0);
            """,
            {"player": player_name},
        )
        row = cur.fetchone()
        if row and row[0] is not None and row[1] > 0:
            return round(row[0]), int(row[1])
        return None
    finally:
        conn.close()


def generate_training_session(
    db_path: str,
    player_name: str,
    count: int = 10,
    rating_window: int = 200,
    forced_phase: GamePhase | None = None,
) -> TrainingSession | None:
    """Gera uma sessão de treino personalizada baseada nas fraquezas do jogador.

    Passos:
    1. Obtém estatísticas de fase via stats_by_game_phase.
    2. Identifica a pior fase (ou usa forced_phase se especificado).
    3. Consulta Elo médio do jogador e tamanho da amostra.
    4. Busca puzzles com o tema da fase no rating calibrado [elo - w, elo + w].
       Se encontrar menos que `count`, relaxa a janela de rating automaticamente.
    5. Converte posições de forma defensiva: valida FEN e lances com try/except,
       aplica Moves[0] ao FEN para obter training_fen e gera soluções em SAN.
    """
    from chess_analyzer.stats import stats_by_game_phase

    phase_stats = stats_by_game_phase(db_path, player_name)
    if not phase_stats and forced_phase is None:
        return None

    if forced_phase is not None:
        target_phase = forced_phase
        matching = [s for s in phase_stats if s.group_key == forced_phase.value]
        stat_item = matching[0] if matching else None
        avg_loss = stat_item.avg_delta_win_prob if stat_item else 0.0
        blunders = stat_item.category_counts.blunder if stat_item else 0
        total_moves = stat_item.total_moves if stat_item else 0
    else:
        detected = detect_weakest_phase(phase_stats)
        if detected is None:
            return None
        target_phase, stat_item = detected
        avg_loss = stat_item.avg_delta_win_prob
        blunders = stat_item.category_counts.blunder
        total_moves = stat_item.total_moves

    theme_name = target_phase.value.lower()  # 'opening', 'middlegame', 'endgame'
    elo_info = get_player_elo(db_path, player_name)
    player_elo = elo_info[0] if elo_info else None
    elo_sample_size = elo_info[1] if elo_info else 0

    if player_elo is not None:
        min_r = max(0, player_elo - rating_window)
        max_r = player_elo + rating_window
    else:
        min_r = 0
        max_r = 3000

    raw_puzzles = get_puzzles_by_theme(
        db_path=db_path,
        theme=theme_name,
        limit=count,
        min_rating=min_r,
        max_rating=max_r,
    )

    # Fallback se a janela restrita não retornar quantidade suficiente
    if len(raw_puzzles) < count and player_elo is not None:
        raw_puzzles = get_puzzles_by_theme(
            db_path=db_path,
            theme=theme_name,
            limit=count,
            min_rating=max(0, player_elo - rating_window * 2),
            max_rating=player_elo + rating_window * 2,
        )

    puzzle_items: list[PuzzleItem] = []
    for p in raw_puzzles:
        fen_before = p["fen"]
        moves_list = p["moves"].split()
        if len(moves_list) < 2:
            continue

        try:
            board = chess.Board(fen_before)
            opp_move_uci = moves_list[0]
            opp_move = chess.Move.from_uci(opp_move_uci)
            opp_san = board.san(opp_move)
            board.push(opp_move)
            training_fen = board.fen()
        except ValueError:
            continue

        solution_uci = moves_list[1:]
        solution_san: list[str] = []
        sol_board = board.copy()
        for m_uci in solution_uci:
            try:
                m = chess.Move.from_uci(m_uci)
                solution_san.append(sol_board.san(m))
                sol_board.push(m)
            except ValueError:
                solution_san.append(m_uci)

        puzzle_items.append(
            PuzzleItem(
                puzzle_id=p["puzzle_id"],
                fen_before=fen_before,
                opponent_move_uci=opp_move_uci,
                opponent_move_san=opp_san,
                training_fen=training_fen,
                solution_uci=solution_uci,
                solution_san=solution_san,
                rating=p["rating"],
                popularity=p.get("popularity", 0),
                themes=p["themes"].split() if isinstance(p["themes"], str) else p["themes"],
                game_url=p.get("game_url"),
            )
        )

    return TrainingSession(
        player_name=player_name,
        weakest_phase=target_phase,
        target_theme=theme_name,
        avg_delta_win_prob=avg_loss,
        blunder_count=blunders,
        total_moves_in_phase=total_moves,
        player_elo=player_elo,
        elo_sample_size=elo_sample_size,
        requested_count=count,
        puzzles=puzzle_items,
    )

