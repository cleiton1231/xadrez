"""Testes do módulo puzzles.py — Fase 2: indexação do dataset Lichess.

Todos os testes são escritos antes da implementação (TDD — RED-GREEN-REFACTOR).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chess_analyzer.db import get_connection, init_db
from chess_analyzer.puzzles import (
    IndexStats,
    get_puzzles_by_theme,
    index_puzzles,
    iter_puzzles,
)

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample_puzzles.csv"


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_zst_fixture(tmp_path: Path, csv_path: Path) -> Path:
    """Comprime o CSV de fixture em .zst e retorna o Path."""
    import zstandard

    zst_path = tmp_path / "sample_puzzles.csv.zst"
    cctx = zstandard.ZstdCompressor()
    with csv_path.open("rb") as fin, zst_path.open("wb") as fout:
        cctx.copy_stream(fin, fout)
    return zst_path


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Caminho temporário de banco de dados para cada teste."""
    return str(tmp_path / "test_puzzles.db")


@pytest.fixture
def zst_path(tmp_path: Path) -> Path:
    """Fixture CSV comprimida em .zst."""
    return make_zst_fixture(tmp_path, FIXTURE_CSV)


# ── Test 1: parsing do CSV de fixture ────────────────────────────────────────


def test_iter_puzzles_parses_sample_fixture(zst_path: Path) -> None:
    """iter_puzzles deve retornar dicts com os campos corretos do CSV."""
    puzzles = list(iter_puzzles(zst_path))

    assert len(puzzles) == 20
    first = puzzles[0]
    assert first["PuzzleId"] == "00sHx"
    assert first["FEN"] == "q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17"
    assert first["Moves"] == "e8d7 a2e6 d7d8 f7f8"
    assert first["Rating"] == "1760"
    # Themes devem ser normalizados para lowercase
    assert first["Themes"] == "mate matein2 middlegame short"


def test_iter_puzzles_themes_are_lowercase(zst_path: Path) -> None:
    """Todos os temas devem estar em lowercase após normalização."""
    for puzzle in iter_puzzles(zst_path):
        assert puzzle["Themes"] == puzzle["Themes"].lower(), (
            f"Tema não-lowercase em puzzle {puzzle['PuzzleId']}: {puzzle['Themes']!r}"
        )


# ── Test 2: indexação idempotente ─────────────────────────────────────────────


def test_index_puzzles_idempotent(zst_path: Path, db_path: str) -> None:
    """Rodar index_puzzles duas vezes com o mesmo arquivo não duplica dados."""
    stats1 = index_puzzles(zst_path, db_path)
    assert not stats1.skipped
    assert stats1.inserted == 20

    stats2 = index_puzzles(zst_path, db_path)
    # SHA-256 idêntico: deve abortar sem re-indexar
    assert stats2.skipped is True
    assert stats2.inserted == 0

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM puzzles;")
        assert cur.fetchone()[0] == 20

        cur.execute("SELECT COUNT(*) FROM puzzle_themes;")
        theme_count = cur.fetchone()[0]
        assert theme_count > 0
    finally:
        conn.close()


# ── Test 3: re-indexação com SHA-256 diferente ───────────────────────────────


def test_index_puzzles_updates_on_new_hash(tmp_path: Path, db_path: str) -> None:
    """Arquivo com SHA-256 diferente causa re-indexação completa."""
    import zstandard

    zst_path_v1 = make_zst_fixture(tmp_path, FIXTURE_CSV)
    stats1 = index_puzzles(zst_path_v1, db_path)
    assert stats1.inserted == 20

    # Cria arquivo "diferente" com apenas 5 puzzles
    alt_csv = tmp_path / "alt_puzzles.csv"
    with FIXTURE_CSV.open() as fin:
        lines = fin.readlines()
    with alt_csv.open("w") as fout:
        fout.writelines(lines[:6])  # header + 5 puzzles

    zst_path_v2 = tmp_path / "alt_puzzles.csv.zst"
    cctx = zstandard.ZstdCompressor()
    with alt_csv.open("rb") as fin, zst_path_v2.open("wb") as fout:
        cctx.copy_stream(fin, fout)

    stats2 = index_puzzles(zst_path_v2, db_path)
    assert not stats2.skipped
    assert stats2.inserted == 5

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM puzzles;")
        assert cur.fetchone()[0] == 5  # re-indexou do zero
    finally:
        conn.close()


# ── Test 4: normalização de themes lowercase ──────────────────────────────────


def test_puzzle_themes_normalized_lowercase(zst_path: Path, db_path: str) -> None:
    """Campo themes na tabela puzzles deve estar em lowercase."""
    index_puzzles(zst_path, db_path)

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT puzzle_id, themes FROM puzzles;")
        for puzzle_id, themes in cur.fetchall():
            assert themes == themes.lower(), (
                f"Puzzle {puzzle_id}: themes não-lowercase: {themes!r}"
            )
    finally:
        conn.close()


# ── Test 5: consulta por tema ─────────────────────────────────────────────────


def test_query_by_theme_returns_correct_puzzles(zst_path: Path, db_path: str) -> None:
    """get_puzzles_by_theme deve retornar apenas puzzles com o tema solicitado."""
    index_puzzles(zst_path, db_path)

    middlegame_puzzles = get_puzzles_by_theme(db_path, "middlegame")
    assert len(middlegame_puzzles) > 0

    for p in middlegame_puzzles:
        themes = p["themes"].split()
        assert "middlegame" in themes, (
            f"Puzzle {p['puzzle_id']} retornado mas não tem tema 'middlegame': {p['themes']}"
        )

    # Tema inexistente deve retornar lista vazia
    none_puzzles = get_puzzles_by_theme(db_path, "tema_que_nao_existe_xyz")
    assert none_puzzles == []


# ── Test 6: migration v1 → v2 ─────────────────────────────────────────────────


def test_migration_v1_to_v2(tmp_path: Path) -> None:
    """Banco com user_version=1 deve receber migration v2 sem perder dados de games/moves."""
    db_path = str(tmp_path / "v1_bank.db")

    # Cria banco v1 manualmente (simula banco da Fase 1 existente)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE games (
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
        INSERT INTO games (game_hash, white, black, result)
        VALUES ('abc123', 'Alice', 'Bob', '1-0');
        PRAGMA user_version = 1;
    """)
    conn.close()

    # init_db deve detectar v1 e aplicar migration para v2
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # user_version deve ser 2
        cur.execute("PRAGMA user_version;")
        assert cur.fetchone()[0] == 2, "user_version deve ser 2 após migration"

        # Dados de games existentes devem estar intactos
        cur.execute("SELECT white, black FROM games WHERE game_hash = 'abc123';")
        row = cur.fetchone()
        assert row == ("Alice", "Bob"), f"Dados de games corrompidos após migration: {row}"

        # Tabelas v2 devem existir
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='puzzles';"
        )
        assert cur.fetchone() is not None, "Tabela 'puzzles' não criada pela migration"

        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='puzzle_themes';"
        )
        assert cur.fetchone() is not None, "Tabela 'puzzle_themes' não criada pela migration"

        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='puzzle_index_meta';"
        )
        assert cur.fetchone() is not None, "Tabela 'puzzle_index_meta' não criada pela migration"

        # Índice de temas deve existir
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_puzzle_themes_theme';"
        )
        assert cur.fetchone() is not None, (
            "Índice 'idx_puzzle_themes_theme' não criado pela migration"
        )
    finally:
        conn.close()
