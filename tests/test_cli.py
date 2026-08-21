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
