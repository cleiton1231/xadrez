"""Classificação de lances de xadrez baseada no modelo de Win Probability do Lichess.

Converte centipawns em probabilidade de vitória e classifica os lances
a partir da perda de chance de vitória (ΔWin%), com normalização unificada
de perspectiva e tolerância de ponto flutuante.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import chess

# Constante de regressão logística utilizada pelo Lichess para conversão de centipawns
LICHESS_WIN_PROB_CONSTANT: float = 0.00368208

# Tolerância para mitigar imprecisões de ponto flutuante em comparações de fronteira
EPSILON: float = 1e-4


class MoveCategory(StrEnum):
    """Categorias de qualidade de um lance de acordo com a perda de Win% (ΔWin%)."""

    BEST = "BEST"
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    INACCURACY = "INACCURACY"
    MISTAKE = "MISTAKE"
    BLUNDER = "BLUNDER"

    @classmethod
    def from_delta(cls, delta_win_prob: float) -> MoveCategory:
        """Determina a categoria do lance a partir da perda de probabilidade de vitória.

        Faixas de corte com tolerância EPSILON:
        - ΔW <= 0.0 + EPSILON  -> BEST
        - ΔW <= 2.0 + EPSILON  -> EXCELLENT
        - ΔW <= 5.0 + EPSILON  -> GOOD
        - ΔW <= 10.0 + EPSILON -> INACCURACY
        - ΔW <= 20.0 + EPSILON -> MISTAKE
        - ΔW > 20.0 + EPSILON  -> BLUNDER
        """
        if delta_win_prob <= 0.0 + EPSILON:
            return cls.BEST
        if delta_win_prob <= 2.0 + EPSILON:
            return cls.EXCELLENT
        if delta_win_prob <= 5.0 + EPSILON:
            return cls.GOOD
        if delta_win_prob <= 10.0 + EPSILON:
            return cls.INACCURACY
        if delta_win_prob <= 20.0 + EPSILON:
            return cls.MISTAKE
        return cls.BLUNDER


@dataclass(frozen=True)
class PositionEvaluation:
    """Avaliação de uma posição do tabuleiro, normalizada na perspectiva das Brancas."""

    white_cp: int | None = None
    mate_for_white: int | None = None


@dataclass(frozen=True)
class MoveClassification:
    """Resultado da classificação de um lance de xadrez."""

    category: MoveCategory
    win_prob_before: float
    win_prob_after: float
    delta_win_prob: float
    player: chess.Color


def to_player_perspective(eval_pos: PositionEvaluation, color: chess.Color) -> PositionEvaluation:
    """Converte a avaliação da ótica absoluta das Brancas para a ótica do jogador ativo.

    - Se o jogador for Brancas, retorna a avaliação inalterada.
    - Se o jogador for Pretas, inverte os sinais de centipawns e mate com checagem segura de None.
    """
    if color == chess.WHITE:
        return eval_pos

    black_cp = -eval_pos.white_cp if eval_pos.white_cp is not None else None
    black_mate = -eval_pos.mate_for_white if eval_pos.mate_for_white is not None else None

    return PositionEvaluation(white_cp=black_cp, mate_for_white=black_mate)


def win_probability(white_cp: int | None, mate_for_white: int | None) -> float:
    """Calcula a probabilidade de vitória (0.0% a 100.0%) a partir da avaliação.

    Fórmula: W(cp) = 100 / (1 + exp(-0.00368208 * cp))

    Limitação assumida da Fase 1: Qualquer mate positivo (+M) resulta em 100.0%,
    e qualquer mate negativo (-M) resulta em 0.0%, não diferenciando distância de mate.
    """
    if mate_for_white is not None:
        if mate_for_white > 0:
            return 100.0
        if mate_for_white < 0:
            return 0.0
        raise ValueError("mate_for_white não pode ser 0.")

    if white_cp is not None:
        # Prevenção contra overflow em centipawns extremos
        exponent = -LICHESS_WIN_PROB_CONSTANT * white_cp
        if exponent > 700:
            return 0.0
        if exponent < -700:
            return 100.0
        return 100.0 / (1.0 + math.exp(exponent))

    raise ValueError("Pelo menos um entre white_cp e mate_for_white deve ser fornecido.")


def classify_move(
    eval_before: PositionEvaluation,
    eval_after: PositionEvaluation,
    player: chess.Color,
) -> MoveClassification:
    """Classifica um lance com base na perda de Win% (ΔWin%) da perspectiva do jogador."""
    norm_before = to_player_perspective(eval_before, player)
    norm_after = to_player_perspective(eval_after, player)

    prob_before = win_probability(norm_before.white_cp, norm_before.mate_for_white)
    prob_after = win_probability(norm_after.white_cp, norm_after.mate_for_white)

    delta_win_prob = prob_before - prob_after
    category = MoveCategory.from_delta(delta_win_prob)

    return MoveClassification(
        category=category,
        win_prob_before=prob_before,
        win_prob_after=prob_after,
        delta_win_prob=delta_win_prob,
        player=player,
    )
