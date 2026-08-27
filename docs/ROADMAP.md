# Roadmap de Produto — Chess Performance Analyzer

## Estado atual (v0.1.0)

O projeto entrega uma CLI local completa para:

1. Importar PGNs e analisar lances com Stockfish (ΔWin%).
2. Agregar estatísticas por cor, abertura (ECO) e fase do jogo.
3. Indexar puzzles do Lichess e gerar treino direcionado por fraqueza detectada.

**Decisão de retomada:** estabilizar o núcleo antes de expandir superfície.

## Direção recomendada (próximos 90 dias)

### Prioridade 1 — Uso real pessoal
- Importar 50+ partidas próprias (Lichess/Chess.com).
- Validar se o diagnóstico por fase corresponde à percepção do jogador.
- Medir tempo de `analyze` e utilidade do comando `train`.

### Prioridade 2 — Melhorias de qualidade de treino
- Inferência de código ECO quando ausente em PGNs do Chess.com.
- Refinar calibração de Elo com intervalo de confiança por tamanho de amostra.
- Opcional: cruzar temas táticos específicos (fork, pin, skewer) com fraquezas.

### Prioridade 3 — Integração com agentes (MCP)
- Expor `import`, `analyze`, `stats` e `train` como tools MCP via FastMCP.
- Manter lógica no core Python; MCP apenas como adaptador fino.
- **Justificativa:** baixo custo de manutenção, alto valor para uso com LLMs locais.

### Adiado
- **Dashboard web:** só se houver necessidade de visualização compartilhada.
- **Coaching LLM:** depende de MCP estável e dados confiáveis.
- **Fase 2.5 (temas táticos via Stockfish):** alto custo computacional; avaliar após uso real.

## Critérios para avançar de fase

| Marco | Critério |
|---|---|
| Core estável | 105+ testes passando, CI verde, zero gaps P0 |
| MCP | 3+ workflows reais com agente usando as tools |
| Dashboard | 2+ usuários pedindo visualização além da CLI |
| Temas táticos | Evidência de que fase do jogo não basta para treino |
