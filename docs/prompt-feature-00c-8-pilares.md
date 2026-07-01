# Prompts /feature-00c — Implementar os 8 pilares de confiabilidade (integral)

> **Uso:** cada bloco "PROMPT PARA /feature-00c" abaixo é autocontido e pronto para
> colar numa **sessão fresca** invocando `/feature-00c`. Entrega **faseada em 3 ondas
> por risco**; rodar uma onda por vez, validar em produção, então a próxima. O projeto
> vive em `/root/sdr-goldincision`. Fonte da avaliação: skill
> `agente-atendimento-confiavel` (8 pilares) + auditoria verificada em código.

---

## Mapa do programa

| Pilar | Estado hoje (verificado) | Onda que fecha |
|---|---|---|
| 1. Máquina de estados fora do prompt | ✅ Pronto (`FlowEngine`, `Ticket.etapa_mapa_mestre`) | — |
| 2. Estado estruturado | ✅ Pronto (`Contato` + `perfil` JSONB) | — |
| 3. Memória em camadas | ✅ Pronto (hot window + resumo rolante) | — |
| 7. Controle de turnos | ⚠️ Parcial — gaps G1–G6 | **Onda 1** |
| 8. Observabilidade e avaliação | ⚠️ Parcial — sem registro por-turno / golden set | **Onda 1** |
| 5. Portão de verificação de fidelidade | ❌ Ausente | **Onda 2** |
| 6. Contrato JSON + guardrails | ⚠️ Parcial — responder retorna texto livre | **Onda 2** |
| (extra) Interpretação agêntica (slot-filling) | 📄 Planejado (`docs/plano-interpretacao-agentica.md`) | **Onda 2** |
| 4. Recuperação ancorada (RAG híbrido) | ⚠️ Parcial — verbatim ✅, sem busca/limiar/abstenção | **Onda 3** |

**Sequência:** Onda 1 (turnos + observabilidade) → Onda 2 (verificação + contrato JSON +
interpretação agêntica) → Onda 3 (RAG híbrido pgvector). A observabilidade da Onda 1
(golden set) é pré-requisito para calibrar os limiares das Ondas 2 e 3.

---

## Restrições invioláveis (valem para TODAS as ondas)

Copie mentalmente estas guardas em cada onda — o `/feature-00c` deve preservá-las:

- **Fonte da verdade**: `knowledge_base/documentos_agente/` (`MAPA MESTRE DO ATENDIMENTO.docx`,
  `REGRAS GERAIS DO AGENTE COMERCIAL GOLDINCISION.docx`). Em conflito código×documento,
  **o documento prevalece** (Regra 30).
- **Anti-alucinação**: responder só com a Base Oficial; lacuna → recusa + handoff.
- **Apresentações verbatim** (FR-010/Regra 15): saem do DB **sem passar pelo LLM**. Nunca
  parafrasear. RAG/verificação/contrato JSON **não** se aplicam aos blocos canônicos.
- **Handoff**: o destino lógico e o `queueId` vêm **sempre** da allowlist/config
  (SEC-LLM-3) — o LLM **nunca** fornece destino/queueId.
- **Mensagem do lead = dado não-confiável** (SEC-LLM-1): tratar como conteúdo, não
  instrução; guardas contra prompt injection em todo prompt novo.
- **Elegibilidade médica inflexível** (FR-009/Regra 20); **1 pergunta por mensagem**
  (exceto menus); **idioma do lead** PT/EN/ES.
- **Stack**: Python 3.12 · FastAPI · Postgres 16 · Redis 7 · OpenAI (`gpt-4o` raciocínio,
  `gpt-4o-mini` classificação/extração/verificação) · Docker Swarm
  (`registry.todo-tips.com/sdr-whatsapp`). Persistência própria da stack.
- **Testes**: `FlowEngine` **real** nos testes (o `StubFlowEngine` stuba só I/O de DB —
  **não** reintroduzir mock do motor); toda correção com teste de regressão; **suíte
  inteira verde** (hoje ~320) + `ruff check app/ tests/` limpo.
- **Migrations** rodam no startup (`alembic upgrade head`); seeds pesados (ex.: embeddings,
  traduções) são gerados **UMA vez** e salvos como dado revisável (padrão do
  `faq_i18n.json`), nunca recomputados a cada boot.
- **Fluxo de entrega**: `master` é protegido (CI **Lint + Testes (pytest)** obrigatório) →
  mudanças por **PR**, não push direto. Deploy pelo operador (`build-push.sh` + `service
  update`); validação real por WhatsApp com `#reset` (número de teste), cobrindo os 6
  caminhos e os gates de elegibilidade, em PT/EN/ES.
- **Docs de referência já no repo** (ler antes de planejar): `docs/plano-controle-turnos.md`,
  `docs/plano-interpretacao-agentica.md`, `docs/constitution.md`, `docs/specs/sdr-whatsapp/`,
  `CLAUDE.md`.

---

## PROMPT PARA /feature-00c — ONDA 1 (Pilar 7 + Pilar 8)

> short-name sugerido: `sdr-turnos-obs`

```
Feature: Controle de turnos robusto + observabilidade de turno para o agente SDR
(Consultor Virtual GoldIncision). Projeto em /root/sdr-goldincision. Antes de planejar,
LEIA docs/plano-controle-turnos.md (é o design detalhado desta feature, com refs
arquivo:linha), CLAUDE.md e docs/constitution.md.

OBJETIVO: fechar os gaps G1–G6 de controle de turnos (Pilar 7 da metodologia de agente
confiável) e tornar cada turno observável (Pilar 8), SEM refazer o que já existe.

JÁ EXISTE E DEVE SER PRESERVADO (não reimplementar): contador anti-loop por etapa
(_MAX_TENTATIVAS=3, _reformular_ou_handoff em flow.py); teto de mensagens do bot por
turno (max_msgs_per_turn=4 aplicado em chatmaster.send_message_blocks); pacing (_Pacer)
e retry 429 honrando Retry-After; idempotência (SET NX EX); lock por ticket (SET NX PX);
gate de fila (IA=77); debounce de rajada de entrada (8s).

REQUISITOS FUNCIONAIS:
1. Orçamento de turnos (G1) — sem migration: em Redis estado:{chamadoId}, manter
   turnos_sessao e turnos_no_no:{etapa}, incrementados 1x por turno em
   _process_consolidated_messages/engine.process. Envs:
   - MAX_TURNOS_NO_NO (default 6): ao atingir, o nó faz um NUDGE contextual
     ("Quer que eu te conecte com um especialista?") — não handoff; o lead pode seguir.
   - MAX_TURNOS_SESSAO (default 25): teto de segurança → handoff cordial ao destino
     lógico do caminho atual (allowlist/config; nunca do LLM).
   - ETAPA_DUVIDAS é legitimamente aberta: usar limiar próprio maior (default 12) para
     nudge; nunca cortar dúvida legítima. Registrar motivo (turnos_no_no/turnos_sessao).
   Este contador é de TURNOS (reconhecidos ou não) e coexiste com o anti-loop de
   respostas-não-reconhecidas por etapa (que zera ao avançar). Não fundir os dois.
2. Timeout de inatividade + reengajamento (G2) — sem migration: gravar ultima_interacao
   (timestamp) no estado Redis a cada turno; detecção LAZY no início de engine.process:
   - delta > REENGAJAMENTO_HORAS (default 24) e sessão em fluxo → retomada cordial
     ("Oi! Retomando de onde paramos…") sem reiniciar a jornada.
   - delta > EXPIRA_SESSAO_HORAS (default 72) → tratar como sessão nova (etapa→saudação),
     PRESERVANDO o perfil do Contato (médico, idioma, especialidade). Não re-perguntar
     o que já sabemos.
3. Durabilidade do debounce (G3): no lifespan do startup (main.py), após conectar o
   Redis, SCAN debounce:* e, para cada lista pendente, reagendar o flush (ou flush
   imediato se a janela já passou). O flush é atômico (LRANGE+DEL) → idempotente. Evita
   perder turno órfão quando o processo reinicia dentro da janela de 8s.
4. Robustez de concorrência (G4): elevar o TTL do lock por ticket para cobrir o pior
   caso de turno (LLM + até 4 envios paced + retries) — medir a duração real via a
   observabilidade do RF#6 e fixar (sugestão inicial: 90s), OU renovar o PTTL durante o
   processamento. Documentar que o pacing distribuído (G5) só é necessário ao escalar
   >1 réplica (hoje 1) — NÃO implementar agora, apenas registrar como pré-condição.
5. Observabilidade de turno (Pilar 8): emitir 1 evento JSON por turno em
   _process_consolidated_messages, via observability/log.py, com {chamado_id,
   turno_sessao, etapa_entrada, etapa_saida, intencao, idioma, n_blocos_enviados, acao,
   handoff_destino, duracao_ms, tentativas}. Base para calibrar limiares.
6. Golden set de avaliação (Pilar 8): criar tests/golden/ com 30–50 casos reais
   (derivar dos cenários de #reset e dos 6 caminhos) no formato da skill
   (agente-atendimento-confiavel/padroes-implementacao.md §7): {mensagem, estado_inicial,
   esperado:{proxima_acao, etapa, nao_repetir_slot...}}. Um harness pytest roda o
   FlowEngine REAL sobre os casos e mede: fluxo correto (não repete slot preenchido, não
   pula etapa), abstenção correta (recusa quando não há base), zero preço inventado.
   Marcar como suite separada (não bloquear o CI se ainda instável; documentar como rodar).

NÃO-OBJETIVOS desta onda: RAG vetorial, portão de verificação, contrato JSON (Ondas 2 e 3).

DECISÕES/DEFAULTS (o clarify pode confirmar; começar conservador e ajustar por telemetria):
MAX_TURNOS_NO_NO=6, MAX_TURNOS_SESSAO=25, limiar dúvidas=12, REENGAJAMENTO_HORAS=24,
EXPIRA_SESSAO_HORAS=72, lock=90s. Contadores em Redis (efêmeros, sem migration);
entidade Turno durável só se o operador quiser analytics histórico (então migration
simples Turno(id,ticket_id,seq,etapa,intencao,acao,ts)) — default: NÃO criar, usar log.

RESTRIÇÕES: preservar todos os mecanismos existentes; anti-alucinação e verbatim
intactos; handoff destino sempre da config; suíte verde + ruff limpo; expor os novos
envs em stack.yml e .env.example.

CRITÉRIOS DE ACEITE: tetos disparam nudge (nó) e handoff (sessão); retorno após horas é
reconhecido e sessão antiga reinicia preservando perfil; restart no meio do debounce não
perde turno; turno lento não é re-processado; cada turno é logado; golden set roda e
reporta métricas; validação real por WhatsApp (#reset) confirmada em PT + 1 caminho EN/ES.
```

---

## PROMPT PARA /feature-00c — ONDA 2 (Pilar 5 + Pilar 6 + interpretação agêntica)

> short-name sugerido: `sdr-fidelidade-json`. Rodar após a Onda 1 em produção.

```
Feature: Contrato JSON de saída + portão de verificação de fidelidade + interpretação
agêntica (slot-filling por etapa) para o agente SDR GoldIncision. Projeto em
/root/sdr-goldincision. LEIA docs/plano-interpretacao-agentica.md (design do slot-filling,
com refs), CLAUDE.md e docs/constitution.md antes de planejar.

OBJETIVO: (a) fazer o LLM responder em contrato JSON validável (Pilar 6); (b) verificar
a fidelidade das respostas GERADAS antes de enviar (Pilar 5); (c) tornar o entendimento
das respostas do lead fluido via slot-filling agêntico por etapa. Tudo sem dar poder de
DECISÃO de fluxo ao LLM — a máquina de estados continua determinística.

REQUISITOS FUNCIONAIS:
1. Contrato JSON (Pilar 6): GroundedResponder.generate passa a retornar JSON validado
   por Pydantic — {resposta, fonte_ids, precisa_humano, confianca} — usando
   response_format=json_schema no gpt-4o. Temperatura 0–0.2 nas etapas factuais. JSON
   malformado → 1 retry com instrução de correção → se falhar, handoff (nunca improvisar).
   Checar que o idioma da resposta bate com o slot de idioma. O roteamento de fluxo
   permanece no código (o JSON informa o que o modelo entendeu/redigiu, não decide o nó).
2. Portão de verificação de fidelidade (Pilar 5): antes de enviar qualquer resposta
   GERADA que toque preço/data/elegibilidade/condição comercial, um verificador barato
   (gpt-4o-mini) confere se toda afirmação factual está sustentada pelo contexto
   fornecido (chunks/base) — prompt em agente-atendimento-confiavel/padroes-implementacao.md
   §5, saída JSON {fiel, afirmacoes_nao_sustentadas}. fiel=false → NÃO envia: cai no bloco
   canônico correspondente ou handoff. As APRESENTAÇÕES VERBATIM continuam fora do LLM e
   NÃO passam pelo portão (já são seguras). O portão cobre só as dúvidas em texto livre.
3. Interpretação agêntica / slot-filling (docs/plano-interpretacao-agentica.md): novo
   app/core/interpret.py com SlotExtractor.extract(slot_schema, user_message, contexto)
   via gpt-4o-mini + response_format=json_schema. Padrão por etapa:
   - FAST-PATH determinístico primeiro (regex/keyword/número já existentes) — se casar com
     alta certeza, decide na hora, ZERO LLM.
   - FALLBACK agêntico só quando o fast-path não resolve: extrai o slot (ex.:
     {eh_medico: true|false|null, confianca}, {objetivo}, {experiencia_corporal},
     {especialidade}, {escolha_turma}), com limiar de confiança (~0.6). Slot inválido ou
     confiança baixa → trata como "não entendido" (reformula) — NUNCA inventa slot.
   - Reusa o perfil conhecido (_perfil_conhecido) e o histórico como contexto para
     desambiguar. Fase A: eh_medico, objetivo_sistema, experiencia_corporal. Fase B:
     especialidade, escolha_turma, fechamento. Fase C: telemetria (logar quando o LLM
     "salvou" uma resposta que o regex não pegava) + afinar limiares.
4. Guardas de prompt injection (SEC-LLM-1): a mensagem do lead entra como DADO delimitado;
   instrução de sistema robusta; a mensagem nunca altera destino de handoff, regra de
   elegibilidade nem faz o modelo revelar/ignorar instruções.

NÃO-OBJETIVOS: RAG vetorial (Onda 3). Mudar a estrutura da jornada/Mapa Mestre.

DEPENDÊNCIAS: usa a observabilidade de turno da Onda 1 para medir taxa de fallback,
verificação reprovada e "salvamentos" do slot-filling. Adiciona campos ao golden set
(casos de abstenção e de preço-fora-da-base) e mede groundedness/fidelidade por caso.

RESTRIÇÕES: verbatim intacto; handoff da config (SEC-LLM-3); fast-path antes do LLM
(custo/latência); fallback só quando necessário; suíte verde (FlowEngine real) + ruff
limpo; expor envs (limiar de confiança, modelo do extrator/verificador) em config +
stack.yml + .env.example.

CRITÉRIOS DE ACEITE: respostas geradas saem em JSON validado; verificação bloqueia preço/
data/elegibilidade não-sustentados (fallback/handoff, nunca envia errado); respostas
naturais do lead são entendidas na maioria das etapas sem cair em "não entendi"; script
determinístico e segurança preservados; validação real WhatsApp (#reset) PT + EN/ES.
```

---

## PROMPT PARA /feature-00c — ONDA 3 (Pilar 4: RAG híbrido completo)

> short-name sugerido: `sdr-rag-hibrido`. Rodar após a Onda 2 em produção.

```
Feature: Recuperação ancorada com RAG híbrido (pgvector + full-text + rerank + limiar +
abstenção) para as respostas em texto livre (FAQ/objeções/dúvidas) do agente SDR
GoldIncision. Projeto em /root/sdr-goldincision. LEIA CLAUDE.md, docs/constitution.md e
o padrão em agente-atendimento-confiavel/padroes-implementacao.md §4.

OBJETIVO: substituir o grounding montado manualmente (flow.py) por uma recuperação
híbrida ancorada, com pré-filtro por metadados, reranking, limiar de relevância e
ABSTENÇÃO forçada (sem fonte boa → não responde: recusa + handoff). As APRESENTAÇÕES,
PREÇOS e LINKS oficiais continuam blocos canônicos verbatim do DB — RAG é só para as
respostas livres.

REQUISITOS FUNCIONAIS:
1. Infra (Postgres 16): migration Alembic CREATE EXTENSION IF NOT EXISTS vector; tabela
   chunk(id, conteudo, embedding vector(1536), produto, tipo[objecao|faq|base|licenciamento],
   idioma, fonte_doc, tsvector gerado). Índice HNSW no embedding + índice GIN no tsvector.
2. Chunking por unidade semântica (não por tamanho fixo): 1 objeção = 1 chunk, 1 entrada de
   FAQ = 1 chunk, 1 seção coerente = 1 chunk, com metadados {produto, tipo, idioma,
   fonte_doc}. Reusa os parsers já existentes de knowledge_base (FAQ, objeções, licenciamento).
3. Embeddings: text-embedding-3-small, gerados UMA vez no seed e persistidos (revisável,
   como o faq_i18n.json) — NÃO recomputar a cada startup. Seed idempotente; re-embedar só
   quando o conteúdo/versão muda.
4. Recuperação híbrida: busca_vetorial(k=20) + busca_textual/BM25(k=20) → reciprocal rank
   fusion → reranking (cross-encoder/BGE reranker ou, se preferir simplicidade, rerank por
   score combinado) → top-5. PRÉ-FILTRAR por metadados (produto em contexto, idioma) ANTES
   da etapa vetorial — impede aplicar objeção do produto A a lead do produto B.
5. Limiar + abstenção: se o melhor score < LIMIAR (calibrar com o golden set das Ondas 1/2;
   começar ~0.45) → ABSTER: mensagem padrão "não tenho essa informação" + handoff. Nunca
   responder sem fonte acima do limiar.
6. Integração: os chunks recuperados viram o grounding do GroundedResponder; os fonte_ids
   do contrato JSON (Onda 2) passam a apontar os chunk.id; o portão de verificação (Onda 2)
   recebe esses chunks como CONTEXTO. Atribuição/rastreio (Pilar 4d) em todo turno de dúvida.
7. Custo/latência: cache de embeddings de consulta; semantic cache opcional para perguntas
   repetidas; medir latência p95 e custo por turno via a observabilidade da Onda 1.

NÃO-OBJETIVOS: mudar apresentações verbatim (continuam fora do RAG); reescrever a jornada.

RESTRIÇÕES: verbatim/anti-alucinação/handoff-da-config intactos; abstenção é obrigatória
abaixo do limiar; seed de embeddings idempotente e revisável; migration roda no startup;
suíte verde (FlowEngine real) + ruff limpo; expor envs (modelo de embedding, LIMIAR, k) em
config + stack.yml + .env.example.

CRITÉRIOS DE ACEITE: FAQ/objeções respondidas por recuperação ancorada com metadados;
objeção do produto errado não vaza; abaixo do limiar o agente se abstém e faz handoff (não
inventa); groundedness do golden set melhora vs baseline; zero preço/data inventados;
validação real WhatsApp (#reset) PT + EN/ES, incluindo uma pergunta fora da base (deve
abster) e uma objeção (deve usar o banco oficial).
```

---

## Notas de engenharia (transversais)

- **Roteamento de modelos / custo**: `gpt-4o` só para redação de dúvidas (contrato JSON);
  `gpt-4o-mini` para classificação, extração de slot e verificação; `text-embedding-3-small`
  para RAG. Fast-path determinístico antes de qualquer chamada. Medir custo/latência por
  turno desde a Onda 1 (observabilidade) para decidir trade-offs com dado.
- **Rollout escalonado** (toda onda): merge por PR (CI verde) → deploy nova tag → validar
  primeiro nos **números de teste** (`#reset`, canário) → só então tráfego real. Rollback
  via `docker service update --rollback`.
- **Segurança/governança**: prompt-injection guard (SEC-LLM-1) em todo prompt novo; destino
  de handoff sempre da allowlist (SEC-LLM-3); avaliar redigir PII nos logs de turno; a
  mensagem do lead nunca vira instrução.
- **Avaliação contínua**: o golden set (Onda 1) é o instrumento que calibra os limiares das
  Ondas 2 (confiança do slot/verificação) e 3 (limiar de recuperação) — rodar a cada
  mudança de prompt/base e acompanhar groundedness, abstenção correta e fluxo correto.
- **Decisões de produto pendentes** (o clarify do /feature-00c deve confirmar): valores dos
  limiares/tetos; se "dúvidas" tem teto de turnos; encerramento proativo (job) vs detecção
  lazy; entidade `Turno` durável; reranker (modelo dedicado vs score combinado).
```
