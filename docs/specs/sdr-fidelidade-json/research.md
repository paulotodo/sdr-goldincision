# Research — Contrato JSON, Portão de Fidelidade e Interpretação Agêntica (Onda 2)

Feature: `sdr-fidelidade-json` · Fase: plan · Branch: `feature/sdr-fidelidade-json`
Design primário: `docs/plano-interpretacao-agentica.md` · Constitution: `docs/constitution.md` (v1.0.0)

Este documento registra as decisões técnicas (com alternativas descartadas) que
sustentam `plan.md` e `data-model.md`. As decisões Q1–Q3 já foram ratificadas na
fase clarify (`spec.md` §Clarifications; `state.json` dec-009/dec-010/dec-011).

## Decision 1 — Onde o contrato JSON se acopla (Pilar 6, FR-001..FR-007)

- **Decisão**: envolver a saída de `GroundedResponder.generate()`
  (`app/core/responder.py:165`) num pacote estruturado validado por Pydantic,
  em vez de reescrever a máquina de estados. Hoje `generate()` retorna a 2-tupla
  `(resposta: str, handoff: bool)`; o contrato passa a ser um objeto intermediário
  `RespostaEstruturada {texto, fontes, precisa_handoff, confianca}` produzido via
  `response_format=json_schema` no `openai_model_reasoning` (gpt-4o), e o adapter
  final continua devolvendo a 2-tupla que `flow.py` espera (chamadas em
  `flow.py:1403` e no caminho de licenciamento `flow.py:1409`).
- **Por quê**: preserva a assinatura consumida pela FlowEngine (RESTRIÇÃO
  INVIOLÁVEL — máquina de estados determinística; FR-006 "o pacote apenas informa,
  nunca determina transição"). Blast radius confinado a `responder.py` + um novo
  módulo de contrato.
- **Alternativas descartadas**: (a) fazer `flow.py` consumir o pacote diretamente —
  espalharia lógica de parsing pela máquina de estados e arriscaria o LLM influenciar
  transições; (b) tool-calling em vez de `json_schema` — mais indireção sem ganho,
  o SDK já suporta `response_format`.

## Decision 2 — Portão de Fidelidade: componente e ponto de invocação (Pilar 7, FR-008..FR-012)

- **Decisão**: novo componente `FidelityGate` (`app/core/fidelity.py`) invocado por
  `responder.generate()` DEPOIS de gerar o texto e ANTES de retornar, SOMENTE quando a
  resposta toca "condição comercial" (ver Decision 5). Usa `openai_model_cheap`
  (gpt-4o-mini) para conferir cada afirmação factual do texto gerado contra o
  `knowledge_context` oficial já carregado, devolvendo `VeredictoFidelidade
  {fiel: bool, afirmacoes_nao_sustentadas: list[str]}`.
- **Fail-closed (Edge Case da spec)**: erro/indisponibilidade/timeout do gate ==
  reprovação (`fiel=false`). O sistema NUNCA envia resposta gerada sem confirmação
  positiva. Reprovação → contingência: bloco canônico "informação indisponível" →
  reformulação → handoff.
- **Por quê**: é o único ponto do fluxo sem segunda checagem antes do envio (US1
  "Why this priority"). Reusa `knowledge_context` já carregado em `flow.py`
  (`_load_knowledge_by_slug`), sem novas fontes.
- **Alternativa descartada**: verificar toda resposta gerada (inclusive rapport) —
  custo/latência desnecessários e fora do escopo definido em Q2/dec-010.

## Decision 3 — Roteamento de modelos (RESTRIÇÃO INVIOLÁVEL)

- **Decisão**: `gpt-4o` (openai_model_reasoning) SOMENTE para redação de dúvidas /
  contrato JSON, `temperature` 0–0.2 quando a resposta trata de fatos (FR-004).
  `gpt-4o-mini` (openai_model_cheap) para classificação, extração de slot
  (SlotExtractor) e verificação de fidelidade (FidelityGate).
- **Por quê**: fixado na spec (§Clarifications, modelos) e no design §3.1. Reduz custo
  e latência das chamadas de verificação/extração, que são as mais frequentes.
- **Nota**: `config.py` já expõe `openai_model_reasoning` e `openai_model_cheap`
  (config.py:38,40) — nenhum novo campo de modelo é necessário.

## Decision 4 — SlotExtractor: fast-path primeiro (FR-013..FR-017)

- **Decisão**: novo `SlotExtractor` (`app/core/interpret.py`) com
  `extract(slot_schema, user_message, contexto) -> SlotQualificacao`. O
  reconhecimento determinístico existente roda PRIMEIRO em cada etapa; o LLM só é
  acionado quando o fast-path não resolve com alta certeza (FR-013), e é
  curto-circuitado quando resolve (custo/latência — design §3.1).
- **Threshold (Q1/dec-009)**: limiar único global `SLOT_CONFIDENCE_THRESHOLD=0.6`,
  aplicado às 5 etapas do FR-017 (elegibilidade médica, objetivo/produto, experiência
  corporal prévia, especialidade, escolha de turma). Abaixo do limiar OU extração
  inválida → tratar como "não entendida" → reformular a pergunta (FR-015), nunca
  adivinhar valor.
- **Alternativa descartada**: limiar por etapa — FR-015 usa "um limiar" (singular);
  FR-018 cita "limiares" só para ajuste futuro. Global é o escopo desta onda.

## Decision 5 — Escopo de "condição comercial" (Q2/dec-010, score 3)

- **Decisão**: o portão dispara quando a resposta afirma sobre: preço/valor,
  parcelamento, desconto/promoção, data/prazo, disponibilidade de turma/vaga, e
  elegibilidade médica. Saudações e rapport NÃO passam pelo portão.
- **Fundamentação empírica**: constitution Princípio V (v1.0.0) trata comercial +
  elegibilidade sob o mesmo princípio; FR-008 lista os gatilhos conjuntos.

## Decision 6 — Retry e timeout (Q3/dec-011)

- **Decisão**: retry de pacote JSON malformado = 1 (FR-003, não reaberto); na 2ª
  falha → handoff, nunca conteúdo improvisado. Timeout duro
  `VERIFY_TIMEOUT_SECONDS=3` por chamada de verificação de fidelidade E de
  entendimento assistido (slot-filling); alvo interno ~2s (gpt-4o-mini). Exceder o
  timeout == indisponibilidade == caminho de contingência (fail-closed).

## Decision 7 — Verbatim e blocos canônicos fora do portão (RESTRIÇÃO INVIOLÁVEL)

- **Decisão**: apresentações e textos oficiais saem VERBATIM do DB sem passar pelo
  LLM; o contrato estruturado e o portão de fidelidade só existem para texto livre
  gerado pelo modelo (`responder.generate`), nunca para `generate_menu`,
  `generate_paciente_modelo`, apresentações ou Banco de Objeções.
- **Por quê**: Edge Case da spec ("uma apresentação verbatim nunca é avaliada");
  constitution Princípio II (regra 4: verbatim). Injetar o LLM aí violaria
  anti-alucinação.

## Decision 8 — Anti prompt-injection (SEC-LLM-1 / SEC-LLM-3)

- **Decisão**: a mensagem do lead entra em SlotExtractor e no prompt de redação como
  DADO delimitado, nunca como instrução (SEC-LLM-1). Handoff destino/queueId sempre da
  allowlist/config (`handoff_queue_ids_json`, config.py:55; SEC-LLM-3) — o pacote JSON
  só pode setar `precisa_handoff: bool`, jamais o destino.
- **Por quê**: RESTRIÇÃO INVIOLÁVEL + spec Edge Case (mensagem que tenta se passar por
  instrução não altera destino/elegibilidade/conteúdo).

## Decision 9 — Preservação da Onda 1 e mecanismos anteriores (RESTRIÇÃO INVIOLÁVEL)

- **Decisão**: nenhuma alteração destrutiva em `log_turno`/observabilidade,
  contadores/nudge/handoff de sessão, reengajamento, debounce recovery, lock TTL
  (config.py:97-115), `max_msgs_per_turn=4` (config.py:134), `_Pacer`+429, idempotência,
  gate IA=77, debounce 8s (config.py:91), anti-loop `_MAX_TENTATIVAS=3` (não fundido).
  O log de turno da Onda 1 é o ponto de observabilidade onde os novos eventos
  (veredito de fidelidade, confiança de slot) são registrados de forma aditiva.

## Decision 10 — Testes (RESTRIÇÃO INVIOLÁVEL)

- **Decisão**: FlowEngine REAL nos testes (mock apenas do `OpenAIClient`, nunca do
  motor — padrão já usado em `tests/test_reengajamento.py`, `tests/test_responder.py`).
  Suíte verde + `ruff` limpo ao final. Golden set estendido fica fora do CI padrão
  (`@pytest.mark.golden`, como em `tests/golden/`). Novos envs validados por teste de
  config (padrão da task 1.1.6 da Onda 1).

## Decision 11 — Configuração dos envs novos

- **Decisão**: `slot_confidence_threshold: float = 0.6` e
  `verify_timeout_seconds: int = 3` adicionados a `app/config.py` (Settings), com
  entradas correspondentes em `.env.example` e `stack.yml` (Docker Swarm), seguindo o
  padrão da Onda 1 (tasks 1.1.4/1.1.5). Sem hardcode, sem secrets.
