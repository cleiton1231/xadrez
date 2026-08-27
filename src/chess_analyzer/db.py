"""Módulo de persistência local em SQLite para partidas, lances e avaliações."""

import hashlib
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from chess_analyzer.pgn_import import STANDARD_START_FEN, ParsedGame

DEFAULT_ENGINE_KEY = "legacy"
BUSY_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class ImportStats:
    """Estatísticas resultantes do processo de importação de partidas."""

    total: int
    inserted: int
    skipped: int


def get_connection(db_path: str) -> sqlite3.Connection:
    """Retorna uma conexão SQLite com PRAGMAs essenciais ativados."""
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS};")
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


def build_engine_cache_key(engine_path: str, depth: int) -> str:
    """Gera chave estável para o cache de avaliações por binário e profundidade."""
    resolved = os.path.realpath(engine_path) if os.path.exists(engine_path) else engine_path
    raw = f"{resolved}:{depth}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


_PUZZLE_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS puzzles (
    puzzle_id        TEXT PRIMARY KEY,
    fen              TEXT NOT NULL,
    moves            TEXT NOT NULL,
    rating           INTEGER NOT NULL,
    rating_deviation INTEGER NOT NULL,
    popularity       INTEGER NOT NULL,
    nb_plays         INTEGER NOT NULL,
    themes           TEXT NOT NULL,
    game_url         TEXT,
    opening_tags     TEXT,
    daily_date       INTEGER
);

CREATE TABLE IF NOT EXISTS puzzle_themes (
    puzzle_id TEXT NOT NULL REFERENCES puzzles(puzzle_id) ON DELETE CASCADE,
    theme     TEXT NOT NULL,
    PRIMARY KEY (puzzle_id, theme)
);

CREATE INDEX IF NOT EXISTS idx_puzzle_themes_theme ON puzzle_themes(theme);

CREATE TABLE IF NOT EXISTS puzzle_index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _apply_puzzle_schema(conn: sqlite3.Connection) -> None:
    """Aplica o schema v2 de puzzles numa conexão já aberta."""
    conn.executescript(_PUZZLE_SCHEMA_V2)


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Aplica migration v3: starting_fen em games e engine_key em evaluations."""
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(games);")
    game_columns = {row[1] for row in cur.fetchall()}
    if "starting_fen" not in game_columns:
        conn.execute("ALTER TABLE games ADD COLUMN starting_fen TEXT;")

    conn.execute(
        "UPDATE games SET starting_fen = ? WHERE starting_fen IS NULL;",
        (STANDARD_START_FEN,),
    )

    cur.execute("PRAGMA table_info(evaluations);")
    eval_columns = {row[1] for row in cur.fetchall()}
    if "engine_key" not in eval_columns:
        conn.executescript("""
        CREATE TABLE evaluations_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fen TEXT NOT NULL,
            depth INTEGER NOT NULL,
            engine_key TEXT NOT NULL DEFAULT 'legacy',
            eval_cp INTEGER,
            eval_mate INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(fen, depth, engine_key)
        );

        INSERT INTO evaluations_new (id, fen, depth, engine_key, eval_cp, eval_mate, created_at)
        SELECT id, fen, depth, 'legacy', eval_cp, eval_mate, created_at
        FROM evaluations;

        DROP TABLE evaluations;
        ALTER TABLE evaluations_new RENAME TO evaluations;
        CREATE INDEX IF NOT EXISTS idx_evaluations_fen ON evaluations(fen);
        """)

    conn.execute("PRAGMA user_version = 3;")


def init_db(db_path: str) -> None:
    """Inicializa o schema do banco de dados SQLite caso ainda não exista.

    Versionamento via PRAGMA user_version:
    - 0 → cria schema v1 + v2 puzzles, migra para v3
    - 1 → aplica delta v2 puzzles, migra para v3
    - 2 → aplica migration v3 (starting_fen + engine_key)
    - ≥3 → noop
    """
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
                starting_fen TEXT,
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
                engine_key TEXT NOT NULL DEFAULT 'legacy',
                eval_cp INTEGER,
                eval_mate INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(fen, depth, engine_key)
            );

            CREATE INDEX IF NOT EXISTS idx_games_white ON games(white);
            CREATE INDEX IF NOT EXISTS idx_games_black ON games(black);
            CREATE INDEX IF NOT EXISTS idx_games_eco ON games(eco);
            CREATE INDEX IF NOT EXISTS idx_games_date ON games(date);
            CREATE INDEX IF NOT EXISTS idx_moves_game_id ON moves(game_id);
            CREATE INDEX IF NOT EXISTS idx_moves_fen ON moves(fen_after);
            CREATE INDEX IF NOT EXISTS idx_moves_category ON moves(category);
            CREATE INDEX IF NOT EXISTS idx_evaluations_fen ON evaluations(fen);

            PRAGMA user_version = 3;
            """)
            _apply_puzzle_schema(conn)
            conn.execute(
                "UPDATE games SET starting_fen = ? WHERE starting_fen IS NULL;",
                (STANDARD_START_FEN,),
            )
        elif version == 1:
            _apply_puzzle_schema(conn)
            _migrate_v2_to_v3(conn)
        elif version == 2:
            _migrate_v2_to_v3(conn)
        # version >= 3: noop
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
                    white_elo, black_elo, time_control, eco, variant, starting_fen
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    normalize_fen(game.starting_fen),
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
    engine_key: str = DEFAULT_ENGINE_KEY,
) -> None:
    """Salva ou atualiza a avaliação de uma posição FEN normalizada para uma dada profundidade."""
    init_db(db_path)
    norm_fen = normalize_fen(fen)
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO evaluations (fen, depth, engine_key, eval_cp, eval_mate)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(fen, depth, engine_key) DO UPDATE SET
                    eval_cp = excluded.eval_cp,
                    eval_mate = excluded.eval_mate;
                """,
                (norm_fen, depth, engine_key, eval_cp, eval_mate),
            )
    finally:
        conn.close()


def get_evaluation(
    db_path: str,
    fen: str,
    min_depth: int = 1,
    engine_key: str = DEFAULT_ENGINE_KEY,
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
            WHERE fen = ? AND depth >= ? AND engine_key = ?
            ORDER BY depth DESC
            LIMIT 1;
            """,
            (norm_fen, min_depth, engine_key),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return (row[0], row[1], row[2])
    finally:
        conn.close()


def get_game_starting_fen(db_path: str, game_id: int) -> str:
    """Retorna a FEN inicial persistida da partida ou a posição padrão."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT starting_fen FROM games WHERE id = ?;", (game_id,))
        row = cur.fetchone()
        if row is None or row[0] is None:
            return STANDARD_START_FEN
        return str(row[0])
    finally:
        conn.close()


def get_puzzle_index_sha256(db_path: str) -> str | None:
    """Retorna o SHA-256 registrado da última indexação de puzzles, ou None se nunca indexado."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT value FROM puzzle_index_meta WHERE key = 'file_sha256';"
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def save_puzzle_index_meta(
    db_path: str,
    source_url: str,
    file_sha256: str,
    puzzle_count: int,
) -> None:
    """Persiste metadados da indexação de puzzles (idempotente via UPSERT)."""
    import datetime

    init_db(db_path)
    conn = get_connection(db_path)
    now = datetime.datetime.now(datetime.UTC).isoformat()
    rows = [
        ("source_url", source_url),
        ("file_sha256", file_sha256),
        ("puzzle_count", str(puzzle_count)),
        ("indexed_at", now),
    ]
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO puzzle_index_meta (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """,
                rows,
            )
    finally:
        conn.close()
