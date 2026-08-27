import logging
import subprocess
import time

import chess
import pytest

from chess_analyzer.engine import StockfishEngine
from tests.conftest import STOCKFISH_PATH, requires_stockfish


def count_stockfish_processes() -> int:
    """Retorna o número de processos do stockfish rodando atualmente."""
    res = subprocess.run(["pgrep", "-c", "-x", "stockfish"], capture_output=True, text=True)
    if res.returncode == 0:
        return int(res.stdout.strip())
    return 0


@requires_stockfish
def test_static_evaluation_checkmate_white_wins() -> None:
    """Se o jogo acabou com mate dado pelas Brancas, retorna estaticamente +M sem invocar UCI."""
    board = chess.Board("r1bqkbnr/pppp1Qpp/2n5/4p3/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 0 4")
    assert board.is_checkmate()

    with StockfishEngine(STOCKFISH_PATH) as engine:
        eval_pos = engine.evaluate(board)

    assert eval_pos.white_cp is None
    assert eval_pos.mate_for_white == 1


@requires_stockfish
def test_static_evaluation_checkmate_black_wins() -> None:
    """Se o jogo acabou com mate dado pelas Pretas, retorna estaticamente -M."""
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    assert board.is_checkmate()

    with StockfishEngine(STOCKFISH_PATH) as engine:
        eval_pos = engine.evaluate(board)

    assert eval_pos.white_cp is None
    assert eval_pos.mate_for_white == -1


@requires_stockfish
def test_static_evaluation_draw() -> None:
    """Se a posição é de empate (afogamento), retorna estaticamente 0 cp."""
    board = chess.Board("7k/5K2/6Q1/8/8/8/8/8 b - - 0 1")
    assert board.is_stalemate()

    with StockfishEngine(STOCKFISH_PATH) as engine:
        eval_pos = engine.evaluate(board)

    assert eval_pos.white_cp == 0
    assert eval_pos.mate_for_white is None


@requires_stockfish
def test_real_uci_evaluation() -> None:
    """Posição normal deve usar o Stockfish real com depth=12 para achar centipawns."""
    board = chess.Board()  # Posição inicial
    with StockfishEngine(STOCKFISH_PATH, depth=12) as engine:
        eval_pos = engine.evaluate(board)

    assert eval_pos.mate_for_white is None
    assert eval_pos.white_cp is not None
    assert -100 <= eval_pos.white_cp <= 100


@requires_stockfish
def test_real_uci_evaluation_mate_in_x() -> None:
    """Posição de mate forçado avalia mate_for_white e trata None TypeError."""
    # Ameaça de Scholar's Mate (Brancas dão mate no próximo lance: Qf7#)
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 0 4")

    with StockfishEngine(STOCKFISH_PATH, depth=12) as engine:
        eval_pos = engine.evaluate(board)

    assert eval_pos.white_cp is None
    assert eval_pos.mate_for_white == 1


@requires_stockfish
def test_real_uci_evaluation_timeout() -> None:
    """Valida se o limite de timeout de segurança (2.0s) de fato interrompe buscas demoradas."""
    # Posição complexa inicial, pedindo depth muito alta para forçar o acionamento do timeout
    board = chess.Board()
    start_time = time.time()

    with StockfishEngine(STOCKFISH_PATH, depth=99, move_time_limit=2.0) as engine:
        engine.evaluate(board)

    duration = time.time() - start_time

    # O tempo deve ser limitado em ~2.0s. Damos uma tolerância maior para overhead (ex: até 5.0s)
    # para cobrir I/O UCI e carga de CPU (flakes em CI). Se desativado, travará muito mais tempo.
    assert 2.0 <= duration <= 5.0


@requires_stockfish
def test_logging_truncation_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Deve emitir warning se a avaliação for truncada antes do alvo devido ao time_limit."""
    board = chess.Board()
    # Força truncamento com depth extremo e move_time_limit impossível
    with StockfishEngine(STOCKFISH_PATH, depth=99, move_time_limit=0.001) as engine:
        with caplog.at_level(logging.WARNING):
            engine.evaluate(board)

    # Verifica se warning foi emitido
    warning_found = False
    for record in caplog.records:
        if "Avaliação truncada por tempo limite" in record.message:
            warning_found = True
            assert "Profundidade atingida:" in record.message
            assert "(alvo: 99)" in record.message
    assert warning_found, "O warning de truncamento não foi emitido como esperado."


@requires_stockfish
def test_logging_no_truncation_warning(caplog: pytest.LogCaptureFixture) -> None:
    """NÃO deve emitir warning se a profundidade foi totalmente atingida (operação normal)."""
    board = chess.Board()
    # Avaliação trivial (depth 1) com tempo folgado
    with StockfishEngine(STOCKFISH_PATH, depth=1, move_time_limit=2.0) as engine:
        with caplog.at_level(logging.WARNING):
            engine.evaluate(board)

    # Verifica se o warning não foi emitido
    for record in caplog.records:
        assert "Avaliação truncada por tempo limite" not in record.message


def test_engine_cache_key_is_stable() -> None:
    engine = StockfishEngine(path="/tmp/stockfish", depth=12)
    assert engine.cache_key == StockfishEngine(path="/tmp/stockfish", depth=12).cache_key
    assert engine.cache_key != StockfishEngine(path="/tmp/stockfish", depth=14).cache_key


@requires_stockfish
def test_process_lifecycle_no_orphans() -> None:
    """O gerenciador de contexto DEVE encerrar o processo (quit), mesmo ocorrendo exceção."""
    initial_count = count_stockfish_processes()

    # Caso 1: Sucesso
    with StockfishEngine(STOCKFISH_PATH):
        # Afirma que o processo de fato foi criado dentro do contexto
        assert count_stockfish_processes() == initial_count + 1

    assert count_stockfish_processes() == initial_count

    # Caso 2: Exceção no meio do processamento
    try:
        with StockfishEngine(STOCKFISH_PATH):
            # Afirma que o processo de fato foi criado dentro do contexto antes de quebrar
            assert count_stockfish_processes() == initial_count + 1
            raise RuntimeError("Simulação de quebra")
    except RuntimeError:
        pass

    assert count_stockfish_processes() == initial_count
