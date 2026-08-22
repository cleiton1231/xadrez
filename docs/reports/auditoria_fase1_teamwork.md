# Relatório Formal Consolidado de Auditoria de Código — Fase 1 (Etapas 1 a 8)
**Projeto:** Chess Performance Analyzer  
**Data da Auditoria:** 21 de Agosto de 2026  
**Metodologia:** Auditoria Multi-Agente Teamwork (Explorer 1, Explorer 2, Worker 1, Worker 2)  
**Status de Integridade:** Strict Read-Only mantido em `src/` e `tests/` (100% inalterados)  
**Documento Alvo:** `docs/reports/auditoria_fase1_teamwork.md`  

---

## Declaração Explícita de Cobertura Integral dos 7 Eixos

A equipe de auditoria multi-agente atesta que este relatório consolida a investigação profunda, empírica e cruzada de **todos os 7 eixos estruturais** determinados pela constituição do projeto (`GEMINI.md`) e pelo pedido original (`ORIGINAL_REQUEST.md`), sem omissão de qualquer subsistema:
1. **Eixo 1 — Convenção de Perspectiva Consistente** (Auditoria de leitura/escrita de `eval_cp`, `eval_mate`, `PositionEvaluation`, `delta_win_prob` e agregação).
2. **Eixo 2 — Transações e Concorrência SQLite** (Auditoria exaustiva dos 11 pontos de conexão, PRAGMAs, Two-Phase Execution, overhead de `init_db` e normalização FEN).
3. **Eixo 3 — Tratamento de Erro Consistente** (Convenção de Exit codes 0, 1 e 2, clareza das mensagens, ciclo de vida do Stockfish e vazamento de tracebacks).
4. **Eixo 4 — Validade e Rigor dos Testes (TDD Anti-Falso-Positivo)** (Varredura dos 80 testes, prova de Win% vs centipawns, média ponderada, asserções e edge cases).
5. **Eixo 5 — Aderência do GEMINI.md à Realidade do Código** (Auditoria Seções 1 a 10 contra implementação real).
6. **Eixo 6 — Dependências e Superfície de Risco** (Cruzamento de `pyproject.toml` vs imports de `src/` e `tests/`, isolamento no `.gitignore` e software offline).
7. **Eixo 7 — Hipóteses de Performance** (Mapeamento sistemático de 6 hipóteses arquiteturais entre mecanismos testados e métricas pendentes).

---

## 1. Sumário Executivo

A auditoria de código da Fase 1 (Etapas 1 a 8) do **Chess Performance Analyzer** avaliou a integridade arquitetural, a correção matemática, a segurança de persistência e a conformidade da suíte de testes do projeto.

### Síntese dos Resultados
- **Bugs Bloqueantes Detectados:** **0 (Zero)**.
- **Achados Não-Bloqueantes:** **5** (ACH-01: FEN customizado no lance 1; ACH-02: overhead de conexões efêmeras por `init_db` [risco já identificado e aceito na Etapa 6/7]; ACH-05: divergência de normalização de FEN en passant; ACH-06: assimetria de traceback em erro no CLI `stats`; ACH-07: ausência de lance com erro das Pretas em teste de integração de análise).
- **Observações e Code Smells:** **5** (ACH-03: nomenclatura semântica em dataclass `PositionEvaluation`; ACH-04: transições de mate com Win% idêntico; ACH-08: asserção relaxada em teste E2E do CLI; ACH-09: gaps de testes unitários para limites extremos de centipawns; ACH-10: ausência de teste de `evaluate()` fora do gerenciador de contexto).
- **Suíte de Testes Automatizados:** **80 testes aprovados** (100% de sucesso em `pytest -v`).
- **Verificação de Tipagem Estrita:** **0 erros** em 17 arquivos inspecionados (`mypy src tests` com `disallow_untyped_defs = true`).
- **Linter de Regras:** **0 violações** reportadas por `ruff check .`.
- **Definição de Pronto (DoD da Fase 1):** **100% Cumprida**, com importação de PGN, avaliação real via Stockfish (sem mocks no teste E2E), classificação por Win Probability e agregação estatística tabular e JSON operacionais no CLI.

---

## 2. Auditoria Detalhada dos 7 Eixos Estruturais

```
                                ARQUITETURA GERAL DO PIPELINE — FASE 1
 ┌────────────────┐      ┌─────────────────────────┐      ┌───────────────────────────┐
 │   PGN Import   │ ───> │     StockfishEngine     │ ───> │       classify.py         │
 │ (pgn_import.py)│      │       (engine.py)       │      │   (Win Probability ΔW%)   │
 └────────────────┘      │  [White Absolute Eval]  │      │  [Player Perspective Eval]│
                         └─────────────────────────┘      └───────────────────────────┘
                                      │                                 │
                                      ▼                                 ▼
                         ┌─────────────────────────┐      ┌───────────────────────────┐
                         │   evaluations (Cache)   │      │       moves / games       │
                         │    [White Absolute]     │      │   [White Eval + Player ΔW]│
                         └─────────────────────────┘      └───────────────────────────┘
                                                                        │
                                                                        ▼
                                                          ┌───────────────────────────┐
                                                          │         stats.py          │
                                                          │   (Agregação Ponderada)   │
                                                          └───────────────────────────┘
```

---

### Eixo 1: Convenção de Perspectiva Consistente

O Eixo 1 auditou o fluxo de sinais e pontos de vista em todo o pipeline de avaliação, classificação e persistência, garantindo que não ocorra inversão de vantagem entre as cores branca e preta.

#### 1. Normalização Absoluta no StockfishEngine
Em `src/chess_analyzer/engine.py:59-96`, o wrapper UCI normaliza rigorosamente todas as avaliações para a **perspectiva absoluta das Brancas**:
```python
# Trecho de src/chess_analyzer/engine.py:84-96
limit = chess.engine.Limit(depth=self.depth, time=self.move_time_limit)
info = self._engine.analyse(board, limit)

# Normalização estrita para a perspectiva das Brancas
pov_score = info["score"].white()

if pov_score.is_mate():
    mate_in = pov_score.mate()
    return PositionEvaluation(white_cp=None, mate_for_white=mate_in)

cp = pov_score.score()
return PositionEvaluation(white_cp=cp, mate_for_white=None)
```
- **Constatação:** Valores positivos (`+cp` ou `+M`) representam inequivocamente vantagem para as Brancas; valores negativos (`-cp` ou `-M`) representam vantagem para as Pretas.

#### 2. Conversão para a Perspectiva do Jogador Ativo
Em `src/chess_analyzer/classify.py:58-90` e `classify.py:119-140`, a função `classify_move` converte explicitamente as avaliações antes e depois do lance para o ponto de vista do jogador que executou o lance:
```python
# Trecho de src/chess_analyzer/classify.py:77-90
def to_player_perspective(eval_pos: PositionEvaluation, color: chess.Color) -> PositionEvaluation:
    if color == chess.WHITE:
        return eval_pos

    black_cp = -eval_pos.white_cp if eval_pos.white_cp is not None else None
    black_mate = -eval_pos.mate_for_white if eval_pos.mate_for_white is not None else None

    return PositionEvaluation(white_cp=black_cp, mate_for_white=black_mate)
```
- **Constatação Matemática:** A perda de chance de vitória ($\Delta\text{Win}\% = \text{prob\_before} - \text{prob\_after}$) é calculada sobre a probabilidade do jogador ativo. Lances que pioram a situação do jogador produzem $\Delta W > 0$ (Inaccuracy, Mistake, Blunder), enquanto lances que mantêm ou melhoram a posição produzem $\Delta W \le 0$ (Best, Excellent, Good).

#### 3. Persistência Coerente no Banco de Dados
A separação de responsabilidades no schema (`src/chess_analyzer/db.py:72-95`) e no orquestrador (`src/chess_analyzer/analyze.py:128-144`) preserva:
- `evaluations.eval_cp` / `eval_mate`: **Perspectiva absoluta das Brancas** (cache reutilizável independente de quem jogou).
- `moves.eval_cp` / `eval_mate`: **Perspectiva absoluta das Brancas** (estado estático da posição no tabuleiro).
- `moves.win_prob_before`, `moves.win_prob_after`, `moves.delta_win_prob`: **Perspectiva do jogador ativo**.

#### 4. Agregação Estatística
Em `src/chess_analyzer/stats.py:126-144`, as consultas SQL filtram os lances de cada jogador pela paridade do ply (`ply % 2 != 0` para Brancas e `ply % 2 == 0` para Pretas), agregando métricas que já refletem o impacto sob a ótica daquele jogador específico.

#### 5. Apontamentos do Eixo 1
- **ACH-01 (Não-bloqueante — `src/chess_analyzer/analyze.py:106`):** O `eval_before` do lance 1 é fixado como `chess.STARTING_FEN`. Em partidas iniciadas com FEN armado customizado (tags `[SetUp "1"]` e `[FEN "..."]`), o lance 1 receberá a avaliação da posição inicial padrão.
- **ACH-03 (Observação — `src/chess_analyzer/classify.py:89`):** A função `to_player_perspective` instancia `PositionEvaluation(white_cp=black_cp, mate_for_white=black_mate)`. Embora a matemática funcione perfeitamente, o campo nomeado `white_cp` passa a conter centipawns das Pretas, constituindo um desalinhamento semântico.
- **ACH-04 (Observação — `src/chess_analyzer/classify.py:97-98`):** Transições entre distâncias de mate (ex: $+M1$ para $+M8$) mantêm Win% em 100.0%, sendo classificadas como `BEST` ($\Delta W = 0.0\%$). Esta é uma limitação de modelo aceita no escopo da Fase 1.

---

### Eixo 2: Transações e Concorrência SQLite

O Eixo 2 inspecionou todos os pontos de abertura, uso e fechamento de conexões SQLite, o comportamento transacional sob falha e a consistência das chaves de cache.

#### 1. Ciclo de Vida e Fechamento Universal de Conexões
Foram auditados todos os **11 pontos de abertura de conexão SQLite** no código de produção em `src/`:
1. `src/chess_analyzer/db.py:48` (`init_db`) -> `try ... finally: conn.close()`
2. `src/chess_analyzer/db.py:119` (`save_games`) -> `try ... except Exception: ROLLBACK ... finally: conn.close()`
3. `src/chess_analyzer/db.py:211` (`save_evaluation`) -> `try: with conn: ... finally: conn.close()`
4. `src/chess_analyzer/db.py:236` (`get_evaluation`) -> `try ... finally: conn.close()`
5. `src/chess_analyzer/analyze.py:64` (`analyze_games` busca pendentes) -> `try ... finally: conn.close()`
6. `src/chess_analyzer/analyze.py:84` (`analyze_games` leitura lances) -> `try ... finally: conn.close()`
7. `src/chess_analyzer/analyze.py:147` (`analyze_games` write-back) -> `try: with conn: ... finally: conn.close()`
8. `src/chess_analyzer/cli.py:199` (`stats_cmd` pendentes) -> `try ... finally: conn.close()`
9. `src/chess_analyzer/stats.py:122` (`stats_by_color`) -> `try ... finally: conn.close()`
10. `src/chess_analyzer/stats.py:165` (`stats_by_opening`) -> `try ... finally: conn.close()`
11. `src/chess_analyzer/stats.py:206` (`stats_by_game_phase`) -> `try ... finally: conn.close()`
- **Constatação:** 100% dos pontos em `src/` utilizam `try/finally` explícito, garantindo desalocação imediata de descritores de arquivo e cursores, inclusive em cenários de exceção ou interrupção.

#### 2. Configuração de PRAGMAs e Modo WAL
Em `src/chess_analyzer/db.py:21-27`, toda conexão obtida via `get_connection()` aplica:
- `PRAGMA foreign_keys = ON;` (Reaplicado a cada conexão, garantindo integridade referencial).
- `PRAGMA journal_mode = WAL;` (Ativa Write-Ahead Logging para concorrência de múltiplos leitores simultâneos a um escritor).
- `PRAGMA synchronous = NORMAL;` (Reduz sincronizações de disco desnecessárias sem perda de integridade no modo WAL).

#### 3. Padrão Two-Phase Execution no `analyze_games`
Em `src/chess_analyzer/analyze.py:111-163`, a análise de uma partida opera em duas fases:
- **Fase 1 (Cálculo e Cache FEN):** A engine avalia cada posição; o resultado é gravado individualmente e atomicamente na tabela `evaluations`. Se o processo for interrompido no meio da partida, o trabalho da engine não é perdido.
- **Fase 2 (Write-Back Atômico da Partida):** Os cálculos de todos os lances da partida são persistidos de uma única vez em lote transacional na tabela `moves` (`with conn: conn.executemany(...)`). Lances de uma partida nunca ficam parcialmente categorizados no banco.

#### 4. Apontamentos do Eixo 2
- **ACH-02 (Não-bloqueante — `src/chess_analyzer/analyze.py:21-47`):** Chamada redundante de `init_db(db_path)` dentro de `get_evaluation` e `save_evaluation`. Durante a análise de 1.000 partidas (60.000 lances), ocorrem entre 120.000 e 240.000 aberturas efêmeras de conexões SQLite apenas para checar `PRAGMA user_version;`, gerando overhead de I/O de metadados (*Risco já identificado e aceito conscientemente na Etapa 6/7 como trade-off de idempotência; refatoração recomendada para fases futuras*).
- **ACH-05 (Não-bloqueante — `src/chess_analyzer/db.py:30-33`):** A função `normalize_fen(fen)` executa `" ".join(fen.strip().split()[:4])`. Quando um FEN gerado externamente inclui casa de en passant pseudo-sintática (`e3`), enquanto o `python-chess` emite `-` por ausência de captura legal, a comparação de strings resulta em `False`, gerando cache miss e reavaliação desnecessária pelo Stockfish.

---

### Eixo 3: Tratamento de Erro Consistente

O Eixo 3 validou o comportamento do CLI e dos módulos internos em condições de erro, checando códigos de saída, mensagens e gestão de processos do sistema operacional.

#### 1. Convenção de Exit Codes (0, 1 e 2)
Testes empíricos demonstraram a aderência do CLI aos padrões POSIX/Typer:
- **Exit 0 (Sucesso / Estado Vazio Válido):**
  - Importação bem-sucedida de PGN.
  - Consulta estatística para jogador inexistente: emite aviso informativo em stdout/stderr e encerra com código 0.
- **Exit 2 (Validação Typer/Click de Argumentos e Opções):**
  - Arquivo PGN inexistente (`chess-analyzer import nao_existe.pgn`).
  - Argumento obrigatório ausente (`chess-analyzer stats`).
  - Opção enum inválida (`chess-analyzer stats Player1 --by invalido`).
  - Subcomando inexistente (`chess-analyzer invalid_cmd`).
- **Exit 1 (Erros de Execução em Runtime Tratados):**
  - Arquivo com lixo binário (`chess-analyzer import tests/fixtures/binary.pgn`).
  - Binário do Stockfish inexistente ou sem permissão de execução (`chess-analyzer analyze --engine-path /caminho/invalido`).

#### 2. Separação de Streams e Mensagens
O CLI utiliza instâncias dedicadas de `rich.console.Console`:
- `console = Console()` para stdout (tabelas, JSON, resumos de sucesso).
- `err_console = Console(stderr=True)` para stderr (mensagens de erro formatadas em vermelho, avisos de validação).

#### 3. Gestão do Subprocesso do Stockfish
Em `src/chess_analyzer/engine.py:41-51`, o wrapper utiliza gerenciador de contexto (`__enter__` e `__exit__`). Quando ocorre encerramento normal ou exceção (`KeyboardInterrupt` / SIGINT), o método `__exit__` invoca `self._engine.quit()`, garantindo o encerramento do processo filho e prevenindo processos zumbis/órfãos.

#### 4. Apontamentos do Eixo 3
- **ACH-06 (Não-bloqueante — `src/chess_analyzer/cli.py:196-240`):** Assimetria no tratamento de exceções no comando `stats`. Enquanto `import_cmd` e `analyze_cmd` possuem blocos `try/except` que capturam falhas e emitem mensagens limpas via `err_console.print`, `stats_cmd` não envolve o corpo em `try/except`, fazendo com que erros de banco corrompido ou I/O emitam um traceback bruto do Rich no terminal.

---

### Eixo 4: Validade e Rigor dos Testes (TDD Anti-Falso-Positivo)

O Eixo 4 auditou todos os 80 testes do repositório para garantir que a suíte seja genuinamente discriminatória e imune a implementações ingênuas.

#### 1. Prova Matemática: Win Probability vs Centipawns Cru
O teste `test_proof_of_win_probability_vs_raw_centipawns` em `tests/test_classify.py:118-139` é um exemplo fundamental de teste anti-falso-positivo:
```python
# Trecho de tests/test_classify.py:118-139
def test_proof_of_win_probability_vs_raw_centipawns(self) -> None:
    # Caso 1: Posição desbalanceada ganha (+800 -> +650, queda de 150cp)
    eval_won_before = PositionEvaluation(white_cp=800)
    eval_won_after = PositionEvaluation(white_cp=650)
    res_won = classify_move(eval_won_before, eval_won_after, player=chess.WHITE)

    assert res_won.delta_win_prob == pytest.approx(3.3729, abs=1e-2)
    assert res_won.category == MoveCategory.GOOD

    # Caso 2: Posição equilibrada (0 -> -150, MESMA queda de 150cp)
    eval_equal_before = PositionEvaluation(white_cp=0)
    eval_equal_after = PositionEvaluation(white_cp=-150)
    res_equal = classify_move(eval_equal_before, eval_equal_after, player=chess.WHITE)

    assert res_equal.delta_win_prob == pytest.approx(13.4670, abs=1e-2)
    assert res_equal.category == MoveCategory.MISTAKE
```
- **Constatação:** Uma implementação incorreta baseada em cortes fixos de centipawns classificaria ambos os lances identicamente, falhando obrigatoriamente no teste.

#### 2. Média Ponderada vs Média das Médias
Em `tests/test_stats.py:285-337` (`test_stats_by_color_with_unequal_counts_weighted_average`), a suíte valida que o cálculo de perda média agrega o total de $\Delta W$ ponderado pelo volume de lances em cada categoria, discriminando implementações que calculam média aritmética simples de médias parciais.

#### 3. Testes End-to-End com Stockfish Real
Em conformidade estrita com a DoD da Fase 1, os testes `tests/test_analyze.py:255-288` e `tests/test_cli.py:201-223` invocam o binário real do Stockfish através do pipeline completo (import -> analyze -> stats), validando a integração entre os subsistemas.

#### 4. Apontamentos do Eixo 4
- **ACH-07 (Não-bloqueante — `tests/test_analyze.py:141-145`):** No teste mockado de análise, tanto o lance das Brancas quanto o das Pretas geram melhora de probabilidade ($\Delta W < 0 \to \text{BEST}$). Não há cenário integrado em `test_analyze.py` onde as Pretas cometem um `BLUNDER` ou `MISTAKE`.
- **ACH-08 (Observação — `tests/test_cli.py:222`):** Asserção relaxada `assert data["total_analyzed_moves"] > 0` no teste E2E do CLI. A fixture possui exatamente 2 lances analisados para o jogador testado; validar `== 2` seria mais rigoroso contra contagens duplicadas.
- **ACH-09 (Observação — `tests/test_classify.py:109`):** Ausência de teste para overflow de centipawns e para validação de `mate_for_white=0`.
- **ACH-10 (Observação — `tests/test_engine.py:70`):** Ausência de teste validando o disparo de `RuntimeError` ao invocar `engine.evaluate()` fora do bloco `with`.

---

### Eixo 5: Aderência do GEMINI.md à Realidade do Código

Auditoria cruzada das Seções 1 a 10 do documento raiz `GEMINI.md` contra o código-fonte, configurações e artefatos reais:

| Seção GEMINI.md | Requisito / Diretriz Prevista | Situação no Código Real | Status de Conformidade |
|---|---|---|---|
| **Seção 1: Objetivo** | Análise local PGN, classificação por lance, agregação (fase, cor, estrutura de peões, abertura) e treino de puzzles. | Pipeline de PGN, Stockfish, classificação e agregação (cor, abertura, fase) implementados. Estrutura de peões não implementada no MVP. | **Conforme** (Observação sobre estrutura de peões para fases futuras) |
| **Seção 2: Fases** | Fase 1 (MVP: CLI, import, analyze, stats); Fase 2 (Puzzles Lichess); Fase 3 (MCP/LLM coach). Proibição de pular fases. | `puzzles.py` mantido como stub; zero código vazado de Fase 2 ou 3 em `src/`. | **100% Conforme** |
| **Seção 3: Stack Técnica** | Python 3.11+, type hints, python-chess, Stockfish UCI local, SQLite, Typer, pytest, ruff, mypy. | Declarados no `pyproject.toml` e validados com 100% de sucesso por linters e compiladores de tipos. | **100% Conforme** |
| **Seção 4: Estrutura** | Diretórios `src/chess_analyzer/` e `tests/` com módulos previstos. | Todos os arquivos presentes nas localizações exatas, com o acréscimo positivo de `tests/test_cli.py`. | **100% Conforme** |
| **Seção 5: Classificação** | Proibição de centipawns cru; regressão logística de Win% do Lichess ($k=0.00368208$). | Implementado em `classify.py:17, 92-116` e comprovado por testes matemáticos dedicados. | **100% Conforme** |
| **Seção 6: Skills** | TDD para `classify.py` e `stats.py`; `grounded-planning` e `verification-before-attestation`. | Relatórios de etapa registram evidências e testes foram implementados com rigor. | **100% Conforme** |
| **Seção 7: Regras Não Negociáveis** | Sem conclusão sem teste real; sem dependência extra; proteção de dados em `.gitignore`. | `.gitignore` configurado; 80 testes passando; dependências auditadas. | **100% Conforme** |
| **Seção 8: Definição de Pronto** | CLI executando `import`, `analyze`, `stats`; teste de integração com Stockfish real sem mock. | Teste `test_analyze_real_stockfish_end_to_end` e teste E2E do CLI validados com sucesso. | **100% Conforme** |
| **Seção 9: Convenções** | Nomes em inglês, docstrings em português, conventional commits, ruff e mypy limpos. | Nomenclatura, comentários e linters 100% aderentes. | **Conforme** (Menções históricas a `AGENT.md` em 3 arquivos) |
| **Seção 10: Notas Operacionais** | Registro documental de lições aprendidas e protocolo de skills. | Práticas seguidas em todas as etapas de desenvolvimento. | **100% Conforme** |

---

### Eixo 6: Dependências e Superfície de Risco

Auditoria de segurança, cadeia de suprimentos e isolamento de dados:

#### 1. Cruzamento Exaustivo de Dependências (`pyproject.toml` vs Imports)
- **Dependências de Produção Declaradas:**
  - `chess>=1.10.0` -> Importado e utilizado em `pgn_import.py`, `engine.py`, `classify.py`, `analyze.py`, `stats.py`, `cli.py`.
  - `typer>=0.12.0` -> Importado e utilizado em `cli.py`.
  - `rich>=13.7.0` -> Importado e utilizado em `cli.py` (`rich.console`, `rich.table`).
- **Dependências de Desenvolvimento Declaradas:**
  - `pytest>=8.0.0` -> Utilizado na execução de todos os testes em `tests/`.
  - `ruff>=0.4.0` -> Utilizado para linting e verificação de estilo.
  - `mypy>=1.9.0` -> Utilizado para checagem estrita de tipos.
- **Resultado:** **Zero dependências fantasmas** (declaradas mas não importadas) e **zero dependências não declaradas** (importadas de fora do manifesto).

#### 2. Isolamento de Dados Pessoais e `.gitignore`
As linhas 6 a 16 do `.gitignore` protegem estritamente:
```gitignore
# Data and SQLite databases
data/
*.db
*.sqlite
*.sqlite3

# Virtual environments
.venv/
```
- **Constatação:** O banco de dados padrão do usuário (`data/chess_analyzer.db`) e o binário local do Stockfish (`.venv/bin/stockfish`) estão estritamente fora do controle de versão.

#### 3. Superfície de Rede e Segurança
- O software opera **100% offline**, sem sockets abertos, sem telemetria, sem clientes HTTP e sem credenciais/segredos embutidos no código.

---

### Eixo 7: Hipóteses de Performance

Mapeamento sistemático de todas as alegações e premissas de performance presentes na arquitetura da Fase 1:

| ID | Premissa / Otimização de Performance | Localização | Evidência Comprovada | Hipótese Pendente de Medição |
|---|---|---|---|---|
| **H1** | **Latência de Avaliação Stockfish (`depth=12`, ~6ms-50ms/lance)** | `engine.py:29-35`, `analyze.py:30-34` | Mecanismo de timeout de segurança (2.0s) comprovado em `test_engine.py:80-94`. | Distribuição estatística de latência (p50, p95, p99) em CPUs de diferentes arquiteturas. |
| **H2** | **Taxa de Cache Hit em FENs Reduz Reprocessamento** | `GEMINI.md:39-40`, `db.py:201-255`, `analyze.py:35-38` | Bypass completo do Stockfish em caso de FEN já avaliado comprovado em `test_analyze.py:93-122`. | Percentual real de economia de tempo (% cache hit) em bases reais de repertório de 1.000+ partidas. |
| **H3** | **Rendimento de Inserção em Lote SQLite (WAL + `batch_size=100`)** | `db.py:21-27, 115, 182-186` | Transacionalidade e atomicidade do lote validadas em `test_db.py:285-309`. | Throughput máximo sustentado (partidas/segundo ou lances/segundo) em escala massiva (100k partidas). |
| **H4** | **Two-Phase no `analyze_games` Minimiza Locks no SQLite** | `analyze.py:57-61, 113-165` | Salvamento atômico individual em `evaluations` + lote único em `moves` testado em `test_analyze.py`. | Concorrência de múltiplos processos leitores/escritores sob carga concorrente extrema. |
| **H5** | **Prevenção de Overflow Numérico em Centipawns Extremos** | `classify.py:108-114` | Clamping de expoente em $[-700, 700]$ previne `math range error` em ponto flutuante IEEE 754. | Nenhuma (Comprovado matematicamente por limites da função). |
| **H6** | **Heurística Híbrida de Fase por Material ($\le 26$) e Ply ($\le 20$)** | `stats.py:56-79` | Precedência estrita (Endgame -> Opening -> Middlegame) validada em `test_stats.py:340-372`. | Aderência e acurácia enxadrística contra classificações manuais de partidas magistrais. |

---

## 3. Quadro Consolidado de Achados e Severidades

| ID | Eixo | Localização | Severidade | Descrição Sintética | Ação Recomendada para Fases Futuras |
|---|---|---|---|---|---|
| **ACH-01** | Eixo 1 | `src/chess_analyzer/analyze.py:106` | **Não-bloqueante** | `eval_before` do lance 1 assume `STARTING_FEN`, desconsiderando partidas com tag `[FEN "..."]`. | Obter o FEN inicial do cabeçalho da partida quando `game.headers.get("SetUp") == "1"`. |
| **ACH-02** | Eixo 2 | `src/chess_analyzer/analyze.py:21-47` | **Não-bloqueante** | Chamada redundante de `init_db(db_path)` em `_get_or_evaluate_fen` (*risco já identificado e aceito na Etapa 6/7*). | Invocar `init_db(db_path)` uma única vez no início da execução de `analyze_games`. |
| **ACH-03** | Eixo 1 | `src/chess_analyzer/classify.py:89` | **Observação** | Dataclass `PositionEvaluation` armazena centipawns das Pretas no campo `white_cp` após normalização. | Criar dataclass semântica `PlayerEvaluation(cp: int, mate: int)` para retorno de `to_player_perspective`. |
| **ACH-04** | Eixo 1 | `src/chess_analyzer/classify.py:97-98` | **Observação** | Transições entre distâncias de mate (+M1 a +M8) possuem Win% idêntico (100%), gerando classificação `BEST`. | Considerar penalização por extensão de mate em fases futuras de coaching. |
| **ACH-05** | Eixo 2 | `src/chess_analyzer/db.py:30-33` | **Não-bloqueante** | `normalize_fen` via `split()[:4]` não unifica casas de en passant pseudo-sintáticas (`e3`) com casas legais (`-`). | Normalizar FEN usando `chess.Board(fen).ep_square` para verificar se a captura en passant é legal. |
| **ACH-06** | Eixo 3 | `src/chess_analyzer/cli.py:196-240` | **Não-bloqueante** | `stats_cmd` não possui `try/except` de topo, vazando traceback do Rich em caso de banco corrompido ou erro de I/O. | Envolver `stats_cmd` em `try ... except Exception as e:` emitindo mensagem limpa via `err_console.print`. |
| **ACH-07** | Eixo 4 | `tests/test_analyze.py:141-145` | **Não-bloqueante** | Teste de integração de análise utiliza apenas lances `BEST` para ambos os lados; falta cenário com erros das Pretas. | Adicionar caso de teste em `test_analyze.py` com `BLUNDER` das Pretas no loop de análise. |
| **ACH-08** | Eixo 4 | `tests/test_cli.py:222` | **Observação** | Asserção frouxa `data["total_analyzed_moves"] > 0` no teste E2E do CLI em vez de `== 2`. | Ajustar asserção para validar a contagem exata de lances analisados (`== 2`). |
| **ACH-09** | Eixo 4 | `tests/test_classify.py:109` | **Observação** | Ausência de teste para overflow de centipawns e para validação de `mate_for_white=0`. | Adicionar testes unitários de valores de fronteira em `test_classify.py`. |
| **ACH-10** | Eixo 4 | `tests/test_engine.py:70` | **Observação** | Ausência de teste para chamada de `evaluate()` fora do gerenciador de contexto `with`. | Adicionar teste em `test_engine.py` validando o lançamento de `RuntimeError`. |

---

## 4. Evidências de Execução Real e Comandos Brutos

Em conformidade estrita com a skill `verification-before-attestation`, todas as saídas abaixo foram capturadas diretamente da execução real no ambiente de desenvolvimento:

### 1. `git status` (Verificação de Integridade Read-Only)
```text
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   .gitignore
	modified:   GEMINI.md

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	.agents/
	ORIGINAL_REQUEST.md
	src/chess_analyzer/analyze.py
	src/chess_analyzer/cli.py
	src/chess_analyzer/stats.py
	tests/test_analyze.py
	tests/test_cli.py
	tests/test_stats.py

no changes added to commit (use "git add" and/or "git commit -a")
```
*(Nota: Nenhum arquivo em `src/` ou `tests/` foi alterado durante o processo de auditoria).*

### 2. `pytest -v` (Suíte Completa de Testes)
```text
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/cleiton/projetos.pessoais
configfile: pyproject.toml
testpaths: tests
collected 80 items

tests/test_analyze.py ......                                             [  7%]
tests/test_classify.py ....................                              [ 32%]
tests/test_cli.py ............                                           [ 47%]
tests/test_db.py .............                                           [ 63%]
tests/test_engine.py .........                                           [ 75%]
tests/test_pgn_import.py ...........                                     [ 88%]
tests/test_stats.py .........                                            [100%]

=============================== warnings summary ===============================
.venv/lib64/python3.14/site-packages/chess/engine.py:54
  /home/cleiton/projetos.pessoais/.venv/lib64/python3.14/site-packages/chess/engine.py:54: DeprecationWarning: 'asyncio.DefaultEventLoopPolicy' is deprecated and slated for removal in Python 3.16
    EventLoopPolicy = asyncio.DefaultEventLoopPolicy

tests/test_analyze.py: 1 warning
tests/test_cli.py: 7 warnings
tests/test_engine.py: 10 warnings
  /home/cleiton/projetos.pessoais/.venv/lib64/python3.14/site-packages/chess/engine.py:65: DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead
    assert asyncio.iscoroutinefunction(coroutine)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================= 80 passed, 19 warnings in 9.98s ========================
```

### 3. Cobertura de Linhas (`pytest --cov`)
```text
ERROR: usage: pytest [options] [file_or_dir] [file_or_dir] [...]
pytest: error: unrecognized arguments: --cov=chess_analyzer
```
- **Declaração de Não Verificado:** O pacote `pytest-cov` não está instalado no ambiente virtual (`.venv`). Conforme a regra de Read-Only estrito, nenhuma nova dependência foi instalada. A cobertura foi comprovada qualitativamente pela varredura dos 80 testes implementados.

### 4. `mypy src tests` (Verificação Estrita de Tipos)
```text
Success: no issues found in 17 source files
```

### 5. `ruff check .` (Linter de Código)
```text
All checks passed!
```

### 6. Execução Completa do CLI End-to-End com Stockfish Real

#### A. Importação de PGN
```bash
.venv/bin/chess-analyzer import tests/fixtures/lichess_real.pgn --db data/test_audit.db
```
**Output:**
```text
Importação concluída com sucesso!
Total processado: 1 | Inseridas: 1 | Ignoradas/Duplicadas: 0
(Exit Code: 0)
```

#### B. Análise com Stockfish Real
```bash
.venv/bin/chess-analyzer analyze --db data/test_audit.db --depth 8
```
**Output:**
```text
Análise concluída com sucesso!
Partidas analisadas: 1/1 | Lances avaliados: 3
(Exit Code: 0)
```

#### C. Relatório Estatístico Tabular
```bash
.venv/bin/chess-analyzer stats Player1 --db data/test_audit.db
```
**Output:**
```text
                              Estatísticas por Cor                              
┏━━━━━━━┳━━━━━━━━┳━━━━━━┳━━━━━┳━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃       ┃        ┃      ┃     ┃      ┃      ┃      ┃       ┃       Perda Média ┃
┃ Cor   ┃ Lances ┃ Best ┃ Exc ┃ Good ┃ Inac ┃ Mist ┃ Blund ┃           (ΔWin%) ┃
┡━━━━━━━╇━━━━━━━━╇━━━━━━╇━━━━━╇━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ white │      2 │    1 │   0 │    1 │    0 │    0 │     0 │             0.92% │
└───────┴────────┴──────┴─────┴──────┴──────┴──────┴───────┴───────────────────┘
                        Estatísticas por Abertura (ECO)                         
┏━━━━━┳━━━━━━━━┳━━━━━━┳━━━━━┳━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ ECO ┃ Lances ┃ Best ┃ Exc ┃ Good ┃ Inac ┃ Mist ┃ Blund ┃ Perda Média (ΔWin%) ┃
┡━━━━━╇━━━━━━━━╇━━━━━━╇━━━━━╇━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ C20 │      2 │    1 │   0 │    1 │    0 │    0 │     0 │               0.92% │
└─────┴────────┴──────┴─────┴──────┴──────┴──────┴───────┴─────────────────────┘
                         Estatísticas por Fase do Jogo                          
┏━━━━━━━━━┳━━━━━━━━┳━━━━━━┳━━━━━┳━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃         ┃        ┃      ┃     ┃      ┃      ┃      ┃       ┃     Perda Média ┃
┃ Fase    ┃ Lances ┃ Best ┃ Exc ┃ Good ┃ Inac ┃ Mist ┃ Blund ┃         (ΔWin%) ┃
┡━━━━━━━━━╇━━━━━━━━╇━━━━━━╇━━━━━╇━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ OPENING │      2 │    1 │   0 │    1 │    0 │    0 │     0 │           0.92% │
└─────────┴────────┴──────┴─────┴──────┴──────┴──────┴───────┴─────────────────┘
(Exit Code: 0)
```

#### D. Relatório Estatístico em JSON
```bash
.venv/bin/chess-analyzer stats Player1 --db data/test_audit.db --json
```
**Output:**
```json
{
  "player": "Player1",
  "total_analyzed_moves": 2,
  "color": [
    {
      "group_key": "white",
      "group_type": "color",
      "category_counts": {
        "best": 1,
        "excellent": 0,
        "good": 1,
        "inaccuracy": 0,
        "mistake": 0,
        "blunder": 0,
        "total": 2
      },
      "avg_delta_win_prob": 0.9201047954718256,
      "total_moves": 2
    }
  ],
  "opening": [
    {
      "group_key": "C20",
      "group_type": "eco",
      "category_counts": {
        "best": 1,
        "excellent": 0,
        "good": 1,
        "inaccuracy": 0,
        "mistake": 0,
        "blunder": 0,
        "total": 2
      },
      "avg_delta_win_prob": 0.9201047954718256,
      "total_moves": 2
    }
  ],
  "game_phase": [
    {
      "group_key": "OPENING",
      "group_type": "game_phase",
      "category_counts": {
        "best": 1,
        "excellent": 0,
        "good": 1,
        "inaccuracy": 0,
        "mistake": 0,
        "blunder": 0,
        "total": 2
      },
      "avg_delta_win_prob": 0.9201047954718256,
      "total_moves": 2
    }
  ]
}
```

### 7. Auditoria de Dependências Online (`pip-audit`)
```text
urllib3.exceptions.MaxRetryError: HTTPSConnectionPool(host='pypi.org', port=443): Failed to resolve 'pypi.org'
```
- **Declaração de Não Verificado:** A checagem remota contra o banco de vulnerabilidades do PyPI não pôde ser executada devido ao isolamento de rede (sandbox offline). A auditoria estática de integridade das dependências locais foi realizada com sucesso.

---

## 5. Declaração de Encerramento e Assinatura da Equipe de Auditoria

A equipe de auditoria multi-agente conclui formalmente a auditoria de código da **Fase 1 (Etapas 1 a 8)** do **Chess Performance Analyzer**.

### Parecer Final
O software encontra-se em **excelente estado de integridade arquitetural e maturidade técnica**:
1. A convenção de perspectiva das Brancas e a regressão logística de Win Probability do Lichess estão implementadas com rigor matemático e consistência em todos os módulos.
2. O ciclo de vida das conexões SQLite é 100% protegido por blocos `try/finally`, com modo WAL e transações atômicas Two-Phase.
3. Os códigos de saída do CLI e a gestão do subprocesso do Stockfish operam conforme as melhores práticas de ferramentas Unix/Python.
4. A suíte de testes unitários e de integração com 80 itens é discriminatória, robusta e aderente às regras de TDD estrito.
5. Os apontamentos não-bloqueantes catalogados neste relatório constituem refinamentos pontuais de performance e usabilidade que não impedem o avanço do projeto para a Fase 2 (Treino direcionado com dataset de puzzles do Lichess).

**Assinaturas dos Agentes Especializados:**
- **Explorer 1:** Auditoria dos Eixos 1 (Perspectiva) e 4 (Testes / Anti-Falso-Positivo).
- **Explorer 2:** Auditoria dos Eixos 2 (Transações SQLite) e 3 (Tratamento de Erros).
- **Worker 1:** Auditoria dos Eixos 5 (GEMINI.md), 6 (Dependências) e 7 (Performance) + Execução Real.
- **Worker 2:** Consolidação Formal e Redação Final do Relatório.
