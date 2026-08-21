# Chess Performance Analyzer

Ferramenta local para análise do histórico de partidas de xadrez (PGN) com Stockfish, identificando padrões de erro por lance, fase do jogo, abertura e cor para direcionar o treinamento e evolução pessoal no xadrez.

---

## 1. Status Atual do Projeto

> **Fase atual:** Fase 1 (MVP) — Em andamento.

O projeto segue desenvolvimento orientado a testes (TDD) e rigor metodológico. O estado real dos componentes é:

- **Implementado e Testado:**
  - `src/chess_analyzer/classify.py`: Classificação precisa de lances baseada na perda de probabilidade de vitória ($\Delta\text{Win\%}$), convertendo centipawns via regressão logística com normalização de perspectiva e tratamento de mate.
  - `src/chess_analyzer/engine.py`: Wrapper UCI para o Stockfish com gerenciamento de ciclo de vida (`with`), bypass automático para posições de fim de jogo e avaliação síncrona com timeout seguro.
  - `src/chess_analyzer/pgn_import.py`: Parsing iterativo (streaming via generator `Iterator[ParsedGame]`) de arquivos PGN com suporte ao Lichess e Chess.com, canonicidade de lances SAN e robustez contra arquivos binários ou malformados.
  - `src/chess_analyzer/db.py`: Persistência local em SQLite com journal mode WAL, garantia de integridade referencial (`PRAGMA foreign_keys = ON`), deduplicação canônica idempotente por hash SHA-256, FEN normalizado (4 campos essenciais) e cache de avaliações de posições por profundidade (`depth`).
  - Suíte de testes automatizados (`tests/test_classify.py`, `tests/test_engine.py`, `tests/test_pgn_import.py`, `tests/test_db.py`) cobrindo a lógica core (53 testes passando).
- **Em Planejamento / Próximos Passos (Fase 1):**
  - `src/chess_analyzer/stats.py`: Agregação estatística de erros (por fase, abertura ECO e cor).
  - `src/chess_analyzer/cli.py`: Interface de linha de comando (CLI via Typer/Rich) para processamento de arquivos e visualização de tabelas/JSON.
- **Roadmap Futuro (Fases 2 e 3):**
  - Fase 2: Integração com dataset de puzzles do Lichess (`puzzles.py`).
  - Fase 3: Servidor MCP / Coaching explicativo com LLM (planejado no `GEMINI.md`).

---

## 2. Stack Técnica

- **Linguagem & Tipagem:** Python 3.11+ com type hints estritos (`mypy --strict`).
- **Lógica de Xadrez & PGN:** `python-chess`.
- **Engine de Análise:** Stockfish via protocolo UCI (binário local instalado pelo usuário, não vendorizado).
- **Persistência Local:** SQLite (para cache de avaliações de posições e histórico de partidas).
- **Interface CLI:** `typer` e `rich`.
- **Qualidade & Testes:** `pytest`, `ruff` (linter/formatter) e `mypy` (type checker).

---

## 3. Critério de Classificação de Lances

Em vez de cortes fixos de centipawns (onde uma perda de 150cp em posição com -800cp seria tratada com o mesmo peso de uma em posição equilibrada), o projeto adota o modelo de **Win Probability** (probabilidade de vitória) utilizado pelo Lichess.

A avaliação bruta em centipawns ($cp$) na perspectiva das Brancas é convertida em probabilidade de vitória ($W$) de 0.0% a 100.0% através de uma função logística:

$$W(cp) = \frac{100}{1 + e^{-0.00368208 \cdot cp}}$$

- **Normalização de Perspectiva:** A avaliação é sempre normalizada para a perspectiva do jogador ativo antes do cálculo da probabilidade.
- **Posições de Mate:** Mate a favor ($+M$) resulta em 100.0% de probabilidade de vitória; mate contra ($-M$) resulta em 0.0%.
- **Perda de Chance de Vitória ($\Delta\text{Win\%}$):**
  $$\Delta\text{Win\%} = W_{\text{antes}} - W_{\text{depois}}$$

### Faixas de Classificação:

| Categoria | Perda de Chance ($\Delta\text{Win\%}$) |
|---|---|
| **BEST** | $\Delta W \le 0.0\%$ |
| **EXCELLENT** | $0.0\% < \Delta W \le 2.0\%$ |
| **GOOD** | $2.0\% < \Delta W \le 5.0\%$ |
| **INACCURACY** (Imprecisão) | $5.0\% < \Delta W \le 10.0\%$ |
| **MISTAKE** (Erro) | $10.0\% < \Delta W \le 20.0\%$ |
| **BLUNDER** | $\Delta W > 20.0\%$ |

---

## 4. Estrutura de Diretórios

```
chess-analyzer/
├── GEMINI.md                    # Constituição do agente, regras e orquestração
├── pyproject.toml               # Configuração do pacote, ferramentas e dependências
├── README.md                    # Documentação do projeto
├── src/chess_analyzer/
│   ├── __init__.py              # [Implementado]
│   ├── classify.py              # [Implementado] Classificação por Win Probability (TDD)
│   ├── engine.py                # [Implementado] Wrapper do Stockfish via UCI
│   ├── pgn_import.py            # [Implementado] Parsing iterativo e streaming de PGN
│   ├── db.py                    # [Implementado] Persistência local SQLite, idempotência e cache FEN
│   ├── stats.py                 # [Planejado] Agregação de métricas e padrões de erro
│   ├── puzzles.py               # [Fase 2 - Planejado] Integração com dataset de puzzles Lichess
│   └── cli.py                   # [Planejado] Interface de linha de comando (Typer)
├── tests/
│   ├── __init__.py              # [Implementado]
│   ├── test_classify.py         # [Implementado] Testes da lógica de conversão e faixas
│   ├── test_engine.py           # [Implementado] Testes unitários e integração Stockfish
│   ├── test_pgn_import.py       # [Implementado] Testes de parsing robusto de PGN
│   ├── test_db.py               # [Implementado] Testes de persistência, constraints e integridade
│   ├── test_stats.py            # [Planejado] Testes de agregação estatística
│   └── fixtures/                # [Implementado] PGNs reais e sintéticos de teste
└── data/                        # [Gitignored] Armazenamento de SQLite e PGNs locais
```

---

## 5. Roadmap

O desenvolvimento é estritamente sequencial. Fases posteriores só são iniciadas quando as fases anteriores estiverem sólidas, estáveis e testadas.

1. **Fase 1 — MVP (Foco Atual):**
   - Importação e normalização de PGNs locais (`pgn_import.py`).
   - Persistência e deduplicação local em SQLite com cache FEN (`db.py`).
   - Avaliação de posições com Stockfish (`engine.py`).
   - Classificação de lances por $\Delta\text{Win\%}$ (`classify.py`).
   - Agregação estatística por fase da partida, cor e abertura ECO (`stats.py`).
   - CLI com saídas em tabela e JSON (`cli.py`).
2. **Fase 2 — Treino Personalizado:**
   - Indexação do dataset público de puzzles do Lichess (`puzzles.py`).
   - Cruzamento dos pontos fracos identificados com temas táticos correspondentes.
   - Geração de cadernos de exercícios (FENs + soluções) direcionados.
3. **Fase 3 — Extensões (Stretch Goals):**
   - Servidor MCP (Model Context Protocol) expondo ferramentas de análise para agentes de IA.
   - Módulo de coaching explicativo com LLM (local via `llama.cpp` ou API).
   - Dashboard web (apenas se justificado).

---

## 6. Requisitos e Execução de Testes

### Requisitos

- **Python 3.11+**
- **Stockfish:** Binário local instalado no sistema (não empacotado no repositório). O wrapper espera o caminho para o binário configurado no ambiente.

### Configuração do Ambiente

```bash
# Criar e ativar o ambiente virtual
python3 -m venv .venv
source .venv/bin/activate

# Instalar dependências em modo editável com ferramentas de desenvolvimento
pip install -e ".[dev]"
```

### Execução dos Testes e Checagens de Qualidade

> *Nota: Como a CLI (`cli.py`) ainda não foi implementada, não há comandos de linha de comando para uso final no momento. A execução atual é focada nos testes automatizados e linters.*

```bash
# Executar a suíte de testes completa
pytest

# Executar linter e formatação
ruff check .
ruff format --check .

# Executar verificação estática de tipos
mypy src tests
```

---

## 7. Licença

Não definida formalmente no repositório.
