# Plano — Melhor controle de turnos (Pilar 7 da metodologia "Agente Confiável")

> **Para executar numa sessão limpa.** Documento autocontido. Projeto em
> `/root/sdr-goldincision`. Stack em produção: `sdr-whatsapp_app`
> (`registry.todo-tips.com/sdr-whatsapp`), Docker Swarm.
> Origem: avaliação da estrutura atual contra a skill `agente-atendimento-confiavel`
> (8 pilares), com foco no pedido do operador (2026-06-30): **melhor controle dos turnos**.

## 0. Como ler este plano

A skill define 8 pilares de confiabilidade. A §1 é a **avaliação honesta** da
estrutura atual contra os 8 pilares (o que já existe, com refs). A partir da §2 o
plano se concentra no **Pilar 7 — controle de sessão longa/turnos**, que é o pedido.
Pilares 5 e 6 (verificação de fidelidade e contrato JSON) aparecem como anexo
opcional porque habilitam controle, mas não são o foco.

---

## 1. Avaliação contra os 8 pilares (estado real, verificado em código)

> ⚠️ Esta auditoria foi **verificada linha a linha**. Vários mecanismos de turno que
> uma leitura superficial julgaria "ausentes" **já estão implementados** — não
> reimplementar.

| Pilar | Estado | Evidência (arquivo:linha) |
|---|---|---|
| **1. Máquina de estados fora do prompt** | ✅ Forte | Fluxo decidido por código; nó em `Ticket.etapa_mapa_mestre`; `FlowEngine.process` despacha por caminho/etapa (`app/core/flow.py`). LLM não decide próximo nó. |
| **2. Estado estruturado** | ✅ Forte | `Contato` (médico, especialidade, experiência, idioma, interesse, `perfil` JSONB) + `Ticket.etapa_mapa_mestre`; chaveado por `chamadoId`. Guardas "não re-perguntar" já aplicadas (C1/C2/C3). |
| **3. Memória em camadas** | ✅ Boa | Janela quente Redis (`hot_window.py`); resumo rolante `SessaoConversa.resumo_rolante` acima de `_SUMMARIZE_THRESHOLD=50` (`memory.py:243`); recupera resumo de tickets anteriores (`_recover_previous_summary`). |
| **4. Recuperação ancorada** | ⚠️ Parcial | Blocos canônicos verbatim do DB ✅ (apresentações/links/preços não passam pelo LLM). FAQ/objeções por **grounding montado manualmente**, **sem RAG híbrido / limiar / abstenção formal** — abstenção hoje é via recusa+handoff no prompt, não por score. |
| **5. Portão de verificação de fidelidade** | ❌ Ausente | `GroundedResponder.generate` (`responder.py:165`) envia o texto gerado direto; sem fact-check pós-geração de preço/data/elegibilidade. |
| **6. Saída estruturada JSON + guardrails** | ⚠️ Parcial | Responder retorna **texto livre** `(resposta, handoff)` (`responder.py:265`), não JSON com contrato; `temperature=0.3`. Roteamento é determinístico (bom), mas a saída do LLM não é um contrato validável. |
| **7. Controle de turnos** | ⚠️ **Parcial — foco deste plano** | Muito já existe (ver abaixo); faltam orçamento global de turnos, timeout de inatividade e durabilidade do debounce. |
| **8. Observabilidade e avaliação** | ⚠️ Parcial | Logging estruturado JSON ao stdout (`observability/log.py`); **sem registro por-turno** (turno_id/intent/fonte/ação) e **sem golden set** de avaliação. |

### 1.1 Pilar 7 — o que JÁ está implementado (NÃO refazer)

- **Máquina de estados durável**: nó em `Ticket.etapa_mapa_mestre` (Postgres) sobrevive a restart.
- **Contador anti-loop por etapa**: `_MAX_TENTATIVAS=3` (`flow.py:150`); `_tent_bump`
  incrementa `Contato.etapa_funil` (JSON `{"et","n"}`, `flow.py:744`);
  `_reformular_ou_handoff` (`flow.py:947`) reformula na 2ª tentativa e **encaminha a
  humano na 3ª**; `_tent_clear` zera ao avançar. Chamado de ~10 etapas.
- **Debounce de rajada de entrada**: consolida até 5 msgs em 8s (`debounce.py`).
- **Teto de mensagens do bot por turno**: `max_msgs_per_turn=4` **aplicado** em
  `send_message_blocks` (`chatmaster.py:353` — envia `cap-1` blocos + aviso de overflow).
- **Pacing WhatsApp**: `_Pacer` (`chatmaster.py:85`) garante intervalo mínimo entre envios.
- **Retry/backoff 429/5xx** honrando `Retry-After` (`chatmaster.py:299`).
- **Idempotência** por messageId: `SET NX EX 86400` (`idempotency.py`).
- **Lock por ticket**: `SET NX PX 30000` (`locks.py:26`), evita processamento concorrente.
- **Gate de fila (IA=77)**: silencia o bot quando o humano assume (`webhook.py`).

### 1.2 Definição de "turno" hoje (e a ambiguidade)

- **Turno do lead** = 1 rajada consolidada pelo debounce → 1 chamada a `engine.process`.
- **Turno do bot** = 1 reação = 1..N mensagens (teto `max_msgs_per_turn`).
- **Ambiguidade**: "turno" não é entidade de primeira classe. O nº de mensagens
  trocadas é usado de forma incidental (`flow.py:659`, para variar aberturas), mas
  não há **contador de turnos por sessão nem por nó** — só o contador de *tentativas
  não reconhecidas* por etapa (que **zera ao avançar**).

---

## 2. Gaps reais de controle de turnos (verificados, com refs)

Estes são os gaps que sobram **depois** de creditar o que já existe (§1.1):

| # | Gap | Evidência | Por que importa |
|---|---|---|---|
| **G1** | **Sem orçamento global de turnos** (por sessão e por nó) | Só existe contador *por-etapa de respostas não reconhecidas* (`flow.py:947`), que zera ao avançar (`_tent_clear`) | Lead que fica em "dúvidas" indefinidamente, ou que pula entre etapas/caminhos sem convergir, **nunca dispara escalonamento**. O anti-loop só pega "não entendi" repetido na MESMA etapa. |
| **G2** | **Sem timeout de inatividade / reengajamento** | Nenhum `last_seen`/`inativ`/`timeout` por sessão (só TTLs de lock/idempotência) | Lead some no meio da qualificação e volta dias depois → bot retoma no meio do fluxo, sem reconhecer o gap. Sessão "morta" nunca encerra. Item do checklist da skill. |
| **G3** | **Debounce não-durável (perde turno no restart)** | `asyncio.create_task(_delayed_flush)` é tarefa em-memória (`debounce.py:91`); a lista `debounce:{id}` no Redis sobrevive, mas **ninguém reagenda o flush** no startup | Deploy/restart durante a janela de 8s → mensagens consolidadas **órfãs**: só processam quando o lead mandar a próxima. Perda silenciosa de turno. |
| **G4** | **Lock 30s pode expirar num turno lento** | `_LOCK_PX=30000` (`locks.py:26`) vs turno = LLM + até 4 envios *paced* (≥1s cada) + retries (backoff até 4s×3) | Turno lento → lock expira no meio → uma nova rajada (ou retry do n8n) pode **re-processar o mesmo turno**. A idempotência cobre msg duplicada, não re-entrada no turno após expiry. |
| **G5** | **Pacing/`_Pacer` é por-processo (em-memória)** | `_last_by_number`/`_last_global` são dicts locais (`chatmaster.py:95`) | Com 2+ réplicas, o pacing **não vale globalmente** → risco de rajada agregada que fere o rate limit da Meta. Hoje 1 réplica, mas é gargalo ao escalar. |
| **G6** | **Turno não é observável** | `Mensagem` guarda mensagens, não decisões; sem `turno_id`/intent/fonte/ação por turno | Impossível medir "turnos médios até handoff", "presos na etapa X", ou **dirigir políticas de turno por dado**. Sem isso, ajustar limites vira chute. |

---

## 3. Mudanças propostas (faseadas, do maior impacto/menor risco ao maior)

> Princípio (mantido): **decisão de fluxo determinística**; o LLM não ganha poder novo.
> Tudo abaixo é código/estado/observabilidade em volta do motor existente.

### Fase 1 — Orçamento de turnos por sessão e por nó (G1) — sem migration

**Objetivo:** um turno contado explicitamente, com tetos que escalam de forma
graciosa (nudge antes de handoff duro).

- **Onde guardar:** Redis `estado:{chamadoId}` (efêmero, sem migration), seguindo a
  preferência do projeto para contadores. Campos: `turnos_sessao` (total) e
  `turnos_no_no:{etapa}` (por nó). Incrementados 1×/turno em
  `_process_consolidated_messages` (`webhook.py:317`) ou no início de `engine.process`.
- **Política (determinística), com 2 limiares configuráveis por env:**
  - `MAX_TURNOS_NO_NO` (default sugerido **6**): ao atingir, o nó faz um **nudge**
    contextual ("Quer que eu te conecte com um especialista que resolve isso em
    minutos?") — **não** handoff imediato; o lead pode seguir.
  - `MAX_TURNOS_SESSAO` (default sugerido **25**): teto de segurança → handoff cordial
    ao destino lógico do caminho atual (allowlist/config; nunca do LLM).
  - `nudge` e `handoff` registram motivo (`turnos_no_no` / `turnos_sessao`) para observabilidade.
- **Distinção importante** do anti-loop existente: aquele conta *respostas não
  reconhecidas* na etapa; este conta *turnos* (reconhecidos ou não). Os dois coexistem.
- **"Dúvidas" é legitimamente aberto:** por isso o teto de nó faz **nudge**, não corte.
  O limiar de nó pode ser maior (ou desligado) para `ETAPA_DUVIDAS` — decisão do operador (§5).

**Critério:** uma sessão que excede os tetos recebe nudge → e, no limite, handoff —
em vez de girar para sempre. Etapas curtas de qualificação não são afetadas.

### Fase 2 — Timeout de inatividade + reengajamento/encerramento (G2) — sem migration

- **Marcar atividade:** gravar `ultima_interacao` (timestamp) no `estado:{chamadoId}`
  do Redis a cada turno (barato; já escrevemos esse estado na Fase 1).
- **Detecção lazy (sem worker novo):** no início de `engine.process`, calcular
  `delta = agora - ultima_interacao`:
  - `delta > REENGAJAMENTO_HORAS` (default **24h**) e sessão ainda ativa no meio de um
    fluxo → **reconhecer o gap** antes de continuar ("Oi! Retomando de onde paramos…")
    e, se fizer sentido, reapresentar a última pergunta — sem reiniciar a jornada.
  - `delta > EXPIRA_SESSAO_HORAS` (default **72h**) → tratar como **sessão nova**
    (resetar etapa para saudação), preservando o **perfil** do `Contato` (médico,
    idioma, etc.) — não re-perguntar o que já sabemos.
- **Encerramento proativo (opcional, Fase 2b):** um job leve (APScheduler no processo
  ou um `docker service` cron) que varre sessões `em fluxo` inativas há >X e marca
  `status=encerrada`/handoff. Só implementar se o operador quiser proatividade; a
  detecção lazy acima já cobre o caso comum (o lead volta).

**Critério:** lead que retorna após horas/dias é reconhecido e retomado com naturalidade;
sessões muito antigas reiniciam preservando o perfil.

### Fase 3 — Durabilidade do debounce no restart (G3) — sem migration

Eliminar a perda silenciosa de turno quando o processo reinicia na janela de debounce.

- **Recuperação no startup:** no lifespan (`main.py`), após conectar o Redis, fazer
  `SCAN` por `debounce:*` e, para cada lista pendente, reagendar o flush
  (`DebounceManager._delayed_flush`) — ou flush imediato se a janela já passou (comparar
  com um timestamp gravado no push). Processa as mensagens órfãs uma vez (o `flush` já é
  atômico LRANGE+DEL → idempotente).
- **Alternativa mais simples (se preferir):** ao gravar a rajada, persistir também um
  marcador `debounce_due:{id}` com TTL; o recovery no startup só reagenda os que existem.
- **Teste-chave:** simular restart (instanciar novo `DebounceManager` apontando ao mesmo
  Redis com uma lista pré-existente) e confirmar que o recovery processa exatamente uma vez.

**Critério:** deploy no meio de uma rajada não perde a resposta ao lead.

### Fase 4 — Robustez de concorrência do turno (G4, G5)

- **G4 — Lock que cobre o turno inteiro:** ou (a) elevar `_LOCK_PX` para um teto seguro
  (ex.: **60–90s**, cobrindo o pior caso de LLM + 4 envios paced + retries), ou
  (b) **renovar o lock** (refresh do PTTL) durante o processamento longo. Preferir (a)
  pela simplicidade; medir a duração real de turno nos logs (Fase 5) antes de fixar o valor.
- **G5 — Pacing distribuído (só se/quando escalar >1 réplica):** mover o estado do
  `_Pacer` (último envio por número/global) para Redis (ex.: `pace:{number}` com um
  `SET` + checagem de delta, ou um token-bucket em Lua). Hoje com 1 réplica **não é
  urgente**; deixar documentado como pré-condição para escalar horizontalmente.

**Critério:** turnos lentos não são re-processados; o caminho para multi-réplica está claro.

### Fase 5 — Observabilidade de turno (G6 + Pilar 8) — habilita ajuste por dado

- **Log estruturado por turno:** em `_process_consolidated_messages`, emitir um evento
  JSON único por turno com `{chamado_id, turno_sessao, etapa_entrada, etapa_saida,
  intencao, idioma, n_blocos_enviados, acao, handoff_destino, duracao_ms,
  tentativas}`. Reusa `observability/log.py`.
- **(Opcional) Entidade `Turno` durável:** só se o operador quiser analytics histórico
  (turnos médios até handoff, etapas que mais travam). Migration simples
  `Turno(id, ticket_id, seq, etapa, intencao, acao, ts)`. Caso contrário, o log
  estruturado + um dashboard de logs já cobrem.
- **Golden set (Pilar 8):** montar 30–50 conversas reais (dos `#reset` de teste) com a
  ação esperada por turno, rodável a cada mudança de prompt/base. Formato em
  `agente-atendimento-confiavel/padroes-implementacao.md` §7.

**Critério:** dá para responder "quantos turnos até o handoff?" e pegar regressão de
fluxo antes do cliente.

### Anexo (fora do escopo de turnos, mas recomendado) — Pilares 5 e 6

- **Pilar 5 — Portão de verificação:** antes de enviar resposta **gerada** que toque
  preço/data/elegibilidade, um verificador barato (`openai_model_cheap`) confere a
  resposta contra os chunks/base; falhou → cai no bloco canônico ou handoff. As
  apresentações verbatim já são seguras (não passam pelo LLM); o portão cobre as
  **dúvidas em texto livre** (`responder.generate`).
- **Pilar 6 — Contrato JSON:** fazer `responder.generate` retornar JSON validável
  (`{resposta, fonte_ids, precisa_humano, confianca}`) em vez de texto + flag. Habilita
  o portão da Fase 5 e a observabilidade da Fase 5. Combina com a
  **interpretação agêntica** já planejada (`docs/plano-interpretacao-agentica.md`).

---

## 4. Ordem de execução sugerida

1. **Fase 1** (orçamento de turnos) — maior impacto no "controle de turnos", sem migration.
2. **Fase 3** (durabilidade do debounce) — corrige perda silenciosa, baixo risco.
3. **Fase 2** (inatividade/reengajamento) — melhora a experiência de retorno.
4. **Fase 5** (observabilidade) — passa a guiar os limiares por dado.
5. **Fase 4** (concorrência/pacing distribuído) — quando medir turnos longos / antes de escalar.
6. **Anexo** (Pilar 5/6) — junto da interpretação agêntica.

As Fases 1–3 sozinhas já entregam o "melhor controle dos turnos" pedido.

## 5. Decisões pendentes (operador)

- **Limiares:** `MAX_TURNOS_NO_NO` (~6?), `MAX_TURNOS_SESSAO` (~25?),
  `REENGAJAMENTO_HORAS` (24?), `EXPIRA_SESSAO_HORAS` (72?). Começar conservador e
  ajustar com a telemetria da Fase 5.
- **"Dúvidas" tem teto?** Aplicar nudge em `ETAPA_DUVIDAS` ou deixá-la ilimitada
  (lead pode ter muitas perguntas legítimas). Sugestão: nudge com limiar maior.
- **Encerramento proativo (Fase 2b):** quer um job que encerra sessões abandonadas,
  ou basta a detecção lazy no retorno do lead?
- **Entidade `Turno` durável (Fase 5):** precisa de analytics histórico (migration) ou
  basta log estruturado?
- **Persistência dos contadores:** Redis efêmero (recomendado, sem migration) vs coluna
  durável. Recomendação: Redis para contadores; durável só para analytics.

## 6. Testes (pytest — `FlowEngine` real, sem mock do motor)

- **Fase 1:** turnos no nó atingem o teto → nudge (não handoff); turnos da sessão
  atingem o teto → handoff ao destino do caminho; etapa curta não dispara; o contador
  anti-loop existente continua funcionando (não quebrar `_reformular_ou_handoff`).
- **Fase 2:** `delta > REENGAJAMENTO_HORAS` → mensagem de retomada sem reiniciar;
  `delta > EXPIRA_SESSAO_HORAS` → reinicia preservando perfil (médico/idioma).
- **Fase 3:** lista `debounce:{id}` pré-existente + novo `DebounceManager` → recovery
  processa exatamente uma vez (flush idempotente).
- **Fase 4:** lock renovado/elevado cobre turno simulado longo; sem re-processamento.
- **Fase 5:** evento de turno contém os campos esperados (assert no log).
- **Geral:** `ruff check app/ tests/` limpo; **suíte inteira verde** (hoje ~320).

## 7. Deploy

- Build/push/`service update` de nova tag (operador). Fases 1–4 **sem migration**
  (Redis/estado); só a entidade `Turno` opcional (Fase 5) exige migration.
- Expor os novos envs de limiar em `stack.yml` e `.env.example`.
- **Validação real (WhatsApp, número de teste, com `#reset`):**
  - Forçar muitos turnos numa etapa → confirmar nudge e, no limite, handoff.
  - Parar de responder e voltar depois → confirmar retomada cordial (Fase 2).
  - Simular deploy no meio de uma rajada → confirmar que a resposta não se perde (Fase 3).
  - Repetir 1 caminho em EN/ES.

## 8. Critérios de aceite

- Existe orçamento de turnos por sessão e por nó, com nudge antes de handoff duro.
- Sessões inativas são reconhecidas no retorno e expiram com preservação de perfil.
- Restart no meio do debounce não perde turno.
- Turnos lentos não são re-processados; caminho para multi-réplica documentado.
- Cada turno é observável (log estruturado); limiares ajustáveis por env.
- Mecanismos já existentes (anti-loop, cap de msgs, pacing, gate de fila) **preservados**.
- Suíte verde + lint limpo + validação real confirmada.
