# Contract — Webhook Inbound (ChatMaster via n8n)

**Feature**: `sdr-whatsapp` | Fonte da verdade do shape: `knowledge_base/example_webhook_json/`
Cobre FR-001..FR-005, FR-037-INFRA-IDEMP, FR-035-INFRA-MUTEX, FR-003 (debounce).

## Endpoint

```
POST /webhook/chatmaster
Content-Type: application/json
```

A aplicação responde **200 imediatamente** (ack rápido ao n8n) e processa em
background após a janela de debounce. Respostas != 2xx fazem o n8n reenviar — por
isso a idempotência (FR-037) é obrigatória.

## Segurança do endpoint (OWASP API2/A07, A10-LLM, A07-API SSRF)

> **Endpoint NÃO exposto publicamente.** Decisão do operador (resolve SEC-WH-1):
> o webhook de entrada **não tem rota pública no Traefik**. O n8n roda no MESMO
> Docker Swarm e posta o evento DIRETAMENTE no serviço do agente pela rede
> overlay interna compartilhada (ex.: `http://sdr-agente-app:8000/webhook/chatmaster`),
> sem passar pelo ingress público. A superfície de forja por terceiro
> desaparece: só quem está dentro da overlay alcança o endpoint. Apenas a API de
> admin (`/admin/*`) e o `/health` são roteados pelo Traefik (ver §Exposição de
> rede abaixo e `admin-courses.md`).

- **SEC-WH-1 (autenticação de origem) — RESOLVIDO (era HIGH)**: mitigado por
  **isolamento de rede** em vez de autenticação no ingress. O webhook só é
  alcançável pela overlay interna onde o n8n e o agente coabitam; não há rota
  pública via Traefik, portanto não há URL pública a descobrir/forjar. O serviço
  do agente **atacha-se a uma overlay alcançável pelo n8n** — atacha-se SOMENTE
  o NOSSO serviço; **nunca** alterar containers/serviços de terceiros (incl. o
  n8n) — Princípio VI (isolamento absoluto).
  - **Defesa em profundidade (opcional, recomendada)**: validar também um header
    `X-Webhook-Token` (segredo compartilhado, comparado em **tempo constante**)
    na app. Configurado por env/secret (`WEBHOOK_TOKEN`), sem redeploy de código.
    Quando habilitado, requisição sem token válido → descartada (log de aviso),
    mantendo o ack 200 ao n8n para não disparar retry desnecessário.
- **SEC-WH-2 (SSRF no download de mídia) — MEDIUM**: `mediaUrl`/`remoteUrl` são
  conteúdo externo não confiável. Antes de baixar, validar que o host pertence à
  allowlist (`object.sp2.eveo.com.br`); rejeitar IPs privados/loopback/metadata
  (169.254.169.254), esquemas != https e redirecionamentos para fora da
  allowlist. (API7-SSRF)
- **SEC-WH-3 (rate limiting / unbounded consumption) — MEDIUM**: limitar
  requisições por `sender`/origem e impor teto global de gasto LLM por janela
  (LLM10/API4). Debounce reduz, mas não limita nº de contatos distintos.
- **SEC-WH-4 (validação de entrada)**: parsing tolerante porém com limites de
  tamanho de corpo e de itens em `mensagem[]`; conteúdo do lead tratado como
  NÃO confiável a jusante (ver `internal-flow.md` §prompt injection).

## Exposição de rede (Traefik vs. overlay interna)

| Rota | Exposição | Proteção |
|------|-----------|----------|
| `POST /webhook/chatmaster` | **interna apenas** (overlay compartilhada n8n↔agente) | isolamento de rede; sem rota Traefik; X-Webhook-Token opcional |
| `/admin/*` (CRUD cursos) | **pública via Traefik** | token de admin (deny-by-default) + rate limiting |
| `/health` | **pública via Traefik** | sem dados sensíveis; readiness/liveness |

- O serviço do agente atacha **duas redes**: (1) a overlay própria da stack
  (`app↔postgres↔redis`, não exposta) e (2) a overlay compartilhada onde o n8n
  já reside (para receber o webhook). O atachamento é feito **apenas no nosso
  serviço** — o serviço do n8n NÃO é modificado (Princípio VI).
- Labels do Traefik no `stack.yml` roteiam **exclusivamente** `/admin/*` e
  `/health`. **Não há label/router Traefik para `/webhook/chatmaster`.**
- Trade-off documentado: a porta do webhook fica acessível a qualquer serviço da
  overlay compartilhada — o `X-Webhook-Token` (defesa em profundidade) restringe
  a chamadores que conheçam o segredo, mitigando movimento lateral intra-overlay.

## Request body (shape real do ChatMaster/Whaticket)

```jsonc
{
  "mensagem": [
    { "type": "text", "text": "ola" }
    // OU item de mídia com mediaType/mediaUrl (audio/video/image/document):
    // { "mediaType": "audio", "mediaUrl": "https://object.sp2.eveo.com.br/...opus",
    //   "body": "...", "id": 6568537, "wid": "wamid....", "fromMe": false }
  ],
  "sender": "5511967296849",        // número do contato (DDI+DDD+num)
  "chamadoId": 138901,               // == ticketData.id
  "acao": "start",
  "name": "Paulo Sudre",
  "fromMe": false,                   // se true → ignorar (FR-002)
  "companyId": 1,
  "queueId": 78,
  "isGroup": false,
  "ticketData": {
    "id": 138901,
    "status": "open",                // estado do ticket no ChatMaster
    "queueId": 78,
    "whatsappId": 127,
    "companyId": 1,
    "contactId": 138449,
    "uuid": "7cdd4ec4-...",
    "flowStatus": "menu",
    "variables": { "nome_lead": "Paulo", "numero_lead": "5511967296849" },
    "contact": {
      "id": 138449, "name": "Paulo Sudre", "number": "5511967296849",
      "email": "paulodcs@gmail.com"
    }
  }
}
```

### Itens de `mensagem[]` por `type`/`mediaType`

| Caso | Campos relevantes | Tratamento |
|------|-------------------|-----------|
| Texto | `type:"text"`, `text` | usa `text` direto |
| Áudio | `mediaType:"audio"`, `mediaUrl` (`.opus`) | baixar de `object.sp2.eveo.com.br` + transcrever (FR-005) |
| Vídeo | `mediaType:"video"`, `mediaUrl` | reconhece, não processa binário; pede descrição se necessário (US5-AS6) |
| Imagem | `mediaType:"image"`, `mediaUrl` | idem vídeo |
| Documento | `mediaType:"document"`, `mediaUrl` | idem vídeo |
| Desconhecido | outro `mediaType`/`type` | descarta silenciosamente + log de aviso (edge case) |

## Validação (Pydantic)

- `sender`, `chamadoId`, `mensagem[]` obrigatórios. Payload inválido → 200 + log
  de aviso (não 4xx, para não disparar retry desnecessário do n8n) e descarte.
- `fromMe == true` (no envelope OU no item) → descartar sem efeito (FR-002).

## Pipeline de processamento (ordem obrigatória)

1. **Idempotência** (FR-037): `key = idemp:{chamadoId}:{sha256(conteúdo consolidado)}`,
   `SET NX EX 86400`. Se já existe → descartar (reenvio do n8n).
2. **Filtros**: `fromMe`, tipo desconhecido, e **estado do ticket** — se ticket
   local está `em_handoff`/`encerrado`, não processar (FR-024, edge case).
3. **Debounce** (FR-003): `RPUSH debounce:{chamadoId}`; agenda flush após janela
   (default 8s, env `DEBOUNCE_SECONDS`). Mensagens da janela viram uma entrada
   consolidada (SC-005).
4. **Lock** (FR-035): `SET lock:ticket:{chamadoId} NX PX 30000` antes de processar
   o buffer; garante resposta única por ticket em rajada alta. Libera ao fim.
5. **Motor**: ver `internal-flow.md`.

## Responses

| Status | Quando |
|--------|--------|
| `200 OK` | sempre que o payload é aceito para processamento OU descartado por regra (idempotência/fromMe/tipo) — ack ao n8n |
| `200 OK` | payload malformado → ack + log de aviso (evita retry) |
| `500` | falha inesperada antes do ack (será reprocessado via idempotência) |

## Edge cases cobertos

- `fromMe:true` → ignora (FR-002).
- Rajada (>5 msgs <2s) → debounce consolida (FR-003, SC-005).
- Ticket já em handoff → não responde (FR-024).
- Tipo desconhecido → descarte silencioso + log.
- Reenvio do n8n (mesmo conteúdo) → idempotência descarta (FR-037).
- Falha de transcrição de áudio → agente pede repetição em texto (FR-005).
