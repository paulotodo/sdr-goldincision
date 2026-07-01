# Research: sdr-turnos-obs

Documento do Phase 0 do `/plan`. A spec não deixou nenhum `[NEEDS
CLARIFICATION]` (defaults conservadores foram fornecidos e ratificados na
etapa clarify). As decisões abaixo resolvem escolhas de abordagem técnica
com trade-offs reais, ancoradas no código existente e na constitution.

## Decision 1: Onde e como armazenar os contadores de turno

**Decision**: Persistir `turnos_sessao`, `turnos_no_no:{etapa}` e
`ultima_interacao` como campos do hash Redis `estado:{chamadoId}` (helper
`estado_key`, `app/core/redis_keys.py:56`), já usado como cache de variáveis
de sessão. Incremento via `HINCRBY` (atômico), leitura via `HGET`/`HGETALL`.
Sem coluna nova, sem migration.

**Rationale**: A restrição dura da feature é "sem migration". O hash
`estado:{id}` já existe e é o local canônico de variáveis efêmeras de sessão
(Redis 7). `HINCRBY` garante incremento atômico 1x/turno mesmo sob
concorrência (FR-002). Alinha com o Princípio III (memória em camadas:
Redis para estado quente) e com a prática do projeto ("Redis para
contadores; durável só para analytics", plano-controle-turnos.md §5).

**Alternatives considered**:
- Coluna durável em `Ticket`/`Contato` (Postgres) → exige migration
  (proibido nesta feature); overkill para contador efêmero.
- Entidade `Turno` durável → descartada explicitamente na spec (Out of
  Scope); o log estruturado (Decision 5) cobre analytics.

## Decision 2: Precedência e reset dos contadores

**Decision**: (a) O contador por-nó `turnos_no_no:{etapa}` reinicia (via
`HDEL` do campo da etapa anterior, ou simplesmente passa a incrementar o
campo da nova etapa) quando `ticket.etapa_mapa_mestre` muda. (b) O teto de
sessão (`MAX_TURNOS_SESSAO`) tem **precedência** sobre o teto de nó: se
ambos forem atingidos no mesmo turno, aplica-se o handoff de sessão. (c) A
perda dos contadores (Redis reiniciado sem persistência) é degradação
aceita — `HGET` ausente ⇒ trata como 0, atendimento continua (fail-open,
nunca bloqueia o lead).

**Rationale**: Reflete os edge cases da spec. Precedência de sessão porque
é o teto de segurança mais alto (handoff > nudge). Fail-open respeita o
Princípio IV (prioridade é atendimento correto, nunca acelerar em prejuízo)
e o Princípio V (handoff disciplinado, mas contador perdido não deve
derrubar uma sessão legítima).

**Alternatives considered**:
- Contador por-nó cumulativo sem reset ao avançar → confundiria com o teto
  de sessão e penalizaria jornadas normais de múltiplas etapas.
- Fail-closed (bloquear se contador ausente) → violaria Princípio IV e
  degradaria a experiência por uma falha de infra transitória.

## Decision 3: Distinção do contador anti-loop existente

**Decision**: O novo contador de **turnos** é ortogonal ao contador de
**tentativas não-reconhecidas** por etapa já existente (`_MAX_TENTATIVAS=3`,
`_tent_bump`/`_reformular_ou_handoff`/`_tent_clear` sobre
`Contato.etapa_funil` em `app/core/flow.py`). Os dois coexistem: o
anti-loop conta respostas que o motor não reconheceu na etapa e zera ao
avançar; o contador de turnos conta TODOS os turnos (reconhecidos ou não) e
serve ao orçamento global/por-nó. Nenhuma linha do anti-loop é alterada.

**Rationale**: FR-019 exige preservação sem alteração de comportamento. São
sinais de saúde distintos: "lead não está sendo entendido nesta etapa"
(anti-loop) vs "conversa está longa demais" (orçamento de turnos). Fundi-los
perderia informação e quebraria testes existentes (`test_flow.py`).

**Alternatives considered**:
- Reusar `Contato.etapa_funil` para ambos → acopla semânticas distintas,
  arrisca regressão no `_reformular_ou_handoff`, e exigiria tocar Postgres.

## Decision 4: Robustez do lock — elevar TTL vs renovar PTTL

**Decision**: **Elevar** `LOCK_TTL_MS` de 30_000 para ~90_000 (env-driven,
`lock_ttl_ms` ou reuso do existente em `redis_keys.py:20`) como abordagem
primária; a renovação de PTTL durante o processamento fica documentada como
alternativa não-implementada. O valor de 90s deve ser **confirmado com dados
reais** do `duracao_ms` do evento de turno (Decision 5) antes de ser
considerado final.

**Rationale**: Pior caso de turno = LLM (gpt-4o, `reasoning_max_tokens=280`)
+ até 4 envios com `inter_block_delay_seconds=1.0` + pacing
`whatsapp_min_interval_ms=1000` + retries com backoff (até ~4s×3). Soma
grosseira ~30-50s, com folga 90s cobre. Elevar TTL é a opção mais simples
(plano-controle-turnos.md §Fase 4 recomenda (a) pela simplicidade). A
observabilidade (US5) fecha o loop empírico (FR-013 AC-2). Alinha Princípio
VI (isolamento/robustez de infra).

**Alternatives considered**:
- Renovar PTTL num heartbeat durante o processamento → mais robusto mas
  adiciona complexidade (task de refresh, tratamento de falha do refresh);
  adiar até que os dados mostrem turnos > 90s.
- Manter 30s → risco real de re-processamento em turno lento (G4).

## Decision 5: Evento de turno — reusar `observability/log.py`

**Decision**: Adicionar `log_turno(...)` em `app/observability/log.py`
reusando `_emit` (l.63), `_scrub` (l.48) e `_mask_number` (l.40) já
existentes. Emitir 1 evento por turno em `_process_consolidated_messages`
(`app/api/webhook.py`) com os campos de FR-015. Medir `duracao_ms` com um
relógio monotônico em torno do processamento do turno. Em falha do turno,
emitir evento parcial com `acao="erro"` (FR-016), via bloco try/finally.

**Rationale**: onda-007 (read-back, referência histórica) já construiu essa
infra com scrub de secrets e mascaramento de número — recriar seria
retrabalho e risco. FR-016 (evento mesmo em falha) mapeia naturalmente a
`finally`. Alinha Princípio VI (secrets nunca vazam: `_scrub` +
`_mask_number` no número do lead → PII protegida).

**Alternatives considered**:
- Novo módulo de métricas → duplicação; a infra existente já cobre.
- Persistir evento em Postgres → exigiria migration (proibido); log
  estruturado ao stdout + coletor externo é suficiente (spec Out of Scope
  para entidade durável).

## Decision 6: Recovery de debounce no startup — reagendar vs flush imediato

**Decision**: No lifespan de `app/main.py`, após conectar o Redis, executar
`SCAN debounce:*`. Para cada lista pendente: se um marcador de "vencimento"
indica que a janela de 8s ainda não passou, reagendar o `_delayed_flush`
(reusar `DebounceManager`); caso contrário (janela vencida ou sem marcador),
**flush imediato**. O flush é atômico (`LRANGE`+`DEL`) ⇒ idempotente
(FR-012). Estratégia conservadora: na dúvida, flush imediato (o lead já
esperou).

**Rationale**: FR-011/FR-012. O flush atômico existente já garante
"exatamente uma vez" mesmo se o recovery rodar 2x. Flush imediato como
default evita depender de um timestamp que pode não existir para listas
criadas antes desta feature. Alinha Princípio III (não perder turno =
memória/continuidade) e o critério "restart no meio do debounce não perde
turno".

**Alternatives considered**:
- Sempre reagendar (nunca flush imediato) → se a janela já passou, atrasa
  desnecessariamente a resposta ao lead.
- Marcador `debounce_due:{id}` com TTL (plano §Fase 3 alternativa) → mais
  preciso, mas adiciona uma chave por rajada; adotar só se o flush-imediato
  provar-se agressivo demais na validação real.

## Decision 7: Formato e execução do golden set

**Decision**: Casos em `tests/golden/*.json` (ou `.yaml`) no formato da
skill `agente-atendimento-confiavel/padroes-implementacao.md §7`:
`{mensagem, estado_inicial, esperado:{proxima_acao, etapa,
nao_repetir_slot, ...}}`. Harness pytest dedicado
(`tests/golden/test_golden_runner.py`) roda o **FlowEngine REAL** (via
`StubFlowEngine` que stuba só I/O de DB), agrega taxa de acerto por dimensão
(fluxo correto, abstenção correta, zero preço inventado) e imprime relatório.
Marcado com `@pytest.mark.golden` e **excluído do gate de CI** por default
(suíte separada); documentar `python3 -m pytest tests/golden -m golden` no
README/quickstart.

**Rationale**: FR-017/FR-018. FlowEngine real é exigência do projeto
(CLAUDE.md: nunca mockar o motor). Suíte separada porque casos derivados de
conversas reais podem ser instáveis e não devem travar o merge (spec:
"não bloquear o CI se instável"). Alinha Princípios I, II e III (verifica
fidelidade de fluxo, abstenção/anti-alucinação e não-repetição de slot).

**Alternatives considered**:
- Integrar ao `test_flow.py` principal → violaria "suíte separada" e
  arriscaria flakiness no CI obrigatório.
- Mock do motor para estabilidade → proibido (StubFlowEngine só stuba I/O).

## Decision 8 (Segurança): scrub e mascaramento no evento de turno

**Decision**: O evento de turno passa por `_scrub` (remove chaves sensíveis)
e o número do lead é mascarado via `_mask_number` antes de `_emit`. O campo
`ultima_interacao` e a detecção de inatividade são **fail-open**: erro de
leitura/parse do timestamp ⇒ trata como interação recente (não dispara
retomada/expiração espúria). Nenhum conteúdo de mensagem do lead entra no
evento (mensagem do lead = dado não-confiável, SEC-LLM-1); só metadados
(intenção classificada, idioma, contadores).

**Rationale**: Antecipa os findings LOW típicos de features de
observabilidade (read-back: fail-open de `last_seen`, PII em eventos). Alinha
Princípio VI (secrets/PII nunca vazam) e a restrição anti prompt-injection
(não logar conteúdo bruto do lead). Incorporar como AC de segurança desde o
design evita retrabalho no gate owasp.

**Alternatives considered**:
- Logar payload bruto do turno → vaza PII/secrets; rejeitado.
- Fail-closed na inatividade → uma leitura corrompida derrubaria a sessão;
  viola Princípio IV.

## Decision 9: Limiar de aceitação do golden set (CHK011)

**Decision**: Nesta rodada (Onda 1), o golden set (Decision 7) é
**informativo** — o harness agrega e imprime a taxa de acerto por dimensão
(fluxo correto, abstenção correta, zero preço inventado), mas **não define
um patamar mínimo bloqueante**. Não há threshold numérico de SC-008 nesta
feature; a suíte permanece marcada `@pytest.mark.golden` e excluída do gate
de CI obrigatório (conforme já previsto em spec.md/Decision 7 — "não
bloquear CI se instável"). É o default conservador quando a decisão do dono
do produto está indisponível (task 1.2.1).

**Rationale**: CHK011 aponta que SC-008 não define patamar mínimo de taxa de
acerto. Definir um threshold bloqueante sem dados históricos de execução
arriscaria (a) falsos-negativos travando o merge por instabilidade de casos
derivados de conversas reais, ou (b) um número arbitrário sem lastro
empírico. Manter informativo nesta rodada é reversível e barato: uma vez que
o golden set rodar algumas vezes em produção/CI manual, um threshold por
dimensão pode ser adicionado como mudança isolada (nova feature ou fast-
follow), sem impacto em código de produção.

**Alternatives considered**:
- Threshold mínimo por dimensão nesta rodada (ex.: >=90% fluxo correto,
  100% zero-preço-inventado) → rejeitado por falta de dado histórico para
  calibrar o número; risco de CI instável logo na primeira adoção.
- Sem qualquer relatório agregado → rejeitado; perde-se visibilidade sobre
  regressão de qualidade de jornada (violaria o propósito de US6).

**Nota de esclarecimento SC-008** (task 1.2.3): a redação original de SC-008
já previa suíte separada e não-bloqueante — esta decisão apenas explicita
que, além de não-bloqueante, o critério de aceitação numérico fica
**adiado** (não é omissão, é escopo consciente desta Onda 1). Nenhuma
mudança de escopo em spec.md foi necessária; ver nota equivalente também em
quickstart.md.
