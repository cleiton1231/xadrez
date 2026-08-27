# Chess Performance Analyzer

Ferramenta local que analisa o histórico de partidas de xadrez (PGN) com Stockfish, identifica padrões de erro por lance, fase do jogo, abertura e cor via modelo logístico de probabilidade de vitória ($\Delta\text{Win\%}$), e gera sessões de treino tático direcionado a partir do dataset público de 6,1 milhões de puzzles do Lichess.

---

## 1. Status Geral do Projeto

> [!NOTE]
> **Status:** **Fase 1 (MVP) e Fase 2 (Treino Direcionado) concluídas e estabilizadas.** A retomada de 2026-08 (merge do [PR #1](https://github.com/cleiton1231/xadrez/pull/1)) fechou gaps de integridade de dados, robustez SQLite/download e CI.
>
> Relatórios históricos de auditoria: [`docs/reports/auditoria_fase1_teamwork.md`](docs/reports/auditoria_fase1_teamwork.md) e [`docs/reports/auditoria_fase2_teamwork.md`](docs/reports/auditoria_fase2_teamwork.md).
>
> **Ainda fora do núcleo (constituição em [`GEMINI.md`](GEMINI.md)):** análise por estrutura de peões, Fase 3 (MCP, coaching LLM, dashboard web) e inferência de ECO em PGNs do Chess.com. Direção recomendada: [`docs/ROADMAP.md`](docs/ROADMAP.md) — uso real → MCP → temas táticos.

---

## 2. Visão Geral da Arquitetura

```
┌────────────────────────┐
│  Arquivos PGN locais   │ (Chess.com / Lichess)
└───────────┬────────────┘
            │  chess-analyzer import
            ▼
┌────────────────────────┐      ┌─────────────────────────┐
│     SQLite Local v3    │ <──> │    Stockfish Engine     │ (UCI local, depth 12)
│  (data/chess_analyzer) │      └─────────────────────────┘
│  starting_fen + cache  │
└───────────┬────────────┘
            │
            ├─► chess-analyzer stats (Agregação por Cor, ECO e Fase via ΔWin%)
            │
            ▼
┌────────────────────────┐      ┌─────────────────────────┐
│  Dataset Lichess       │ ───> │  puzzles + temas        │
│  (6.1M Puzzles .zst)   │      │  (reindex via staging)  │
└────────────────────────┘      └────────────┬────────────┘
                                             │
                                             ▼
                                ┌─────────────────────────┐
                                │   chess-analyzer train  │ (Treino tático calibrado
                                └─────────────────────────┘  por Elo e fase fraca)
```

---

## 3. Stack Técnica

- **Python 3.11+** com type hints estritos (`mypy` limpo com `disallow_untyped_defs = true`).
- **`python-chess`:** Manipulação de tabuleiros, lances SAN/UCI, parsing canônico de PGN e representação FEN.
- **Stockfish (UCI):** Binário local de motor de xadrez (não vendorizado, gerenciado com lifecycle context manager).
- **SQLite:** Persistência transacional com `PRAGMA foreign_keys = ON`, `PRAGMA journal_mode = WAL`, `busy_timeout=5000ms` e cache FEN normalizado isolado por `(fen, depth, engine_key)` (schema v3).
- **`zstandard`:** Descompressão em streaming para indexação eficiente de 6.1M de puzzles sem inflar disco.
- **Typer & Rich:** CLI ergonômica com saída em tabelas coloridas e payloads JSON estruturados.
- **Testes & Qualidade:** `pytest` (108 testes; integração Stockfish é skipped se o binário não estiver no PATH), `ruff`, `mypy` e CI GitHub Actions (Python 3.12 + Stockfish).

---

## 4. Instalação e Configuração

### Requisitos Prévios
1. **Python 3.11+**
2. **Stockfish Engine:** Binário instalado no sistema ou em `.venv/bin/stockfish`.
   - *Ubuntu/Debian:* `sudo apt install stockfish`
   - *Arch Linux:* `sudo pacman -S stockfish`
   - *macOS:* `brew install stockfish`

### Instalação Local

```bash
# 1. Clonar o repositório
git clone https://github.com/cleiton1231/xadrez.git
cd xadrez

# 2. Criar e ativar o ambiente virtual
python3 -m venv .venv
source .venv/bin/activate

# 3. Instalar o projeto em modo editável com dependências de desenvolvimento
pip install -e ".[dev]"
```

### Variável de Ambiente `CHESS_ANALYZER_DB`
Todos os comandos do CLI aceitam a opção `--db` / `-d` para definir o caminho do banco SQLite. Para evitar passar a flag repetidamente, você pode configurar a variável de ambiente:

```bash
export CHESS_ANALYZER_DB="/caminho/personalizado/meu_banco.db"
```
Se não informada, o CLI adota o padrão `data/chess_analyzer.db`.

---

## 5. Novidades da Retomada (2026-08)

Mudanças entregues no [PR #1](https://github.com/cleiton1231/xadrez/pull/1) e já em `main`:

- **PGNs com FEN inicial customizado:** headers `[SetUp "1"]` + `[FEN]` são lidos na importação, persistidos em `games.starting_fen` e usados como posição inicial da análise (antes o analisador sempre partia de `chess.STARTING_FEN`).
- **Schema SQLite v3:** migration automática a partir de bancos v1/v2; coluna `starting_fen` em `games` e `engine_key` no cache de avaliações.
- **Cache de avaliações por engine:** chave estável derivada do binário Stockfish + profundidade, para não misturar evals de engines ou depths diferentes.
- **SQLite `busy_timeout`:** 5s no `connect()` e em `PRAGMA busy_timeout`, reduzindo `database is locked` em execuções paralelas.
- **Reindexação segura de puzzles:** escrita em tabelas `*_staging` e promoção atômica; falha no meio do index **preserva** o dataset anterior (antes o clear acontecia no início).
- **Download mais robusto:** timeout de 300s, limpeza do arquivo `.part` em erro/timeout, e `Content-Length` ainda validado.
- **CLI mais rígida:** `train --count` / `--rating-window` e `puzzles index --batch-size` rejeitam valores `< 1`; `stats` trata erros de banco com mensagem e exit code 1.
- **CI:** `.github/workflows/ci.yml` roda `ruff`, `mypy` e `pytest` em Python 3.12 com Stockfish instalado.
- **Testes:** 108 casos, com skip gracioso quando Stockfish não está instalado; fixture `tests/fixtures/custom_fen.pgn` cobre o novo caminho de FEN.

---

## 6. Comandos Disponíveis (Guia de Uso com Saídas Reais)

### 1. `chess-analyzer import <pgn>`
Importa partidas de um arquivo PGN local. Processo idempotente que calcula o hash SHA-256 da partida para evitar duplicações. PGNs com `[SetUp "1"]` e `[FEN "..."]` gravam a posição inicial em `starting_fen` (padrão: posição inicial clássica).

```bash
$ chess-analyzer import tests/fixtures/lichess_real.pgn
Importação concluída com sucesso!
Total processado: 1 | Inseridas: 1 | Ignoradas/Duplicadas: 0
```

---

### 2. `chess-analyzer analyze`
Executa o Stockfish em todas as posições pendentes no banco de dados, calculando a perda de probabilidade de vitória ($\Delta\text{Win\%}$) de cada lance e alimentando o cache FEN. A análise começa na FEN persistida da partida (não assume tabuleiro inicial se o PGN tinha setup customizado). Avaliações ficam isoladas por `engine_key` (binário + profundidade).

```bash
$ chess-analyzer analyze
Análise concluída com sucesso!
Partidas analisadas: 1/1 | Lances avaliados: 3
```

---

### 3. `chess-analyzer stats <jogador>`
Gera agregações estatísticas por Cor, Abertura (código ECO) e Fase do Jogo (Opening, Middlegame, Endgame).

```bash
$ chess-analyzer stats "Player1"
```
**Saída:**
```
                              Estatísticas por Cor                              
┏━━━━━━━┳━━━━━━━━┳━━━━━━┳━━━━━┳━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃       ┃        ┃      ┃     ┃      ┃      ┃      ┃       ┃       Perda Média ┃
┃ Cor   ┃ Lances ┃ Best ┃ Exc ┃ Good ┃ Inac ┃ Mist ┃ Blund ┃           (ΔWin%) ┃
┡━━━━━━━╇━━━━━━━━╇━━━━━━╇━━━━━╇━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ white │      2 │    0 │   1 │    0 │    1 │    0 │     0 │             2.75% │
└───────┴────────┴──────┴─────┴──────┴──────┴──────┴───────┴───────────────────┘
                        Estatísticas por Abertura (ECO)                         
┏━━━━━┳━━━━━━━━┳━━━━━━┳━━━━━┳━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ ECO ┃ Lances ┃ Best ┃ Exc ┃ Good ┃ Inac ┃ Mist ┃ Blund ┃ Perda Média (ΔWin%) ┃
┡━━━━━╇━━━━━━━━╇━━━━━━╇━━━━━╇━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ C20 │      2 │    0 │   1 │    0 │    1 │    0 │     0 │               2.75% │
└─────┴────────┴──────┴─────┴──────┴──────┴──────┴───────┴─────────────────────┘
                         Estatísticas por Fase do Jogo                          
┏━━━━━━━━━┳━━━━━━━━┳━━━━━━┳━━━━━┳━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃         ┃        ┃      ┃     ┃      ┃      ┃      ┃       ┃     Perda Média ┃
┃ Fase    ┃ Lances ┃ Best ┃ Exc ┃ Good ┃ Inac ┃ Mist ┃ Blund ┃         (ΔWin%) ┃
┡━━━━━━━━━╇━━━━━━━━╇━━━━━━╇━━━━━╇━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ OPENING │      2 │    0 │   1 │    0 │    1 │    0 │     0 │           2.75% │
└─────────┴────────┴──────┴─────┴──────┴──────┴──────┴───────┴─────────────────┘
```

Opções:
- `--by`: Dimensão de agregação (`all`, `color`, `opening`, `phase`).
- `--json`: Exporta os dados estruturados em JSON para integração externa.

```bash
$ chess-analyzer stats "Player1" --by phase --json
{
  "player": "Player1",
  "total_analyzed_moves": 2,
  "game_phase": [
    {
      "group_key": "OPENING",
      "group_type": "game_phase",
      "category_counts": {
        "best": 0,
        "excellent": 1,
        "good": 0,
        "inaccuracy": 1,
        "mistake": 0,
        "blunder": 0,
        "total": 2
      },
      "avg_delta_win_prob": 2.7535360707690337,
      "total_moves": 2
    }
  ]
}
```

---

### 4. `chess-analyzer train <jogador>`
Detecta a fase do jogo onde o jogador mais erra ($\Delta\text{Win\%}$ médio ou taxa de blunders como desempate), calcula o Elo médio das partidas e extrai do dataset do Lichess uma lista calibrada de puzzles, com o lance do oponente já executado no FEN (`training_fen`) e solução em SAN.

```bash
$ chess-analyzer train "Player1" --count 10
```
**Saída:**
```
🎯 Treino Direcionado para: Player1
Fase mais fraca identificada: OPENING (Perda média: 2.75% ΔWin%, 0 Blunders em 2 lances)
Elo estimado: 1500 (baseado em 1 partida) | Tema dos puzzles: opening

                      Puzzles Selecionados (10 exercícios)                      
┏━━━━┳━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃  # ┃ ID    ┃ Rating ┃ Lance Oponente ┃ Solução           ┃ FEN de Treino     ┃
┡━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│  1 │ 033xz │   1495 │ dxc5           │ Ne4+ Ke1 Nc3+     │ rn2k2r/pp2qppp/2… │
│    │       │        │                │                   │ b kq - 0 10       │
│  2 │ 03Ht0 │   1330 │ h6             │ Nxf6+ gxf6 Qh7#   │ r1bq1rk1/ppp2pp1… │
│    │       │        │                │                   │ w KQ - 0 10       │
│  3 │ 04gjJ │   1380 │ Kf8            │ Qxf7#             │ rnbqrk2/ppp2pp1/… │
│    │       │        │                │                   │ w KQ - 4 13       │
│  4 │ 06CFU │   1322 │ f3             │ Qh4+ g3 Nxg3 Bxg3 │ r2qk2r/pppn1ppp/… │
│    │       │        │                │ Qxg3+             │ b KQkq - 0 9      │
│  5 │ 08852 │   1474 │ Nc3            │ Qxg2#             │ 2kr1b1r/pbp2pp1/… │
│    │       │        │                │                   │ b - - 1 14        │
│  6 │ 09GfS │   1673 │ dxc3           │ Qxd8+ Kxd8 Bxf6+  │ rnbqk2r/pp3p1p/4… │
│    │       │        │                │                   │ w KQkq - 0 10     │
│  7 │ 0BNak │   1596 │ Nxe5           │ Qxf2#             │ r1b1k1nr/bpp2ppp… │
│    │       │        │                │                   │ b KQkq - 0 10     │
│  8 │ 0EYRm │   1453 │ Re8            │ Ng5+ Kg8 Qe6+     │ rn1qr3/pbp1nkpp/… │
│    │       │        │                │                   │ w KQ - 1 13       │
│  9 │ 0FrmJ │   1525 │ Bxf3           │ Bxf7#             │ rn1qkb1r/p3nppp/… │
│    │       │        │                │                   │ w kq - 0 9        │
│ 10 │ 0OUgY │   1484 │ dxc6           │ Nxf3+ Nxf3 Bxc6   │ r3kb1r/pbpq2pp/2… │
│    │       │        │                │                   │ b kq - 0 13       │
└────┴───────┴────────┴────────────────┴───────────────────┴───────────────────┘
```

Opções:
- `--count` / `-n`: Quantidade de puzzles (padrão: `10`, mínimo `1`).
- `--phase` / `-p`: Forçar uma fase específica (`opening`, `middlegame`, `endgame`).
- `--rating-window` / `-w`: Margem de rating em torno do Elo do jogador (padrão: `200`, mínimo `1`).
- `--json`: Exporta a lista de exercícios em formato JSON estruturado.

---

### 5. `chess-analyzer puzzles index`
Baixa (via `--download`) ou lê um arquivo local (`--file`) do dataset oficial do Lichess (`lichess_db_puzzle.csv.zst`), indexando os 6.100.960 puzzles e temas com streaming descompactado e transações em lote. Possui checagem prévia de espaço em disco (3.5 GB), download atômico (`.part`) com timeout de 300s e limpeza em falha, proteção por hash SHA-256 contra re-indexações desnecessárias, e reindexação via tabelas staging (o dataset anterior só é substituído se o lote inteiro concluir). `--batch-size` deve ser `>= 1`.

```bash
$ chess-analyzer puzzles index --file data/lichess_db_puzzle.csv.zst
Indexando puzzles de data/lichess_db_puzzle.csv.zst ...
Dataset já indexado com este arquivo (SHA-256 idêntico).
Use --force para re-indexar.
```

---

### 6. `chess-analyzer puzzles status`
Exibe os metadados da indexação atual: hash do arquivo fonte, data da indexação e contagem total de puzzles persistidos.

```bash
$ chess-analyzer puzzles status
                          Status do Dataset de Puzzles                          
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Chave         ┃ Valor                                                        ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ file_sha256   │ a0ea9129c6b6434dfb34a9ac4ec660c9cfff22b2de465e01854f018fc84… │
│ indexed_at    │ 2026-08-22T14:18:13.253523+00:00                             │
│ puzzle_count  │ 6100960                                                      │
│ source_url    │ https://database.lichess.org/lichess_db_puzzle.csv.zst       │
│ puzzles_in_db │ 6100960                                                      │
└───────────────┴──────────────────────────────────────────────────────────────┘
```

---

## 7. Critério de Classificação de Lances (Win Probability)

Em conformidade com o modelo matemático do Lichess, a avaliação bruta em centipawns ($cp$) na perspectiva das Brancas é convertida em probabilidade de vitória ($W$) de 0.0% a 100.0% através de regressão logística:

$$W(cp) = \frac{100}{1 + e^{-0.00368208 \cdot cp}}$$

- **Normalização:** Perspectiva ajustada para o jogador da vez antes do cálculo.
- **Mate:** Mate a favor ($+M$) = 100.0%; mate contra ($-M$) = 0.0%.
- **Queda de Probabilidade ($\Delta\text{Win\%}$):** $\Delta W = W_{\text{antes}} - W_{\text{depois}}$.

| Categoria | Faixa de $\Delta\text{Win\%}$ |
|---|---|
| **BEST** | $\Delta W \le 0.0\%$ |
| **EXCELLENT** | $0.0\% < \Delta W \le 2.0\%$ |
| **GOOD** | $2.0\% < \Delta W \le 5.0\%$ |
| **INACCURACY** | $5.0\% < \Delta W \le 10.0\%$ |
| **MISTAKE** | $10.0\% < \Delta W \le 20.0\%$ |
| **BLUNDER** | $\Delta W > 20.0\%$ |

---

## 8. O Que Ainda Falta (`GEMINI.md`)

Constituição do projeto: [`GEMINI.md`](GEMINI.md). DoD das Fases 1 e 2 está cumprido (import → analyze → stats com Stockfish real; `train` gera puzzles do tema mais fraco). Pendências explícitas:

| Item | Onde está escrito | Estado |
|---|---|---|
| **Análise por estrutura de peões** | Objetivo original, nota no §1 do `GEMINI.md` | Não implementada. Stats cobrem fase, cor e ECO — não estrutura. |
| **Fase 3 — servidor MCP** | `GEMINI.md` §2: tools `analyze_pgn`, `get_weak_themes`, `generate_training_set` via FastMCP | Não iniciado. Roadmap coloca MCP como prioridade 3, depois de uso real. |
| **Fase 3 — coaching LLM** | Explicar o porquê de um erro (llama.cpp local ou API) | Adiado até MCP + dados confiáveis. |
| **Fase 3 — dashboard web** | Só se CLI local não bastar | Adiado. |
| **Inferência de ECO (Chess.com)** | Limitação conhecida + roadmap P2 | PGNs sem `[ECO]` são excluídos de `--by opening`; não há árvore de aberturas. |
| **Elo com intervalo de confiança** | Roadmap P2 | Média aritmética simples; amostra pequena deixa o `train` mal calibrado. |
| **Temas táticos (fork, pin, skewer)** | Stretch / Fase 2.5 no `GEMINI.md` e roadmap | Treino hoje usa só o tema da fase fraca (`opening` / `middlegame` / `endgame`). |

Não pular para Fase 3 enquanto o núcleo não for usado de verdade com partidas próprias — regra do `GEMINI.md` §2.

---

## 9. Limitações Conhecidas

1. **Exports do Chess.com e a tag `[ECO]`:**  
   Arquivos PGN do Chess.com frequentemente omitem `[ECO]`. A agregação `--by opening` **exclui** essas partidas (não cria grupo vazio). Sem inferência automática ainda.
2. **Hash de partida não inclui `starting_fen`:**  
   Dois PGNs com os mesmos jogadores/data/resultado/lances SAN e FEN inicial diferente podem colidir no `game_hash` e a segunda importação é ignorada. Raro em partidas reais; mais plausível em studies.
3. **Cálculo de Elo e tamanho de amostra:**  
   Elo = média de `white_elo`/`black_elo` das partidas do jogador. Amostra pequena reduz a qualidade da janela de rating do `train`.
4. **Stockfish no PATH:**  
   No Ubuntu o binário costuma ficar em `/usr/games/stockfish`, fora do PATH padrão. A suíte procura esse caminho; o CLI ainda defaulta para `.venv/bin/stockfish` (use `--engine-path`).
5. **CI em PRs de fork:**  
   O workflow em `.github/workflows/ci.yml` precisa de Actions habilitado (e, em PRs de primeira contribuição, aprovação do maintainer).

---

## 10. Como Verificar e Seguir

1. **Ativar o ambiente virtual:**
   ```bash
   source .venv/bin/activate
   ```
2. **Validar o Stockfish:**
   `which stockfish` ou `--engine-path /usr/games/stockfish` (Debian/Ubuntu).
3. **Suíte de verificação:**
   ```bash
   pytest tests/ -v          # 108 testes (integração Stockfish skipped se o binário ausente)
   ruff check src/ tests/    # 0 erros de lint
   mypy src/chess_analyzer/  # 0 erros de tipagem
   ```
4. **Próximo passo de produto:** [`docs/ROADMAP.md`](docs/ROADMAP.md) — importar partidas reais, depois MCP.
