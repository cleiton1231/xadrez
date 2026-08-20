# AGENT.md — Chess Performance Analyzer

Documento raiz de orquestração para o Antigravity. Define escopo, arquitetura,
regras de execução e quando cada skill deve ser invocada. Isso não é um
README de usuário — é a constituição do agente para este projeto.

---

## 1. Objetivo do projeto

Ferramenta local que analisa o histórico de partidas de xadrez (PGN) do
usuário com Stockfish, classifica erros por lance, agrega padrões ao longo
do tempo (fase do jogo, cor, estrutura de peão, abertura) e gera treino
personalizado a partir do dataset público de puzzles do Lichess, priorizando
os temas onde o usuário mais erra.

Não é um GUI de tabuleiro genérico. O valor está na camada de análise
agregada, não na visualização de uma partida isolada.

---

## 2. Fases

### Fase 1 — MVP (foco atual)
- Importar PGN (arquivo local ou export do Lichess/Chess.com).
- Rodar Stockfish em cada posição do jogo.
- Classificar cada lance (ver seção 6 — critério de classificação).
- Agregar estatísticas por fase do jogo, cor, e abertura (ECO code).
- CLI funcional, output em tabela/JSON.

### Fase 2 — Treino direcionado
- Baixar/indexar dataset público de puzzles do Lichess.
- Cruzar temas fracos do usuário (Fase 1) com puzzles do tema correspondente.
- Gerar sessão de treino (lista de FENs + solução) a partir disso.

### Fase 3 — Stretch (só entra se Fase 1 e 2 estiverem sólidas e testadas)
- Servidor MCP expondo `analyze_pgn`, `get_weak_themes`,
  `generate_training_set` como tools — uso dentro de agentes (Claude, etc).
- Camada de coaching com LLM explicando o porquê de um lance ser erro
  (local via llama.cpp, ou API).
- Dashboard web (só se justificar — CLI/local resolve o MVP inteiro).

Não pular fase. Fase 3 sem Fase 1 e 2 testadas e estáveis é dívida técnica
disfarçada de feature.

---

## 3. Stack técnica

- Python 3.11+, type hints obrigatórios.
- `python-chess` para lógica de tabuleiro/PGN.
- Stockfish via UCI (binário local, não vendorizar).
- SQLite para persistência local (partidas + avaliações já processadas —
  reprocessar tudo a cada run é desperdício).
- CLI com Typer.
- Testes com `pytest`.
- Lint/type-check: `ruff` + `mypy`.
- Fase 3: FastMCP (Python) se for MCP server; FastAPI só se dashboard web
  for justificado.

---

## 4. Estrutura de diretórios

```
chess-analyzer/
├── AGENT.md
├── pyproject.toml
├── src/chess_analyzer/
│   ├── pgn_import.py      # parsing e normalização de PGN
│   ├── engine.py           # wrapper do Stockfish (UCI)
│   ├── classify.py         # classificação de lance (core — TDD obrigatório)
│   ├── stats.py             # agregação estatística
│   ├── puzzles.py           # Fase 2 — dataset Lichess
│   └── cli.py
├── tests/
│   ├── test_classify.py
│   ├── test_stats.py
│   └── fixtures/            # PGNs de teste, posições conhecidas
├── data/                     # gitignored — partidas reais do usuário, .db local
└── docs/
```

---

## 5. Critério de classificação de lance (importante — não trivializar)

Não usar corte fixo de centipawns igual em qualquer posição. Uma queda de
150cp numa posição já perdida (-800) é irrelevante; a mesma queda numa
posição equilibrada é um erro grave. O Lichess resolve isso convertendo
eval em **win probability** (função logística sobre centipawns) e
classificando pela queda de win% — replicar essa lógica, não usar cutoff
cru de centipawns. Se implementar diferente, documentar a decisão e o
porquê explicitamente no código, não silenciosamente.

Isso é lógica core → passa por TDD, ver seção 6.

---

## 6. Orquestração de skills — quando usar cada uma

| Skill | Quando usar neste projeto |
|---|---|
| `concise-planning` | Obrigatório antes de qualquer mudança que toque 2+ arquivos ou for multi-etapa (ex: montar o pipeline import→eval→classify→stats inteiro). Não começar a codar direto. |
| `test-driven-development` | Obrigatório para toda lógica core: `classify.py` e `stats.py`. Red-green-refactor sem pular etapa. GUI/CLI de output não precisa do mesmo rigor. |
| `dependency-audit` | Obrigatório antes de adicionar qualquer lib nova ao `pyproject.toml`. Projeto roda local com dados pessoais (suas partidas) — não é opcional. |
| `systematic-debugging` | Obrigatório para qualquer bug. Proibido "tentar corrigir" sem identificar causa raiz primeiro. Isso vale principalmente pra bugs de sincronização com o processo do Stockfish (UCI é assíncrono e propenso a race condition mal tratada). |
| `verification-before-attestation` | Regra permanente, não específica de uma etapa. Nunca reportar algo como "funcionando", "testado" ou "pronto" sem rodar de fato e mostrar o output real. Dado que você já achou falhas graves no Antigravity antes, essa é a skill que mais importa aqui — trate como inegociável, não como sugestão. |
| `context-window-management` | Só entra na Fase 3 se envolver chamadas LLM repetidas (coaching). Não relevante agora. |
| `local-llm-serving` | Só entra na Fase 3, se decidir rodar o coach via `llama.cpp` local em vez de API paga. |
| `pydantic-ai` | Só entra na Fase 3 se o coach precisar de saída estruturada (ex: JSON com explicação + lance alternativo). Não usar antes disso existir. |
| `mcp-builder` | Candidato natural pra Fase 3 — expor o analisador como MCP server. Não iniciar antes do core (Fase 1) estar testado; um MCP server em cima de lógica não validada só espalha o bug. |
| `threat-modeling` / `top-web-vulnerabilities` | Não bloqueante. Só entram se/quando existir superfície web real (dashboard exposto em rede). MVP é local, sem rede. |

Nenhuma skill nova precisa ser adicionada ao conjunto atual — a lista que
você já tem cobre o projeto inteiro, inclusive as fases futuras. O ponto
não é ter mais skills, é usar `verification-before-attestation` e
`test-driven-development` de forma consistente, já que a queixa original
foi confiabilidade do agente, não falta de ferramenta.

---

## 7. Regras não negociáveis

1. Nunca declarar tarefa concluída sem execução real (`pytest` rodando com
   output visível, CLI executado de fato, não assumido).
2. Nunca instalar dependência sem passar por `dependency-audit`.
3. Nunca "consertar" bug sem `systematic-debugging` documentando a causa
   raiz antes do patch.
4. Todo módulo em `classify.py` e `stats.py` tem teste escrito antes da
   implementação (TDD real, não teste escrito depois pra "cobrir").
5. Mudança multi-arquivo sempre passa por `concise-planning` antes de
   qualquer edição.
6. Dados pessoais do usuário (`data/`) nunca vão pro git — `.gitignore`
   configurado desde o primeiro commit, não depois.

---

## 8. Definição de pronto (DoD) por fase

**Fase 1:** roda `chess-analyzer import partidas.pgn` e
`chess-analyzer stats`, retorna números reais de um PGN de teste conhecido,
com testes unitários cobrindo classificação e agregação passando via
`pytest`, sem mock do Stockfish nos testes de integração (testes de
classificação podem mockar eval, mas pelo menos um teste de ponta a ponta
roda o Stockfish de verdade).

**Fase 2:** dado o output da Fase 1, gera lista de puzzles reais (FEN +
solução) do dataset do Lichess batendo com o tema mais fraco identificado.

**Fase 3:** definido quando chegar lá — não especular agora.

---

## 9. Convenções de código

- Nomes de função/variável/classe em inglês (padrão da comunidade Python).
- Docstrings e README em português.
- Commits pequenos, conventional commits, mensagem em inglês.
- `ruff` + `mypy` limpos antes de qualquer commit — não é sugestão.
