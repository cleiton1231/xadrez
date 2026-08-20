"""Testes unitários e de propriedades para a classificação de lances (TDD v4).

Valida:
1. Função logística do Lichess para Win Probability.
2. Função unificada to_player_perspective (com checagem segura de None em cp e mate).
3. Classificação de lances com tolerância EPSILON = 1e-4 nas fronteiras exatas.
4. Normalização de perspectiva do jogador ativo (Brancas vs Pretas).
5. Limitação assumida da Fase 1 para distância de mate (+M1->+M8 e -M8->-M1).
"""

import chess
import pytest

from chess_analyzer.classify import (
    EPSILON,
    MoveCategory,
    PositionEvaluation,
    classify_move,
    to_player_perspective,
    win_probability,
)


class TestWinProbability:
    """Testes da função matemática win_probability."""

    def test_equal_position_evaluates_to_fifty_percent(self) -> None:
        """Posição de 0 cp deve resultar em exatamente 50% de probabilidade de vitória."""
        prob = win_probability(white_cp=0, mate_for_white=None)
        assert prob == pytest.approx(50.0, abs=1e-4)

    def test_imbalanced_positive_eval(self) -> None:
        """Valida pontos conhecidos da curva logística para centipawns positivos."""
        prob_800 = win_probability(white_cp=800, mate_for_white=None)
        assert prob_800 == pytest.approx(95.0053, abs=1e-3)

        prob_650 = win_probability(white_cp=650, mate_for_white=None)
        assert prob_650 == pytest.approx(91.6324, abs=1e-3)

    def test_imbalanced_negative_eval(self) -> None:
        """Valida pontos conhecidos da curva logística para centipawns negativos."""
        prob_minus_150 = win_probability(white_cp=-150, mate_for_white=None)
        assert prob_minus_150 == pytest.approx(36.5330, abs=1e-3)

        prob_minus_300 = win_probability(white_cp=-300, mate_for_white=None)
        assert prob_minus_300 == pytest.approx(24.8874, abs=1e-3)

    def test_mate_for_white_returns_hundred_percent(self) -> None:
        """Mate a favor das Brancas (+M) sempre resulta em 100% de Win%."""
        assert win_probability(white_cp=None, mate_for_white=1) == pytest.approx(100.0)
        assert win_probability(white_cp=None, mate_for_white=8) == pytest.approx(100.0)

    def test_mate_against_white_returns_zero_percent(self) -> None:
        """Mate contra as Brancas (-M) sempre resulta em 0% de Win%."""
        assert win_probability(white_cp=None, mate_for_white=-1) == pytest.approx(0.0)
        assert win_probability(white_cp=None, mate_for_white=-5) == pytest.approx(0.0)

    def test_invalid_evaluation_raises_value_error(self) -> None:
        """Avaliação sem centipawn e sem mate deve levantar ValueError."""
        with pytest.raises(ValueError, match="Pelo menos um"):
            win_probability(white_cp=None, mate_for_white=None)


class TestToPlayerPerspective:
    """Testes da função unificada de normalização de perspectiva."""

    def test_white_perspective_returns_unchanged(self) -> None:
        """Para as Brancas, os valores de cp e mate permanecem idênticos."""
        eval_pos = PositionEvaluation(white_cp=150, mate_for_white=None)
        res = to_player_perspective(eval_pos, color=chess.WHITE)
        assert res.white_cp == 150
        assert res.mate_for_white is None

        eval_mate = PositionEvaluation(white_cp=None, mate_for_white=3)
        res_mate = to_player_perspective(eval_mate, color=chess.WHITE)
        assert res_mate.white_cp is None
        assert res_mate.mate_for_white == 3

    def test_black_perspective_inverts_centipawns_safely(self) -> None:
        """Para as Pretas, inverte centipawns com segurança sem erro de None."""
        eval_pos = PositionEvaluation(white_cp=200, mate_for_white=None)
        res = to_player_perspective(eval_pos, color=chess.BLACK)
        assert res.white_cp == -200
        assert res.mate_for_white is None

    def test_black_perspective_inverts_mate_safely(self) -> None:
        """Para as Pretas, inverte mate_for_white (+2 vira -2) sem TypeError em white_cp."""
        eval_mate_for_white = PositionEvaluation(white_cp=None, mate_for_white=2)
        res = to_player_perspective(eval_mate_for_white, color=chess.BLACK)
        assert res.white_cp is None
        assert res.mate_for_white == -2

        eval_mate_for_black = PositionEvaluation(white_cp=None, mate_for_white=-4)
        res2 = to_player_perspective(eval_mate_for_black, color=chess.BLACK)
        assert res2.white_cp is None
        assert res2.mate_for_white == 4


class TestClassifyMove:
    """Testes de classificação de lance e prova de Win Probability vs Centipawns."""

    def test_best_move_neutral(self) -> None:
        """Lance em posição neutra que mantém a igualdade é BEST."""
        eval_before = PositionEvaluation(white_cp=0)
        eval_after = PositionEvaluation(white_cp=0)
        res = classify_move(eval_before, eval_after, player=chess.WHITE)
        assert res.category == MoveCategory.BEST
        assert res.delta_win_prob == pytest.approx(0.0, abs=1e-4)

    def test_best_move_gain(self) -> None:
        """Lance que aumenta a chance de vitória do jogador é BEST (delta negativo)."""
        eval_before = PositionEvaluation(white_cp=50)
        eval_after = PositionEvaluation(white_cp=100)
        res = classify_move(eval_before, eval_after, player=chess.WHITE)
        assert res.category == MoveCategory.BEST
        assert res.delta_win_prob < 0.0

    def test_proof_of_win_probability_vs_raw_centipawns(self) -> None:
        """Prova do conceito da Seção 5 do AGENT.md:

        Queda de 150cp em posição ganha (+800 -> +650) é apenas GOOD (delta ~= 3.37%).
        A MESMA queda de 150cp em posição igual (0 -> -150) é MISTAKE (delta ~= 13.47%).
        """
        # Caso 1: Posição desbalanceada ganha (+800 -> +650)
        eval_won_before = PositionEvaluation(white_cp=800)
        eval_won_after = PositionEvaluation(white_cp=650)
        res_won = classify_move(eval_won_before, eval_won_after, player=chess.WHITE)

        assert res_won.delta_win_prob == pytest.approx(3.3729, abs=1e-2)
        assert res_won.category == MoveCategory.GOOD

        # Caso 2: Posição equilibrada (0 -> -150)
        eval_equal_before = PositionEvaluation(white_cp=0)
        eval_equal_after = PositionEvaluation(white_cp=-150)
        res_equal = classify_move(eval_equal_before, eval_equal_after, player=chess.WHITE)

        assert res_equal.delta_win_prob == pytest.approx(13.4670, abs=1e-2)
        assert res_equal.category == MoveCategory.MISTAKE

    def test_black_player_perspective_normalization(self) -> None:
        """Garante que lances das Pretas são avaliados da perspectiva correta.

        Se as Pretas erram em 0cp e deixam as Brancas com +300cp,
        da ótica das Pretas a posição foi de 50% para 24.89% (perda de 25.11% = BLUNDER).
        """
        eval_before = PositionEvaluation(white_cp=0)
        eval_after = PositionEvaluation(white_cp=300)  # Vantagem branca

        res_black = classify_move(eval_before, eval_after, player=chess.BLACK)
        assert res_black.win_prob_before == pytest.approx(50.0, abs=1e-3)
        assert res_black.win_prob_after == pytest.approx(24.8874, abs=1e-3)
        assert res_black.delta_win_prob == pytest.approx(25.1126, abs=1e-3)
        assert res_black.category == MoveCategory.BLUNDER

    def test_mate_to_loss_is_blunder(self) -> None:
        """Deixar escapar mate forçado (+M2) e cair para -100cp é BLUNDER."""
        eval_before = PositionEvaluation(white_cp=None, mate_for_white=2)
        eval_after = PositionEvaluation(white_cp=-100, mate_for_white=None)
        res = classify_move(eval_before, eval_after, player=chess.WHITE)

        assert res.win_prob_before == pytest.approx(100.0)
        assert res.win_prob_after == pytest.approx(40.8974, abs=1e-3)
        assert res.delta_win_prob == pytest.approx(59.1026, abs=1e-3)
        assert res.category == MoveCategory.BLUNDER

    def test_mate_distance_limitation_symmetric(self) -> None:
        """Limitação assumida da Fase 1 documentada na v4:

        +M1 -> +M8 continua avaliado como 100% -> 100% (BEST).
        -M8 -> -M1 continua avaliado como 0% -> 0% (BEST pela limitação de distância).
        """
        # Caso positivo: prolongar vitória
        eval_pos_before = PositionEvaluation(white_cp=None, mate_for_white=1)
        eval_pos_after = PositionEvaluation(white_cp=None, mate_for_white=8)
        res_pos = classify_move(eval_pos_before, eval_pos_after, player=chess.WHITE)
        assert res_pos.delta_win_prob == pytest.approx(0.0)
        assert res_pos.category == MoveCategory.BEST

        # Caso negativo: acelerar derrota
        eval_neg_before = PositionEvaluation(white_cp=None, mate_for_white=-8)
        eval_neg_after = PositionEvaluation(white_cp=None, mate_for_white=-1)
        res_neg = classify_move(eval_neg_before, eval_neg_after, player=chess.WHITE)
        assert res_neg.delta_win_prob == pytest.approx(0.0)
        assert res_neg.category == MoveCategory.BEST


class TestCategoryBoundariesWithEpsilon:
    """Testes explícitos nas fronteiras exatas com tolerância EPSILON."""

    def test_boundary_zero_percent(self) -> None:
        """Delta <= 0.0 + EPSILON é BEST."""
        assert MoveCategory.from_delta(0.0) == MoveCategory.BEST
        assert MoveCategory.from_delta(-0.0001) == MoveCategory.BEST
        assert MoveCategory.from_delta(0.0 + EPSILON) == MoveCategory.BEST
        assert MoveCategory.from_delta(0.0 + EPSILON + 1e-6) == MoveCategory.EXCELLENT

    def test_boundary_two_percent(self) -> None:
        """Delta <= 2.0 + EPSILON é EXCELLENT; acima é GOOD."""
        assert MoveCategory.from_delta(2.0) == MoveCategory.EXCELLENT
        assert MoveCategory.from_delta(2.0 + EPSILON) == MoveCategory.EXCELLENT
        assert MoveCategory.from_delta(2.0 + EPSILON + 1e-6) == MoveCategory.GOOD

    def test_boundary_five_percent(self) -> None:
        """Delta <= 5.0 + EPSILON é GOOD; acima é INACCURACY."""
        assert MoveCategory.from_delta(5.0) == MoveCategory.GOOD
        assert MoveCategory.from_delta(5.0 + EPSILON) == MoveCategory.GOOD
        assert MoveCategory.from_delta(5.0 + EPSILON + 1e-6) == MoveCategory.INACCURACY

    def test_boundary_ten_percent(self) -> None:
        """Delta <= 10.0 + EPSILON é INACCURACY; acima é MISTAKE."""
        assert MoveCategory.from_delta(10.0) == MoveCategory.INACCURACY
        assert MoveCategory.from_delta(10.0 + EPSILON) == MoveCategory.INACCURACY
        assert MoveCategory.from_delta(10.0 + EPSILON + 1e-6) == MoveCategory.MISTAKE

    def test_boundary_twenty_percent(self) -> None:
        """Delta <= 20.0 + EPSILON é MISTAKE; acima é BLUNDER."""
        assert MoveCategory.from_delta(20.0) == MoveCategory.MISTAKE
        assert MoveCategory.from_delta(20.0 + EPSILON) == MoveCategory.MISTAKE
        assert MoveCategory.from_delta(20.0 + EPSILON + 1e-6) == MoveCategory.BLUNDER
