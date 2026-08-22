# Chess Performance Analyzer

Ferramenta local para análise do histórico de partidas de xadrez (PGN) com Stockfish, identificando padrões de erro por lance, fase do jogo, abertura e cor para direcionar o treinamento e evolução pessoal no xadrez.

---

## 1. Status Atual do Projeto

- **Fase 1 (MVP) — Concluída:**
  - `src/chess_analyzer/pgn_import.py`: Parsing iterativo (streaming via `Iterator[ParsedGame]`) de arquivos PGN com suporte ao Lichess e Chess.com, canonicidade de lances SAN e robustez contra dados corrompidos ou malformados.
  - `src/chess_analyzer/engine.py`: Wrapper UCI para o Stockfish com gerenciamento de ciclo de vida (`with`), bypass automático para posições de fim de jogo e avaliação síncrona com timeout seguro.
  - `src/chess_analyzer/classify.py`: Classificação precisa de lances baseada na perda de probabilidade de vitória ($\Delta\text{Win\%}$), convertendo centipawns via regressão logística com normalização de perspectiva e tratamento de mate.
  - `src/chess_analyzer/db.py`: Persistência local em SQLite com journal mode WAL, integridade referencial (`PRAGMA foreign_keys = ON`), deduplicação canônica por hash SHA-256, FEN normalizado (4 campos essenciais) e cache de avaliações de posições por profundidade (`depth`).
  - `src/chess_analyzer/analyze.py`: Orquestrador de análise conectando engine, classificação e persistência, com reuso de avaliações do cache e idempotência.
  - `src/chess_analyzer/stats.py`: Agregação estatística de métricas e distribuição de erros por cor, abertura (código ECO) e fase do jogo (Opening, Middlegame, Endgame).
  - `src/chess_analyzer/cli.py`: Interface de linha de comando completa (via Typer e Rich) com os comandos `import`, `analyze` e `stats` (com saídas formatadas em tabela e opção `--json`).
  - Suíte de testes automatizados completa com 80 testes passando (`pytest`).

- **Fase 2 — Treino Direcionado (Em andamento):**
  - Módulo `src/chess_analyzer/puzzles.py` em desenvolvimento para indexação do dataset de puzzles do Lichess.

---

## 2. Roadmap e Fases

O desenvolvimento segue uma progressão estritamente sequencial conforme definido na constituição do projeto (`GEMINI.md`):

1. **Fase 1 — MVP (Concluída):**
   - Importar PGN (arquivo local ou export do Lichess/Chess.com).
   - Executar Stockfish em cada posição do jogo com cache FEN em SQLite.
   - Classificar cada lance via $\Delta\text{Win\%}$ (função logística sobre centipawns).
   - Agregar estatísticas por fase do jogo, cor e abertura (ECO code).
   - CLI funcional com output em tabela (Rich) e formato JSON.

2. **Fase 2 — Treino Direcionado (Em andamento):**
   - Baixar e indexar dataset público de puzzles do Lichess (`puzzles.py`).
   - Cruzar temas onde o usuário mais erra (Fase 1) com puzzles correspondentes.
   - Gerar sessões de treino personalizadas (lista de FENs + soluções).

3. **Fase 3 — Extensões (Stretch Goals — após Fase 1 e 2 testadas):**
   - Servidor MCP (Model Context Protocol) expondo tools (`analyze_pgn`, `get_weak_themes`, `generate_training_set`) para agentes de IA.
   - Camada de coaching explicativo com LLM (local via `llama.cpp` ou API).
   - Dashboard web (apenas se justificado).

---

## 3. Stack Técnica

- **Linguagem & Tipagem:** Python 3.11+ com type hints estritos (`mypy --strict`).
- **Lógica de Xadrez & PGN:** `python-chess`.
- **Engine de Análise:** Stockfish via protocolo UCI (binário local instalado pelo usuário, não vendorizado).
- **Persistência Local:** SQLite (cache de posições e histórico de partidas).
- **Interface CLI:** `typer` e `rich`.
- **Qualidade & Testes:** `pytest`, `ruff` (linter/formatter) e `mypy` (type checker).

---

## 4. Requisitos e Instalação

### Requisitos Prévios

1. **Python 3.11+**
2. **Stockfish:** Binário local instalado no sistema (não empacotado no repositório).
   - **Linux (Debian/Ubuntu):** `sudo apt install stockfish`
   - **Linux (Arch):** `sudo pacman -S stockfish`
   - **macOS:** `brew install stockfish`
   - **Manual:** Baixe o executável oficial em [stockfishchess.org](https://stockfishchess.org/download/) e configure o caminho na execução via flag `--engine-path` ou posicione o binário em `.venv/bin/stockfish`.

### Instalação

```bash
# 1. Clonar o repositório
git clone <url-do-repositorio>
cd chess-analyzer

# 2. Criar e ativar o ambiente virtual
python3 -m venv .venv
source .venv/bin/activate

# 3. Instalar o pacote em modo editável (com dependências de desenvolvimento)
pip install -e ".[dev]"
```

As dependências principais registradas no `pyproject.toml` são:
- `chess>=1.10.0`
- `typer>=0.12.0`
- `rich>=13.7.0`

> *Nota:* A dependência `zstandard` (necessária para descompressão em streaming do dataset de puzzles do Lichess) é exclusiva da Fase 2 e será adicionada ao `pyproject.toml` na conclusão da etapa de indexação.

---

## 5. Como Usar (CLI)

O executável de linha de comando `chess-analyzer` disponibiliza os seguintes comandos:

### 1. Importar Partidas PGN (`import`)

Importa partidas de um arquivo PGN local para o banco SQLite local (`data/chess_analyzer.db` por padrão). O processo é idempotente (jogos duplicados são ignorados automaticamente via hash SHA-256).

```bash
chess-analyzer import caminho/para/partidas.pgn
```

Opções:
- `--db` / `-d`: Caminho personalizado para o banco SQLite (padrão: `data/chess_analyzer.db`).

### 2. Analisar Posições com Stockfish (`analyze`)

Executa o Stockfish em todas as posições pendentes de análise no banco de dados, classificando os lances e populando o cache FEN.

```bash
chess-analyzer analyze
```

Opções:
- `--db` / `-d`: Caminho do banco SQLite (padrão: `data/chess_analyzer.db`).
- `--engine-path` / `-e`: Caminho para o binário do Stockfish (padrão: `.venv/bin/stockfish`).
- `--depth`: Profundidade de busca do Stockfish por lance (padrão: `12`).

### 3. Visualizar Estatísticas Agregadas (`stats`)

Gera relatórios de desempenho e agregações de erros para um determinado jogador.

```bash
# Visualizar todas as agregações (cor, abertura e fase do jogo) em tabelas formatadas:
chess-analyzer stats "NomeDoJogador"

# Filtrar por dimensão específica ('color', 'opening', 'phase' ou 'all'):
chess-analyzer stats "NomeDoJogador" --by color
chess-analyzer stats "NomeDoJogador" --by opening
chess-analyzer stats "NomeDoJogador" --by phase

# Exportar os dados agregados em formato JSON:
chess-analyzer stats "NomeDoJogador" --json
```

Opções:
- `--db` / `-d`: Caminho do banco SQLite (padrão: `data/chess_analyzer.db`).
- `--by`: Dimensão de agregação (`all`, `color`, `opening`, `phase`).
- `--json`: Exporta as estatísticas estruturadas em JSON.

> **Nota sobre a Fase 2:** Comandos de indexação de puzzles (`chess-analyzer puzzles index` / `puzzles status`) estão atualmente *em desenvolvimento* e serão disponibilizados com a conclusão da Fase 2.

---

## 6. Critério de Classificação de Lances

Em vez de cortes fixos de centipawns (onde uma perda de 150cp em posição com -800cp seria tratada com o mesmo peso de uma em posição equilibrada), o projeto adota o modelo de **Win Probability** (probabilidade de vitória) utilizado pelo Lichess.

A avaliação bruta em centipawns ($cp$) na perspectiva das Brancas é convertida em probabilidade de vitória ($W$) de 0.0% a 100.0% através de uma função logística:

$$W(cp) = \frac{100}{1 + e^{-0.00368208 \cdot cp}}$$

- **Normalização de Perspectiva:** A avaliação é normalizada para a perspectiva do jogador ativo antes do cálculo da probabilidade.
- **Posições de Mate:** Mate a favor ($+M$) resulta em 100.0% de probabilidade de vitória; mate contra ($-M$) resulta em 0.0%.
- **Perda de Chance de Vitória ($\Delta\text{Win\%}$):**
  $$\Delta\text{Win\%} = W_{\text{antes}} - W_{\text{depois}}$$

### Faixas de Classificação

| Categoria | Perda de Chance ($\Delta\text{Win\%}$) |
|---|---|
| **BEST** | $\Delta W \le 0.0\%$ |
| **EXCELLENT** | $0.0\% < \Delta W \le 2.0\%$ |
| **GOOD** | $2.0\% < \Delta W \le 5.0\%$ |
| **INACCURACY** (Imprecisão) | $5.0\% < \Delta W \le 10.0\%$ |
| **MISTAKE** (Erro) | $10.0\% < \Delta W \le 20.0\%$ |
| **BLUNDER** | $\Delta W > 20.0\%$ |

---

## 7. Estrutura de Diretórios

```
chess-analyzer/
├── GEMINI.md                    # Constituição do agente, regras e orquestração
├── pyproject.toml               # Configuração do pacote, ferramentas e dependências
├── README.md                    # Documentação do projeto
├── src/chess_analyzer/
│   ├── __init__.py              # [Implementado]
│   ├── pgn_import.py            # [Implementado] Parsing iterativo e streaming de PGN
│   ├── engine.py                # [Implementado] Wrapper do Stockfish via UCI
│   ├── classify.py              # [Implementado] Classificação por Win Probability (TDD)
│   ├── db.py                    # [Implementado] Persistência local SQLite, idempotência e cache FEN
│   ├── analyze.py               # [Implementado] Orquestrador de análise conectando engine, classify e db
│   ├── stats.py                 # [Implementado] Agregação de métricas e padrões de erro
│   ├── puzzles.py               # [Fase 2 - Em desenvolvimento] Integração com dataset de puzzles Lichess
│   └── cli.py                   # [Implementado] Interface de linha de comando (Typer e Rich)
├── tests/
│   ├── __init__.py              # [Implementado]
│   ├── test_classify.py         # [Implementado] Testes da lógica de conversão e faixas
│   ├── test_engine.py           # [Implementado] Testes unitários e integração Stockfish
│   ├── test_pgn_import.py       # [Implementado] Testes de parsing robusto de PGN
│   ├── test_db.py               # [Implementado] Testes de persistência, constraints e integridade
│   ├── test_analyze.py          # [Implementado] Testes do pipeline orquestrador de análise
│   ├── test_stats.py            # [Implementado] Testes de agregação estatística
│   ├── test_cli.py              # [Implementado] Testes de integração da interface de linha de comando
│   └── fixtures/                # [Implementado] PGNs reais e sintéticos de teste
├── docs/                        # Relatórios e documentos técnicos do projeto
└── data/                        # [Gitignored] Armazenamento de SQLite e PGNs locais
```

---

## 8. Desenvolvimento e Testes

### Executar a Suíte de Testes

```bash
# Execução padrão via pytest
pytest

# Execução com verbosidade
pytest -v
```

### Checagens de Qualidade de Código

```bash
# Executar linter (Ruff)
ruff check .

# Verificar formatação
ruff format --check .

# Executar verificação estática de tipos (Mypy)
mypy src tests
```
