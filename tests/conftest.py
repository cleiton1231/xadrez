"""Fixtures compartilhadas e descoberta do binário Stockfish para testes de integração."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

STANDARD_START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def find_stockfish_path() -> str | None:
    """Procura Stockfish em locais comuns sem instalar pacotes de sistema."""
    candidates: list[Path | None] = [
        Path(".venv/bin/stockfish"),
        Path("/usr/games/stockfish"),
        Path("/usr/bin/stockfish"),
    ]
    which = shutil.which("stockfish")
    if which:
        candidates.insert(0, Path(which))

    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return str(candidate)
    return None


STOCKFISH_PATH = find_stockfish_path()

requires_stockfish = pytest.mark.skipif(
    STOCKFISH_PATH is None,
    reason="Stockfish não encontrado. Instale via apt/brew ou coloque em .venv/bin/stockfish.",
)
