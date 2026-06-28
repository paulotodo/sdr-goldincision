# Contract — Admin API (Cursos como Dados)

**Feature**: `sdr-whatsapp` | Cobre FR-025, FR-026, FR-027, US4 (P1), Princípio VII.
API REST protegida por token de admin (header `Authorization: Bearer <ADMIN_TOKEN>`,
secret). Convenção de payload: JSON `camelCase`. Sem token válido → `401` e
nenhuma operação (US4-AS5).

## Autenticação

```
Authorization: Bearer <ADMIN_TOKEN>   # via secret ADMIN_TOKEN
```
Toda rota `/admin/*` exige token (deny-by-default). Ausente/ inválido →
`401 Unauthorized`.

**Controles de segurança (OWASP API1/API2/A01/A04)**:
- **SEC-ADM-1**: comparação do token em **tempo constante** (evita timing attack);
  nunca comparar com `==` simples.
- **SEC-ADM-2**: **rate limiting** nas rotas `/admin/*` (mitiga brute-force do
  token estático — API4/A07).
- **SEC-ADM-3**: token estático longo-vivo via secret (FR-032); registrar plano
  de rotação. Considerar restringir `/admin/*` por rede/IP no Traefik além do
  token, já que fica no mesmo serviço público.
- **SEC-ADM-4 (mass assignment / BOPLA — API3)**: aceitar somente campos
  declarados no schema (Pydantic estrito); ignorar/rejeitar campos extras como
  `id`, `ativo` em POST que não devam ser definidos pelo cliente.

## Recursos

### Cursos

| Método | Rota | Descrição | Resposta |
|--------|------|-----------|----------|
| `GET` | `/admin/cursos` | lista cursos ativos com dados completos | `200` lista (US4-AS6) |
| `GET` | `/admin/cursos/{id}` | detalhe de um curso | `200` / `404` |
| `POST` | `/admin/cursos` | cria curso completo | `201` + id (US4-AS1) |
| `PUT` | `/admin/cursos/{id}` | atualiza curso (ex. nova turma) | `200` (US4-AS3) |
| `DELETE` | `/admin/cursos/{id}` | soft-delete (`ativo=false`) | `204` (US4-AS4) |

#### POST/PUT body (curso completo)

```jsonc
{
  "slug": "curso-online-hg",
  "nome": "Curso Online de Harmonização Glútea",
  "tipo": "online",                       // online|presencial|licenciamento|franquia
  "caminhoMapaMestre": 1,
  "elegibilidade": { "medico": true },
  "apresentacoes": [
    { "idioma": "pt", "texto": "<apresentação oficial verbatim PT>" },
    { "idioma": "en", "texto": "<...EN>" },
    { "idioma": "es", "texto": "<...ES>" }
  ],
  "objecoes": [
    { "idioma": "pt", "objecao": "está caro", "resposta": "<resposta oficial verbatim>" }
  ],
  "turmas": [
    { "cidade": "São Paulo", "pais": "BR", "dataInicio": "2026-09-01",
      "capacidade": 30, "vagasDisponiveis": 30, "lotePreco": "lote 1" }
  ],
  "links": [
    { "idioma": "pt", "url": "https://pay.hotmart.com/..." },
    { "idioma": "en", "url": "https://pay.hotmart.com/Q95039051K" },
    { "idioma": "es", "url": "https://pay.hotmart.com/N95711232T" }
  ],
  "midias": [
    { "idioma": null, "tipo": "image", "url": "https://...", "legenda": "..." }
  ]
}
```

#### GET /admin/cursos response (200)

```jsonc
[
  {
    "id": 1, "slug": "curso-online-hg", "nome": "...", "tipo": "online",
    "caminhoMapaMestre": 1, "elegibilidade": {"medico": true}, "ativo": true,
    "apresentacoes": [...], "objecoes": [...], "turmas": [...],
    "links": [...], "midias": [...],
    "createdAt": "...", "updatedAt": "..."
  }
]
```

### Sub-recursos (granular — opcional, mesmo token)

Para edição fina sem reenviar o curso inteiro (SHOULD; o CRUD agregado acima é o
mínimo MUST):

| Método | Rota |
|--------|------|
| `POST`/`PUT`/`DELETE` | `/admin/cursos/{id}/turmas[/{turmaId}]` |
| `POST`/`PUT`/`DELETE` | `/admin/cursos/{id}/objecoes[/{objId}]` |
| `PUT` | `/admin/cursos/{id}/apresentacoes/{idioma}` |
| `PUT` | `/admin/cursos/{id}/links/{idioma}` |

## Códigos de status

| Status | Quando |
|--------|--------|
| `200` | GET/PUT ok |
| `201` | curso criado (retorna `id`) |
| `204` | DELETE (soft) ok |
| `400` | payload inválido (campos obrigatórios ausentes) |
| `401` | token ausente/ inválido (US4-AS5) |
| `404` | curso inexistente |
| `409` | `slug` duplicado em POST |

## Runtime read (FR-026)

O motor conversacional lê o catálogo do Postgres em runtime (sem cache de processo
de longa duração que impeça refletir mudanças; cache curto opcional invalidado em
mutações de admin). Criar/editar/remover curso reflete em conversas novas SEM
redeploy (SC-004).

## Seed inicial (FR-027)

`seed` idempotente popula 6 cursos oficiais a partir de
`knowledge_base/documentos_agente/` (apresentações verbatim, objeções,
elegibilidade, turmas, links). Upsert por `slug` (re-run não duplica). Cobre:
Curso Online HG, HG Módulo 1, HG360 SP, HG360 Barcelona, Licenciamento
Internacional, Franquia GoldIncision (US4-AS7).
