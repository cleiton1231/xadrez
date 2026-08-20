"""Wrapper para o Stockfish (UCI) responsável por avaliar as posições do tabuleiro."""

from typing import Self

import chess
import chess.engine

from chess_analyzer.classify import PositionEvaluation


class StockfishEngine:
    """Gerencia o processo do Stockfish e avalia posições de xadrez via UCI."""

    def __init__(self, path: str = ".venv/bin/stockfish", depth: int = 12) -> None:
        """Inicializa a configuração do engine.

        Args:
            path: Caminho para o binário do Stockfish.
            depth: Profundidade de busca. Trade-off documentado entre velocidade vs tática profunda.
        """
        self.path = path
        self.depth = depth
        self._engine: chess.engine.SimpleEngine | None = None

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

        # Normalização para a perspectiva das brancas (com timeout seguro de 2.0s)
        info = self._engine.analyse(board, chess.engine.Limit(depth=self.depth, time=2.0))
        pov_score = info["score"].white()

        # Tratamento de segurança para Mate, prevenindo TypeError com valores .score() nulos
        if pov_score.is_mate():
            mate_in = pov_score.mate()
            return PositionEvaluation(white_cp=None, mate_for_white=mate_in)

        cp = pov_score.score()
        return PositionEvaluation(white_cp=cp, mate_for_white=None)
