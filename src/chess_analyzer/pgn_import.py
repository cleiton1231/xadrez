import logging
from collections.abc import Iterator
from dataclasses import dataclass

import chess
import chess.pgn

STANDARD_START_FEN = chess.STARTING_FEN


@dataclass
class ParsedMove:
    """Representa um lance único efetuado na linha principal da partida."""
    ply: int
    san: str
    fen_after: str


@dataclass
class ParsedGame:
    """Contrato imutável de uma partida após extração do PGN."""
    white: str
    black: str
    result: str               # "1-0", "0-1", "1/2-1/2" ou "*"
    moves: list[ParsedMove]
    event: str | None = None
    site: str | None = None       # cru; normalização fica pra Etapa 5
    date: str | None = None       # YYYY.MM.DD
    white_elo: int | None = None
    black_elo: int | None = None
    time_control: str | None = None
    eco: str | None = None
    variant: str | None = None
    starting_fen: str = STANDARD_START_FEN


def parse_pgn_file(file_path: str) -> Iterator[ParsedGame]:
    """Faz o parse iterativo de um arquivo PGN retornando um gerador de partidas."""
    with open(file_path, encoding="utf-8") as f:
        try:
            content = f.read(4096)
        except UnicodeDecodeError as e:
            msg = (
                "Arquivo com conteúdo real não reconhecível "
                f"como PGN (lixo binário): {file_path}"
            )
            raise ValueError(msg) from e

        if not content.strip():
            f.seek(0)
            try:
                if not f.read().strip():
                    return
            except UnicodeDecodeError as e:
                msg = (
                    "Arquivo com conteúdo real não reconhecível "
                    f"como PGN (lixo binário): {file_path}"
                )
                raise ValueError(msg) from e
        f.seek(0)

        game_index = 0
        valid_games_count = 0
        warnings_buffer = []

        while True:
            try:
                game = chess.pgn.read_game(f)
            except UnicodeDecodeError as e:
                msg = (
                    "Arquivo com conteúdo real não reconhecível "
                    f"como PGN (lixo binário): {file_path}"
                )
                raise ValueError(msg) from e
            except Exception as e:
                msg = f"Erro inesperado ao ler PGN no jogo {game_index + 1}: {e}"
                logging.error(msg)
                raise ValueError(msg) from e

            if game is None:
                break

            game_index += 1

            if game.errors:
                msg = f"Erros de parsing no jogo {game_index}: {game.errors[0]}. Pulando partida."
                if valid_games_count == 0:
                    warnings_buffer.append(msg)
                else:
                    logging.warning(msg)
                continue

            headers = game.headers
            white = headers.get("White")
            if white == "?":
                white = None
            black = headers.get("Black")
            if black == "?":
                black = None
            result = headers.get("Result")

            if not white or not black or not result:
                missing = []
                if not white:
                    missing.append("White")
                if not black:
                    missing.append("Black")
                if not result:
                    missing.append("Result")
                missing_str = ", ".join(missing)
                msg = f"Missing mandatory header {missing_str} no jogo {game_index}."
                msg += " Pulando partida."
                if valid_games_count == 0:
                    warnings_buffer.append(msg)
                else:
                    logging.warning(msg)
                continue

            moves = []
            for node in game.mainline():
                moves.append(ParsedMove(
                    ply=node.ply(),
                    san=node.san(),
                    fen_after=node.board().fen()
                ))

            event = headers.get("Event")
            if event == "?":
                event = None
            site = headers.get("Site")
            if site == "?":
                site = None
            date = headers.get("Date")
            if date == "????.??.??":
                date = None

            white_elo_str = headers.get("WhiteElo")
            black_elo_str = headers.get("BlackElo")

            valid_games_count += 1
            if warnings_buffer:
                for w in warnings_buffer:
                    logging.warning(w)
                warnings_buffer.clear()

            setup = headers.get("SetUp", "0")
            fen_header = headers.get("FEN")
            if setup == "1" and fen_header:
                starting_fen = fen_header
            else:
                starting_fen = STANDARD_START_FEN

            yield ParsedGame(
                white=white,
                black=black,
                result=result,
                moves=moves,
                event=event,
                site=site,
                date=date,
                white_elo=int(white_elo_str) if white_elo_str and white_elo_str.isdigit() else None,
                black_elo=int(black_elo_str) if black_elo_str and black_elo_str.isdigit() else None,
                time_control=headers.get("TimeControl"),
                eco=headers.get("ECO"),
                variant=headers.get("Variant"),
                starting_fen=starting_fen,
            )

        if valid_games_count == 0:
            raise ValueError(f"Arquivo com conteúdo real não reconhecível como PGN: {file_path}")
