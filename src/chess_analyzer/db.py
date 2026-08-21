"""Módulo de persistência local em SQLite para partidas, lances e avaliações."""

import hashlib
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from chess_analyzer.pgn_import import ParsedGame


@dataclass(frozen=True)
class ImportStats:
    """Estatísticas resultantes do processo de importação de partidas."""

    total: int
    inserted: int
    skipped: int


def get_connection(db_path: str) -> sqlite3.Connection:
    """Retorna uma conexão SQLite com PRAGMAs essenciais ativados."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def normalize_fen(fen: str) -> str:
    """Normaliza uma string FEN mantendo apenas os 4 primeiros campos essenciais."""
    return " ".join(fen.strip().split()[:4])


def calculate_game_hash(game: ParsedGame) -> str:
    """Calcula o hash SHA-256 canônico de uma partida com delimitador nulo."""
    moves_san = " ".join(m.san for m in game.moves)
    raw_key = f"{game.white}\0{game.black}\0{game.date or ''}\0{game.result}\0{moves_san}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def init_db(db_path: str) -> None:
    """Inicializa o schema do banco de dados SQLite caso ainda não exista."""
    dir_name = os.path.dirname(db_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA user_version;")
        version = cur.fetchone()[0]
        if version == 0:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_hash TEXT NOT NULL UNIQUE,
                white TEXT NOT NULL,
                black TEXT NOT NULL,
                result TEXT NOT NULL,
                date TEXT,
                event TEXT,
                site TEXT,
                white_elo INTEGER,
                black_elo INTEGER,
                time_control TEXT,
                eco TEXT,
                variant TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS moves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                ply INTEGER NOT NULL,
                san TEXT NOT NULL,
                fen_after TEXT NOT NULL,
                category TEXT,
                eval_cp INTEGER,
                eval_mate INTEGER,
                delta_win_prob REAL,
                win_prob_before REAL,
                win_prob_after REAL,
                UNIQUE(game_id, ply)
            );

            CREATE TABLE IF NOT EXISTS evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fen TEXT NOT NULL,
                depth INTEGER NOT NULL,
                eval_cp INTEGER,
                eval_mate INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(fen, depth)
            );

            CREATE INDEX IF NOT EXISTS idx_games_white ON games(white);
            CREATE INDEX IF NOT EXISTS idx_games_black ON games(black);
            CREATE INDEX IF NOT EXISTS idx_games_eco ON games(eco);
            CREATE INDEX IF NOT EXISTS idx_games_date ON games(date);
            CREATE INDEX IF NOT EXISTS idx_moves_game_id ON moves(game_id);
            CREATE INDEX IF NOT EXISTS idx_moves_fen ON moves(fen_after);
            CREATE INDEX IF NOT EXISTS idx_moves_category ON moves(category);
            CREATE INDEX IF NOT EXISTS idx_evaluations_fen ON evaluations(fen);

            PRAGMA user_version = 1;
            """)
    finally:
        conn.close()


def save_games(
    games_iter: Iterable[ParsedGame],
    db_path: str,
    batch_size: int = 100,
) -> ImportStats:
    """Persiste um fluxo de partidas e lances em lotes transacionais idempotentes."""
    init_db(db_path)
    conn = get_connection(db_path)
    conn.isolation_level = None

    total = 0
    inserted = 0
    skipped = 0
    in_transaction = False
    uncommitted_count = 0

    try:
        for game in games_iter:
            if not in_transaction:
                conn.execute("BEGIN IMMEDIATE;")
                in_transaction = True

            game_hash = calculate_game_hash(game)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO games (
                    game_hash, white, black, result, date, event, site,
                    white_elo, black_elo, time_control, eco, variant
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_hash) DO NOTHING
                RETURNING id;
                """,
                (
                    game_hash,
                    game.white,
                    game.black,
                    game.result,
                    game.date,
                    game.event,
                    game.site,
                    game.white_elo,
                    game.black_elo,
                    game.time_control,
                    game.eco,
                    game.variant,
                ),
            )
            row = cur.fetchone()
            if row is not None:
                game_id = row[0]
                if game.moves:
                    moves_data = [
                        (game_id, m.ply, m.san, normalize_fen(m.fen_after)) for m in game.moves
                    ]
                    cur.executemany(
                        """
                        INSERT INTO moves (game_id, ply, san, fen_after)
                        VALUES (?, ?, ?, ?);
                        """,
                        moves_data,
                    )
                inserted += 1
            else:
                skipped += 1

            total += 1
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

    return ImportStats(total=total, inserted=inserted, skipped=skipped)


def save_evaluation(
    db_path: str,
    fen: str,
    depth: int,
    eval_cp: int | None,
    eval_mate: int | None,
) -> None:
    """Salva ou atualiza a avaliação de uma posição FEN normalizada para uma dada profundidade."""
    init_db(db_path)
    norm_fen = normalize_fen(fen)
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO evaluations (fen, depth, eval_cp, eval_mate)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(fen, depth) DO UPDATE SET
                    eval_cp = excluded.eval_cp,
                    eval_mate = excluded.eval_mate;
                """,
                (norm_fen, depth, eval_cp, eval_mate),
            )
    finally:
        conn.close()


def get_evaluation(
    db_path: str,
    fen: str,
    min_depth: int = 1,
) -> tuple[int | None, int | None, int] | None:
    """Recupera a avaliação mais profunda para um FEN normalizado com profundidade >= min_depth."""
    init_db(db_path)
    norm_fen = normalize_fen(fen)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT eval_cp, eval_mate, depth
            FROM evaluations
            WHERE fen = ? AND depth >= ?
            ORDER BY depth DESC
            LIMIT 1;
            """,
            (norm_fen, min_depth),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return (row[0], row[1], row[2])
    finally:
        conn.close()
