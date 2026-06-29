# CLAUDE.md — SDR WhatsApp / Consultor Virtual Oficial GoldIncision

Agente SDR consultivo para WhatsApp. Recebe webhooks do ChatMaster (via n8n),
conduz o lead pelos **6 caminhos do Mapa Mestre** com anti-alucinação rígida e
responde pela API oficial do ChatMaster.

## Fonte da verdade (ler ANTES de mexer em fluxo/textos)

`knowledge_base/documentos_agente/`:
- **`MAPA MESTRE DO ATENDIMENTO.docx`** — a jornada (estrutura e ordem dos 6 caminhos). É lei.
- **`REGRAS GERAIS DO AGENTE COMERCIAL GOLDINCISION.docx`** — as 30 regras de conduta.

Em conflito entre código e esses documentos, **os documentos prevalecem** (Regra 30).
Extrair texto: `python3 -c "from docx import Document; [print(p.text) for p in Document('<arquivo>').paragraphs]"`.

## Os 6 caminhos (taxonomia oficial)

1. Curso Online HG · 2. Cursos Presenciais HG (HG Módulo 1 / HG360 SP / HG360 BCN)
· 3. Sistema GoldIncision (Licenciamento / Franquia) · 4. Aluno/suporte · 5. Paciente
modelo (Nídia) · 6. Outro assunto.

## Regras invioláveis (anti-alucinação)

- Responder **só** com a Base Oficial (DB). Lacuna → recusa + handoff (Regras 7-8).
- **Apresentações verbatim** do DB — nunca parafrasear/resumir (FR-010 / Regra 15).
- **Objeções** só do Banco Oficial (Regra 16). FAQ só se não estiver na Base (Regra 17).
- **Elegibilidade médica inflexível** (FR-009 / Regra 20).
- **1 pergunta por mensagem** (exceto menus) · respostas curtas · idioma do lead (PT/EN/ES).
- **Mudança de assunto** redireciona (Regra 10), mas de forma conservadora (ver gotchas).
- **Pergunta direta → resposta direta**: se o lead pergunta preço/conteúdo/duração/
  certificado, responder na hora, **sem** disparar "é médico?" antes (REGRA do Caminho 1;
  decisão do operador 2026-06-29). A qualificação médica gateia o **fechamento** (link),
  não a resposta a uma dúvida.

## Arquitetura

- `app/core/flow.py` — **máquina de estados** dos 6 caminhos (`FlowEngine.process`). Estado
  em `ticket.etapa_mapa_mestre`. Textos determinísticos i18n no dict **`_T`** + helper `_t()`.
- `app/core/responder.py` — `GroundedResponder.generate` (LLM) usado **só nas fases de
  DÚVIDAS**, com grounding estrito. Dispatch por **slug** (`_SLUG_PROMPTS`).
- `app/core/intent.py` — classificação de intenção + idioma.
- `app/core/memory.py` — `SessionContext`, histórico (Postgres + janela quente Redis).
- `app/api/webhook.py` — orquestra: `process()` → persiste updates → envia via ChatMaster →
  `transfer_ticket(destination=...)` no handoff (destino lógico via `FlowResult.handoff_destino`).
- Apresentações são enviadas **verbatim do DB** (determinístico), não pelo LLM.

## Convenções de código

- Python 3.12. `ruff check app/ tests/` deve passar (config em `pyproject.toml`; E,F,W,I; E501 off).
- Detectores de NLU (`_detectar_*`): **matching por palavra inteira** (`_norm` remove acentos e
  pontuação). Cuidado com substrings frágeis — em PT, `no`/`na` são contrações ("em o"), **não**
  negação; nunca tratá-los como "não".
- Textos novos de UI → `_T`/`_t` (não criar blocos `if idioma==...`).

## Testes (obrigatório antes de qualquer mudança de fluxo)

- `python3 -m pytest -q` — suíte **inteira verde** (atualmente 284).
- Testes de fluxo usam o **`FlowEngine` REAL** via `StubFlowEngine` (stuba só I/O de DB).
  **Não** reintroduzir um mock que reimplemente `process()`.
- Toda correção de bug de NLU/jornada deve vir com teste de regressão.

## Deploy (Docker Swarm — produção)

- Build/push: `./scripts/build-push.sh <tag>` → `registry.todo-tips.com/sdr-whatsapp:<tag>`.
- Deploy: `docker service update --image registry.todo-tips.com/sdr-whatsapp:<tag> --force --with-registry-auth sdr-whatsapp_app`.
- Rollback: `docker service update --rollback sdr-whatsapp_app`.
- Secrets no Swarm: `openai_api_key`, `chatmaster_token`, `admin_token`, `webhook_token`.
- Filas de handoff: `HANDOFF_QUEUE_IDS_JSON` mapeia destinos lógicos → queueId
  (`consultores`, `presencial`, `licenciamento`, `franquia`, `suporte`, `especialista`);
  fallback `HANDOFF_QUEUE_ID_DEFAULT`.
- **Validar de verdade** após deploy: WhatsApp com `#reset` por cenário (nº de teste autorizado),
  cobrindo os 6 caminhos e os gates de elegibilidade.

## Gotchas

- `IntentClassifier.classify` retorna **2-tupla** `(intencao, idioma)` — vários testes mockam
  esse contrato. Não mudar para 3-tupla. Confiança baixa já é rebaixada a AMBIGUA.
- Troca de caminho **conservadora**: não reinicia a jornada enquanto a etapa aguarda resposta
  (`_ETAPAS_AGUARDANDO_RESPOSTA`).
- Contador anti-loop em `Contato.etapa_funil` (JSON `{"et","n"}`); reformula na 2ª tentativa,
  encaminha a humano na 3ª. Limpo pelo `#reset`.
- `master` é protegido por ruleset (exige o check de CI **"Lint + Testes (pytest)"**): mudanças
  entram por **PR**, não por push direto.
