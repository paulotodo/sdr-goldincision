# Implementation Plan: Agente SDR Consultivo WhatsApp — GoldIncision

**Feature**: `sdr-whatsapp` | **Date**: 2026-06-28 | **Spec**: [spec.md](./spec.md)

## Summary

Construir o Consultor Virtual Oficial da GoldIncision: um serviço FastAPI
(Python 3.12) que recebe webhooks do ChatMaster via n8n, conduz o lead pelos 6
caminhos do Mapa Mestre com anti-alucinação rígida (Base Oficial como única
fonte), responde via API oficial do ChatMaster e faz handoff humano quando o
fluxo determina. Memória durável em Postgres próprio (histórico completo + resumo
rolante + variáveis de qualificação) e estado quente/efêmero em Redis próprio
(debounce de rajada, locks de serialização por ticket, idempotência de evento).
Catálogo de cursos é DADO — gerido por API REST de admin (CRUD protegido por
token) e lido em runtime sem redeploy; seed inicial derivado dos documentos
oficiais. LLM OpenAI com dois modelos (raciocínio para o fluxo + barato para
classificação/sumarização/idioma) e transcrição de áudio. Empacotado como stack
Docker Swarm autocontida (app + Postgres + Redis em overlay própria; só `app`
ingressa na rede do Traefik, e **apenas** para `/admin/*` e `/health` — o webhook
de entrada NÃO é exposto via Traefik, chegando pela overlay interna compartilhada
com o n8n), espelhando `fast-api-homologacao`; a entrega vai até
build + push no `registry.todo-tips.com` e geração de `stack.yml` revisável — SEM
`docker stack deploy` live.

Abordagem técnica resolvida na pesquisa (ver [research.md](./research.md), 12
decisões; 0 NEEDS CLARIFICATION de design).

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: FastAPI + Uvicorn; Pydantic v2; SQLAlchemy 2.0 (async,
`asyncpg`) + Alembic; `redis.asyncio`; cliente OpenAI (chat + transcrição);
`httpx` (chamadas ChatMaster). (Decisões 1, 3, 4 de research.md)
**Storage**: PostgreSQL 16 (próprio, durável) + Redis 7 (próprio, efêmero) —
ambos isolados em overlay própria (Decisão 2)
**Testing**: pytest + pytest-asyncio; Postgres/Redis efêmeros (testcontainers/CI);
OpenAI e ChatMaster mockados + cenário roundtrip real local (Decisão 12)
**Target Platform**: Docker Swarm (nó manager existente, Docker 29.x), atrás de
Traefik v3.5.3; imagem no `registry.todo-tips.com` (Decisão 9)
**Project Type**: web-service (backend API + workers de background, single-layer
backend; sem frontend nesta feature)
**Performance Goals**: resposta a intenção clara < 15s (SC-001); menu < 10s
(US1-AS1); healthcheck < 3s (US6-AS3); debounce default 8s configurável
**Constraints**: isolamento absoluto (não tocar serviços/stores compartilhados —
Princípio VI); secrets nunca em git (FR-032); anti-alucinação MUST (Princípio II);
sem deploy live (FR-031)
**Scale/Scope**: volume de leads de WhatsApp de uma operação comercial; conversas
de 50+ mensagens (SC-002); rajadas de até 5 msgs (SC-005); 6 cursos no seed,
extensível via admin sem redeploy

## Constitution Check

*GATE: Deve passar antes do Phase 0. Re-checado após Phase 1 — ver §Re-check.*

| Princípio | Status | Notas |
|-----------|--------|-------|
| I. Fidelidade ao Fluxo Oficial (Mapa Mestre) | PASS | `internal-flow.md` mapeia os 6 caminhos, identificação de intenção primeiro, menu só quando não-clara, redirecionamento em mudança de assunto; roteamento rastreável ao documento oficial (FR-006, FR-007) |
| II. Anti-Alucinação Rígida | PASS | grounding estrito com hierarquia fixa (Mapa Mestre→Base→Objeções→FAQ), apresentações verbatim (FR-010), recusa fora da base + handoff, guarda de cobertura (Decisão 5, SC-008) |
| III. Memória e Jornada Sem Atrito | PASS | Postgres (histórico durável) + resumo rolante + Redis (janela quente) + variáveis persistidas; nunca repete perguntas (FR-018..021, Decisão 8) |
| IV. Comunicação Consultiva Premium | PASS | blocos curtos, uma pergunta/msg (FR-015), idioma do lead, variante de material correta (FR-012) |
| V. Elegibilidade, Objeções e Handoff Disciplinados | PASS | elegibilidade inflexível (FR-009), objeções só do Banco Oficial (FR-011), handoff via API de tickets + Nídia para paciente modelo (FR-022/014); identidade "Consultor Virtual" (FR-013) |
| VI. Isolamento e Segurança de Infraestrutura | PASS | stack autocontida em overlay própria; só `app` na rede do Traefik e somente para `/admin/*`+`/health`; webhook por overlay interna compartilhada com o n8n (sem rota pública), atachando SOMENTE o nosso serviço (n8n nunca modificado); stores não expostos (FR-028/029); secrets via Docker secrets (FR-032); build+push sem deploy live (FR-031) |
| VII. Cursos como Dados (sem redeploy) | PASS | catálogo em Postgres, CRUD admin por token, leitura runtime, seed dos documentos oficiais (FR-025/026/027, Decisão 7) |

**Resultado**: PASS em todos os princípios MUST. Nenhuma violação → prosseguir.

## Project Structure

### Documentation (this feature)

```
docs/specs/sdr-whatsapp/
├── spec.md            # existente
├── plan.md            # este arquivo
├── research.md        # Phase 0 (12 decisões)
├── data-model.md      # Phase 1 (entidades Postgres + Redis)
├── quickstart.md      # Phase 1 (14 cenários)
└── contracts/         # Phase 1
    ├── webhook-inbound.md
    ├── outbound-chatmaster.md
    ├── admin-courses.md
    └── internal-flow.md
```

### Source Code (repository root)

Estado atual do repo (verificado): apenas `docs/` e `knowledge_base/` existem. A
árvore abaixo é a estrutura PROPOSTA a ser criada em `/execute-task` (espelha
`fast-api-homologacao`):

```
/root/sdr-goldincision/
├── knowledge_base/                 # EXISTENTE (documentos oficiais + exemplos webhook)
├── docs/                           # EXISTENTE
├── app/                            # PROPOSTO — código da aplicação
│   ├── main.py                     # FastAPI app + healthcheck + rotas
│   ├── config.py                   # settings (env/secrets)
│   ├── api/
│   │   ├── webhook.py              # POST /webhook/chatmaster
│   │   └── admin.py                # /admin/cursos CRUD
│   ├── core/
│   │   ├── flow.py                 # motor Mapa Mestre (orquestrador)
│   │   ├── intent.py               # classificação/idioma (modelo barato)
│   │   ├── responder.py            # geração de resposta (modelo raciocínio)
│   │   ├── debounce.py             # janela + consolidação (Redis)
│   │   ├── idempotency.py          # chave de evento (Redis)
│   │   ├── locks.py                # lock por ticket (Redis)
│   │   └── memory.py               # histórico + resumo rolante + variáveis
│   ├── integrations/
│   │   ├── openai_client.py        # chat + transcrição
│   │   ├── chatmaster.py           # outbound + tickets/handoff
│   │   └── media.py                # download de mídia (object.sp2.eveo.com.br)
│   ├── repository/
│   │   ├── models.py               # SQLAlchemy models
│   │   └── mapper.py               # DB(snake_case) ↔ DTO(camelCase)
│   ├── schemas/                    # Pydantic DTOs (camelCase)
│   ├── observability/log.py        # logs JSON estruturados
│   └── seed.py                     # seed idempotente dos 6 cursos
├── migrations/                     # PROPOSTO — Alembic
├── tests/                          # PROPOSTO — pytest
├── Dockerfile                      # PROPOSTO — multi-stage, non-root, healthcheck
├── stack.yml                       # PROPOSTO — Swarm (app+postgres+redis); app em 2 redes:
│                                   #   overlay própria (stores) + overlay compartilhada com n8n
│                                   #   (webhook interno); labels Traefik SÓ p/ /admin/* e /health
├── .env.example                    # PROPOSTO
├── pyproject.toml                  # PROPOSTO
└── README.md                       # PROPOSTO
```

**Structure Decision**: backend single-layer estilo `fast-api-homologacao`,
camadas `api` (transporte) → `core` (lógica de fluxo) → `integrations` (APIs
externas) → `repository` (persistência). Sem frontend nesta feature. Background
tasks do FastAPI processam debounce/LLM/envio (sem fila externa — Decisão 6).

## Convenções de Borda

A feature cruza fronteiras DB ↔ backend ↔ APIs externas. Fontes da verdade:

| Camada | Case style | Validação | Fonte da verdade |
|--------|------------|-----------|------------------|
| DB columns (PostgreSQL) | snake_case | Alembic migrations + CHECK constraints | `migrations/*.py` + `app/repository/models.py` |
| Backend DTO interno (Python) | camelCase nos schemas expostos; snake_case nos models | Pydantic v2 | `app/schemas/*.py` |
| API admin payload (request/response) | camelCase | Pydantic (request) + serialização (response) | `contracts/admin-courses.md` |
| Webhook inbound (ChatMaster) | shape EXTERNO fixo (`mensagem`,`sender`,`chamadoId`,`ticketData`,`mediaType`...) | Pydantic tolerante (`extra=ignore`) | `contracts/webhook-inbound.md` + `knowledge_base/example_webhook_json/` |
| Outbound ChatMaster | shape EXTERNO fixo (`{number,text}`, Bearer) | conforme doc oficial | `contracts/outbound-chatmaster.md` |
| Chaves Redis | snake/colon (`lock:ticket:{id}`) | convenção de prefixo | `data-model.md §Estruturas Redis` |

**Mapper layer (DB ↔ DTO)**: `app/repository/mapper.py` — converte models
SQLAlchemy (snake_case) ↔ DTOs Pydantic camelCase. ORM auto-mapping: NÃO para a
borda externa (mapper explícito, evita drift snake/camel — risco documentado pela
skill /plan).

**Validação Pydantic**: request da admin API e payload do webhook validados na
borda de entrada; o webhook usa parsing tolerante (`extra=ignore`) porque o shape
do ChatMaster traz muitos campos não usados. Roundtrip real dos exemplos
(`quickstart.md` Cenário 13) confirma que nenhum campo obrigatório se perde por
divergência de nome — guarda anti-drift de contrato.

## Re-check de Constitution (pós Phase 1)

Revalidado após o design (data-model + contracts):
- Nenhum 4º serviço introduzido (app/postgres/redis = 3; background tasks no
  próprio app, sem broker — Decisão 6). Isolamento (Princípio VI) preservado.
- Catálogo como dados (Princípio VII) reforçado por tabelas + admin API + seed
  idempotente; nada de curso hardcoded.
- Anti-alucinação (Princípio II) materializada no contrato do LLM com grounding
  estrito e guarda de cobertura.
- **Resultado**: PASS mantido. Complexity Tracking não aplicável (sem violações).

## Complexity Tracking

> Sem violações de constitution. Nenhuma complexidade adicional a justificar.

| Violação | Por Que Necessário | Alternativa Simples Rejeitada Porque |
|----------|-------------------|--------------------------------------|
| (nenhuma) | — | — |

## Considerações de Segurança (gate OWASP — Phase 1)

Auditoria OWASP/ASVS da arquitetura proposta (detalhes nos contratos). Controles
incorporados ao design nesta onda. O único item HIGH (SEC-WH-1), escalado ao
operador (block-001), foi RESOLVIDO por isolamento de rede — gate sem
findings HIGH/critical em aberto.

| ID | Risco (OWASP) | Severidade | Status no plano |
|----|----------------|------------|-----------------|
| SEC-WH-1 | Webhook sem autenticação de origem (API2/A07) → forja de eventos, envio a números arbitrários, custo OpenAI | ~~HIGH~~ → **RESOLVIDO** | **Decisão do operador (block-001)**: webhook NÃO exposto via Traefik; n8n posta direto pela overlay interna compartilhada. Sem rota pública = sem URL a forjar. Defesa em profundidade opcional: header `X-Webhook-Token` (tempo constante). Atacha-se SOMENTE o nosso serviço à overlay; n8n nunca modificado. |
| SEC-WH-2 | SSRF no download de mídia via `mediaUrl` (API7) | MEDIUM | Resolvido no design — allowlist de host + bloqueio de IP privado/metadata |
| SEC-WH-3 | Rate limiting / consumo ilimitado (LLM10/API4) | MEDIUM | Resolvido no design — limite por origem + teto de gasto LLM |
| SEC-LLM-1 | Prompt injection (direta/indireta) (LLM01/ASI01) | MEDIUM | Resolvido — separação estrutural sistema/usuário; conteúdo do lead não confiável |
| SEC-LLM-3 | Handoff target via texto livre do LLM (ASI02) | MEDIUM | Resolvido — destino resolvido por allowlist de filas |
| SEC-ADM-1/2 | Token admin: timing attack + brute-force (API2/A04) | MEDIUM | Resolvido — comparação tempo-constante + rate limiting |
| SEC-ADM-4 | Mass assignment na admin API (API3/BOPLA) | LOW-MEDIUM | Resolvido — Pydantic estrito, sem campos cliente-controlados |
| A05 SQLi | Injeção SQL | LOW | Mitigado por design — SQLAlchemy parametrizado |
| A04 secrets | Exposição de secrets | LOW | OK — Docker secrets, nunca em git (FR-032) |

**Decisão do gate**: o finding HIGH (SEC-WH-1) foi escalado como BloqueioHumano
(gate de segurança, Princípio VI) e **resolvido pela resposta do operador
(block-001)**: o webhook deixa de ser exposto via Traefik e passa a ser recebido
pela overlay interna compartilhada com o n8n (isolamento de rede), com
`X-Webhook-Token` como defesa em profundidade opcional. Com isso o gate
owasp-security não tem mais findings HIGH/critical abertos. Os demais (MEDIUM/LOW)
foram incorporados ao design (`corrigir-agora`). A implementação (`/execute-task`)
deve materializar: exposição de rede (overlay interna p/ webhook; Traefik só
`/admin/*`+`/health`; atachar só o nosso serviço), `X-Webhook-Token` opcional, e
SEC-WH-2/3, SEC-LLM-1/3 e SEC-ADM-1/2/4.

## Itens confirmáveis em runtime (não-bloqueantes de design)

- **Nome da rede externa do Traefik** (Decisão 10): `stack.yml` referencia
  `network_main` (provável), a CONFIRMAR via inspeção de `traefik_traefik` em uma
  task de implementação antes do push final. Labels Traefik cobrem SOMENTE
  `/admin/*` e `/health`.
- **Nome da overlay compartilhada com o n8n** (resolve block-001): a CONFIRMAR
  via inspeção da rede em que o serviço do n8n já participa (`docker service
  inspect`/`network ls`), para o nosso `app` se atachar a ela (external,
  `attachable`). Atachar SOMENTE o nosso serviço — o serviço do n8n não é
  alterado (Princípio VI). O webhook (`http://<app>:8000/webhook/chatmaster`) é
  alcançado por essa overlay, nunca pelo ingress público.
- **IDs exatos dos modelos OpenAI**: parametrizados por env (Decisão 4),
  ajustáveis pelo operador sem redeploy de código.
- **Literais de request dos endpoints de mídia/botão/ticket** do ChatMaster:
  confirmados na implementação a partir da doc oficial linkada em
  `knowledge_base/example_webhook_json/outbound/links_documentacao_api.txt`.
