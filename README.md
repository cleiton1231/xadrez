# Chess Performance Analyzer

Ferramenta local que analisa o histórico de partidas de xadrez (PGN) com Stockfish, identifica padrões de erro por lance, fase do jogo, abertura e cor via modelo logístico de probabilidade de vitória ($\Delta\text{Win\%}$), e gera sessões de treino tático direcionado a partir do dataset público de 6,1 milhões de puzzles do Lichess.

---

## 1. Status Geral do Projeto

> [!NOTE]
> **Status:** **Fase 1 (MVP) e Fase 2 (Treino Direcionado) concluídas.** O projeto foi retomado em 2026-08 para estabilização do núcleo (integridade de dados, robustez SQLite/download, CI).
>
> Relatórios históricos de auditoria: [`docs/reports/auditoria_fase1_teamwork.md`](docs/reports/auditoria_fase1_teamwork.md) e [`docs/reports/auditoria_fase2_teamwork.md`](docs/reports/auditoria_fase2_teamwork.md).
>
> **Próxima direção de produto:** ver [`docs/ROADMAP.md`](docs/ROADMAP.md) — prioridade recomendada: uso real → MCP → temas táticos.

---

## 2. Visão Geral da Arquitetura

```
┌────────────────────────┐
│  Arquivos PGN locais   │ (Chess.com / Lichess)
└───────────┬────────────┘
            │  chess-analyzer import
            ▼
┌────────────────────────┐      ┌─────────────────────────┐
│     SQLite Local       │ <──> │    Stockfish Engine     │ (UCI local, depth 12)
│  (data/chess_analyzer) │      └─────────────────────────┘
└───────────┬────────────┘
            │
            ├─► chess-analyzer stats (Agregação por Cor, ECO e Fase via ΔWin%)
            │
            ▼
┌────────────────────────┐      ┌─────────────────────────┐
│  Dataset Lichess       │ ───> │  SQLite Schema v2       │
│  (6.1M Puzzles .zst)   │      │  (puzzles + temas)      │
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
- **SQLite:** Persistência transacional com `PRAGMA foreign_keys = ON`, `PRAGMA journal_mode = WAL` e cache FEN normalizado.
- **`zstandard`:** Descompressão em streaming para indexação eficiente de 6.1M de puzzles sem inflar disco.
- **Typer & Rich:** CLI ergonômica com saída em tabelas coloridas e payloads JSON estruturados.
- **Testes & Qualidade:** `pytest` (97 testes unitários e de integração), `ruff` e `mypy`.

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

## 5. Comandos Disponíveis (Guia de Uso com Saídas Reais)

### 1. `chess-analyzer import <pgn>`
Importa partidas de um arquivo PGN local. Processo idempotente que calcula o hash SHA-256 da partida para evitar duplicações.

```bash
$ chess-analyzer import tests/fixtures/lichess_real.pgn
Importação concluída com sucesso!
Total processado: 1 | Inseridas: 1 | Ignoradas/Duplicadas: 0
```

---

### 2. `chess-analyzer analyze`
Executa o Stockfish em todas as posições pendentes no banco de dados, calculando a perda de probabilidade de vitória ($\Delta\text{Win\%}$) de cada lance e alimentando o cache FEN.

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
- `--count` / `-n`: Quantidade de puzzles (padrão: `10`).
- `--phase` / `-p`: Forçar uma fase específica (`opening`, `middlegame`, `endgame`).
- `--rating-window` / `-w`: Margem de rating em torno do Elo do jogador (padrão: `200`).
- `--json`: Exporta a lista de exercícios em formato JSON estruturado.

---

### 5. `chess-analyzer puzzles index`
Baixa (via `--download`) ou lê um arquivo local (`--file`) do dataset oficial do Lichess (`lichess_db_puzzle.csv.zst`), indexando os 6.100.960 puzzles e temas com streaming descompactado e transações em lote. Possui checagem prévia de espaço em disco (3.5 GB), download atômico (`.part`) e proteção por hash SHA-256 contra re-indexações desnecessárias.

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

## 6. Critério de Classificação de Lances (Win Probability)

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

## 7. Limitações Conhecidas

Para contextualizar quem for utilizar ou retomar o projeto:

1. **Exports do Chess.com e a tag `[ECO]`:**  
   Arquivos PGN exportados do Chess.com frequentemente omitem a tag `[ECO]`. Nesses casos, a agregação por abertura (`--by opening`) **exclui** partidas sem ECO da agregação (não aparecem como grupo vazio).
2. **PGNs com FEN inicial customizado (`[SetUp "1"]`):**  
   Suportados desde a retomada v0.1.x — a FEN inicial é persistida e usada na análise.
3. **Cálculo de Elo e Tamanho de Amostra:**  
   O Elo do jogador é estimado pela média aritmética de `white_elo`/`black_elo` das partidas analisadas. Com poucas partidas registradas (amostra pequena indicada no diagnóstico), a calibração de rating do treino tático tem confiabilidade estatística reduzida.
4. **Concorrência SQLite e `PRAGMA busy_timeout`:**  
   O gerenciador de conexões em `src/chess_analyzer/db.py` ativa `WAL`, `foreign_keys=ON`, `synchronous=NORMAL` e `busy_timeout=5000ms`.
5. **Cache de avaliações por engine:**  
   O cache SQLite diferencia avaliações por `(fen, depth, engine_key)`, onde `engine_key` deriva do caminho do binário Stockfish e profundidade configurada.

---

## 8. Como Retomar o Projeto

Para retomar o desenvolvimento ou rodar o projeto do zero:

1. **Ativar o Ambiente Virtual:**
   ```bash
   source .venv/bin/activate
   ```
2. **Validar o Binário do Stockfish:**
   Certifique-se de que o Stockfish está disponível em `$PATH` (`which stockfish`) ou posicione o executável em `.venv/bin/stockfish`.
3. **Executar a Suíte de Verificação Integral:**
   Confirme que as dependências do ambiente continuam íntegras:
   ```bash
   pytest tests/ -v          # ~105 testes (integração Stockfish opcional se binário ausente)
   ruff check src/ tests/    # 0 erros de lint
   mypy src/chess_analyzer/  # 0 erros de tipagem
   ```
4. **Consultar roadmap:** [`docs/ROADMAP.md`](docs/ROADMAP.md) para direção de produto pós-estabilização.
