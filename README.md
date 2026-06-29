# SDR WhatsApp — Consultor Virtual Oficial GoldIncision

[![CI](https://github.com/paulotodo/sdr-goldincision/actions/workflows/ci.yml/badge.svg)](https://github.com/paulotodo/sdr-goldincision/actions/workflows/ci.yml)

Agente SDR consultivo para WhatsApp baseado no Mapa Mestre de Atendimento da
GoldIncision. Recebe webhooks do ChatMaster via n8n (overlay interna Docker),
conduz o lead pelos 6 caminhos do Mapa Mestre com anti-alucinacao rigida,
e responde via API oficial do ChatMaster.

## Stack

- Python 3.12 + FastAPI
- PostgreSQL 16 (persistencia duravel)
- Redis 7 (estado efemero: debounce, locks, janela quente)
- OpenAI (gpt-4o raciocinio + gpt-4o-mini classificacao/idioma)
- Docker Swarm (stack autocontida)

## Pre-requisitos

- Docker 24+ com Swarm inicializado
- Registry interno: `registry.todo-tips.com`
- Overlay do n8n pre-existente (confirmar nome com `docker network ls`)
- Overlay do Traefik pre-existente (confirmar nome com `docker network ls`)

## Build e Push

```bash
# Build e push da imagem
./scripts/build-push.sh latest

# Build com tag especifica
./scripts/build-push.sh 1.0.0
```

**NOTA**: O script NAO executa `docker stack deploy` (FR-031).
O deploy e responsabilidade do operador apos validacao da imagem.

## Configuracao

1. Copiar `.env.example` para `.env` e preencher valores reais (NAO commitar)
2. Confirmar nomes das redes externas:
   ```bash
   docker network ls | grep -E "n8n|traefik"
   ```
   Atualizar `N8N_NETWORK` e `TRAEFIK_NETWORK` no `.env` ou `stack.yml` se necessario.
3. Criar Docker secrets:
   ```bash
   echo 'sk-SUA_CHAVE' | docker secret create openai_api_key -
   echo 'TOKEN_CHATMASTER' | docker secret create chatmaster_token -
   echo 'TOKEN_ADMIN_SEGURO' | docker secret create admin_token -
   echo '' | docker secret create webhook_token -   # opcional
   ```

## Deploy (responsabilidade do operador)

```bash
# Validar stack.yml antes de deployar
docker stack config -c stack.yml

# Deploy (apos build-push e criacao de secrets)
N8N_NETWORK=n8n-net TRAEFIK_NETWORK=traefik-public SDR_HOST=sdr.todo-tips.com \
  docker stack deploy -c stack.yml sdr-whatsapp
```

## Redes Docker

| Rede | Proposito | Servicos |
|------|-----------|----------|
| `sdr-internal` | Overlay propria (isolada) | app + postgres + redis |
| `n8n-net` | Overlay compartilhada com n8n (webhook interno) | app apenas |
| `traefik-public` | Exposicao publica via Traefik | app apenas (`/admin/*` e `/health`) |

**IMPORTANTE**: `/webhook/chatmaster` NAO e exposto via Traefik.
O n8n posta direto na overlay interna sem rota publica.

## Endpoints

| Endpoint | Rede | Acesso |
|----------|------|--------|
| `POST /webhook/chatmaster` | n8n-net (overlay interna) | Sem rota Traefik |
| `GET/POST/PUT/DELETE /admin/cursos` | traefik-public | Token Bearer obrigatorio |
| `GET /health` | traefik-public | Sem autenticacao |

## Desenvolvimento local

```bash
pip install -e ".[dev]"
pytest tests/
```

## Estrutura de diretorios

```
app/
├── main.py          # FastAPI app + /health
├── config.py        # Settings via env/secrets
├── api/             # Routers (webhook, admin)
├── core/            # Logica de negocio (flow, intent, responder, memory)
├── integrations/    # Clientes externos (OpenAI, ChatMaster, midia)
├── repository/      # ORM SQLAlchemy + mapper
├── schemas/         # DTOs Pydantic
├── observability/   # Logging estruturado JSON
└── seed.py          # Seed dos 6 cursos
migrations/          # Alembic
tests/               # pytest
knowledge_base/      # Documentos oficiais (source of truth)
Dockerfile           # Multi-stage, non-root (UID 1001)
stack.yml            # Docker Swarm
scripts/
└── build-push.sh    # Build + push (SEM stack deploy)
```
