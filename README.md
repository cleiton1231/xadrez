# Chess Performance Analyzer

Ferramenta local para análise do histórico de partidas de xadrez (PGN) com Stockfish, identificando padrões de erro por lance, fase do jogo, abertura e cor para direcionar o treinamento e evolução pessoal no xadrez.

---

## 1. Status Atual do Projeto

> **Fase atual:** Fase 1 (MVP) — Em andamento.

O projeto segue desenvolvimento orientado a testes (TDD) e rigor metodológico. O estado real dos componentes é:

- **Implementado e Testado:**
  - `src/chess_analyzer/classify.py`: Classificação precisa de lances baseada na perda de probabilidade de vitória ($\Delta\text{Win\%}$), convertendo centipawns via regressão logística com normalização de perspectiva e tratamento de mate.
  - `src/chess_analyzer/engine.py`: Wrapper UCI para o Stockfish com gerenciamento de ciclo de vida (`with`), bypass automático para posições de fim de jogo e avaliação síncrona com timeout seguro.
  - Suíte de testes automatizados (`tests/test_classify.py` e `tests/test_engine.py`) cobrindo a lógica existente (29 testes passando).
- **Em Planejamento / Próximos Passos (Fase 1):**
  - `src/chess_analyzer/pgn_import.py`: Parsing e normalização de partidas PGN (Etapa 4).
  - `src/chess_analyzer/stats.py`: Agregação estatística de erros (por fase, abertura ECO e cor).
  - `src/chess_analyzer/cli.py`: Interface de linha de comando (CLI via Typer/Rich) para processamento de arquivos e visualização de tabelas/JSON.
- **Roadmap Futuro (Fases 2 e 3):**
  - Nada implementado ainda (planejado no `AGENT.md`).

---

## 2. Stack Técnica

- **Linguagem & Tipagem:** Python 3.11+ com type hints estritos (`mypy --strict`).
- **Lógica de Xadrez & PGN:** `python-chess`.
- **Engine de Análise:** Stockfish via protocolo UCI (binário local instalado pelo usuário, não vendorizado).
- **Persistência Local:** SQLite (para cache de avaliações e histórico de partidas).
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
├── AGENT.md                     # Constituição do agente, regras e orquestração
├── pyproject.toml               # Configuração do pacote, ferramentas e dependências
├── README.md                    # Documentação do projeto
├── src/chess_analyzer/
│   ├── __init__.py              # [Implementado]
│   ├── classify.py              # [Implementado] Classificação por Win Probability (TDD)
│   ├── engine.py                # [Implementado] Wrapper do Stockfish via UCI
│   ├── pgn_import.py            # [Em planejamento] Parsing e normalização de PGN
│   ├── stats.py                 # [Planejado] Agregação de métricas e padrões de erro
│   ├── puzzles.py               # [Fase 2 - Planejado] Integração com dataset de puzzles Lichess
│   └── cli.py                   # [Planejado] Interface de linha de comando (Typer)
├── tests/
│   ├── __init__.py              # [Implementado]
│   ├── test_classify.py         # [Implementado] Testes da lógica de conversão e faixas
│   ├── test_engine.py           # [Implementado] Testes unitários e integração Stockfish
│   ├── test_stats.py            # [Planejado] Testes de agregação estatística
│   └── fixtures/                # [Planejado] PGNs de teste e posições conhecidas
└── data/                        # [Gitignored] Armazenamento de SQLite e PGNs locais
```

---

## 5. Roadmap

O desenvolvimento é estritamente sequencial. Fases posteriores só são iniciadas quando as fases anteriores estiverem sólidas, estáveis e testadas.

1. **Fase 1 — MVP (Foco Atual):**
   - Importação e normalização de PGNs locais.
   - Avaliação de posições com Stockfish (benchmark medido: ~6–50ms por posição de meio-jogo com `depth=12`).
   - Classificação de lances por $\Delta\text{Win\%}$.
   - Agregação estatística por fase da partida, cor e abertura (ECO).
   - CLI com saídas em tabela e JSON.
2. **Fase 2 — Treino Personalizado:**
   - Indexação do dataset público de puzzles do Lichess.
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
# Executar a suíte de testes
pytest

# Executar linter e formatação
ruff check .

# Executar verificação estática de tipos
mypy src tests
```

---

## 7. Licença

Não definida formalmente no repositório.
