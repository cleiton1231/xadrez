"""Wrapper para o Stockfish (UCI) responsável por avaliar as posições do tabuleiro."""

import hashlib
import logging
import os
from typing import Self

import chess
import chess.engine

from chess_analyzer.classify import PositionEvaluation

# Configura o logger do módulo
logger = logging.getLogger(__name__)


class StockfishEngine:
    """Gerencia o processo do Stockfish e avalia posições de xadrez via UCI."""

    def __init__(
        self,
        path: str = ".venv/bin/stockfish",
        depth: int = 12,
        move_time_limit: float = 2.0,
    ) -> None:
        """Inicializa a configuração do engine.

        Args:
            path: Caminho para o binário do Stockfish.
            depth: Profundidade de busca alvo.
                   Nota de Design (Fase 1): depth=12 foi escolhido pois em hardware comum
                   avalia posições complexas de meio-jogo muito rapidamente (medido empíricamente
                   entre ~6ms e ~50ms por lance).
            move_time_limit: Tempo máximo (s) por lance. Atua apenas como safety valve
                   para evitar que a engine trave o processo indefinidamente em posições
                   complexas. Não é esperado que depth=12 estoure 2.0s em uso normal.
        """
        self.path = path
        self.depth = depth
        self.move_time_limit = move_time_limit
        self._engine: chess.engine.SimpleEngine | None = None

    @property
    def cache_key(self) -> str:
        """Chave estável para o cache SQLite de avaliações deste binário/profundidade."""
        resolved = os.path.realpath(self.path) if os.path.exists(self.path) else self.path
        raw = f"{resolved}:{self.depth}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def __enter__(self) -> Self:
        """Inicia o processo do Stockfish de forma segura."""
        self._engine = chess.engine.SimpleEngine.popen_uci(self.path)
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Garante o encerramento do processo do Stockfish sem deixar processos órfãos."""
        if self._engine is not None:
            self._engine.quit()
            self._engine = None

    def evaluate(self, board: chess.Board) -> PositionEvaluation:
        """Avalia uma posição do tabuleiro.

        Realiza bypass estático em posições de game_over para não travar o UCI com
        posições sem lances legais. Avalia pelo UCI normalizando a saída
        para a ótica unificada das Brancas, tratando mate e centipawns isoladamente.
        """
        # Bypass de avaliação para posições terminais (Evita comportamentos UCI indefinidos)
        if board.is_game_over():
            outcome = board.outcome()
            if outcome and outcome.termination == chess.Termination.CHECKMATE:
                if outcome.winner == chess.WHITE:
                    return PositionEvaluation(white_cp=None, mate_for_white=1)
                else:
                    return PositionEvaluation(white_cp=None, mate_for_white=-1)
            # Empate (Afogamento, insuficiência de material, 50 lances)
            return PositionEvaluation(white_cp=0, mate_for_white=None)

        if self._engine is None:
            raise RuntimeError("Engine não foi inicializado. Use 'with StockfishEngine(...)'.")

        # Normalização para a perspectiva das brancas (com timeout de segurança configurável)
        limit = chess.engine.Limit(depth=self.depth, time=self.move_time_limit)
        info = self._engine.analyse(board, limit)

        # Checagem de precisão de avaliação (evita corrupção silenciosa da métrica Win Probability)
        # O python-chess retorna None no get() se a chave não estiver no dict, retornamos 0.
        reached_depth = info.get("depth", 0)
        if reached_depth < self.depth:
            logger.warning(
                f"Avaliação truncada por tempo limite ({self.move_time_limit}s). "
                f"Profundidade atingida: {reached_depth} (alvo: {self.depth}). "
                "A classificação deste lance pode estar imprecisa."
            )

        pov_score = info["score"].white()

        # Tratamento de segurança para Mate, prevenindo TypeError com valores .score() nulos
        if pov_score.is_mate():
            mate_in = pov_score.mate()
            return PositionEvaluation(white_cp=None, mate_for_white=mate_in)

        cp = pov_score.score()
        return PositionEvaluation(white_cp=cp, mate_for_white=None)
