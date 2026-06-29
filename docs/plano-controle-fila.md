# Plano — Controle de Fila (gate da IA) + atendimento humano sem interferência

> Objetivo: o agente só atende quando o ticket está na **fila da IA (queueId=77)**.
> Quando o lead é transferido para a **fila humana (queueId=78)** via
> `POST /api/tickets/updateAPI` (`status:"pending"`, `queueId:"78"`), o agente fica
> **silencioso** — o humano atende **no mesmo número**, sem interferência. Ao
> devolver o ticket à fila 77, o agente retoma. Inclui o número de teste 555195953520.

## 0. Estado atual (auditoria)

- O webhook **lê** `payload.queueId` e `ticketData.queueId`, mas **não os usa** para
  decidir se processa (`app/api/webhook.py:473` só guarda em `msg_data`).
- O único filtro de estado é `ticketData.is_handoff` (`app/schemas/webhook.py:90`),
  que cobre `status ∈ {em_handoff, encerrado, closed, resolved}`.
  **GAP crítico:** a transferência humana usa `status="pending"` + `queueId=78`, e
  `"pending"` **não** está em `is_handoff` → hoje o bot **continuaria respondendo**
  após o handoff humano (interferência). O gate por fila resolve isso.
- `transfer_ticket` (`app/integrations/chatmaster.py`) resolve o `queueId` pela config
  (`handoff_queue_ids` / `handoff_queue_id_default`), com `userId=null`, `status="pending"`.
- Número de teste: env **`RESET_TEST_NUMBERS`** (CSV; default
  `5511967296849,5511941410998`) → seed em `numero_teste` no startup; também via admin API.
- Sinal extra disponível: `ContactData.disableBot` (o ChatMaster pode marcar quando um
  humano assume) — opcional como reforço.

## 1. Mudanças propostas

### 1.1 Config (`app/config.py`)
- Novo: `ai_queue_id: Optional[int] = 77` (env **`AI_QUEUE_ID`**) — fila onde o agente atua.
- Garantir fila humana = **78** no handoff: `HANDOFF_QUEUE_ID_DEFAULT=78` e/ou
  `HANDOFF_QUEUE_IDS_JSON` mapeando os destinos lógicos para 78
  (`{"consultores":78,"presencial":78,"licenciamento":78,"franquia":78,"suporte":78,"especialista":78}`).
- Incluir **555195953520** em `RESET_TEST_NUMBERS`.

### 1.2 Gate por fila no webhook (`app/api/webhook.py`)
No `receive_webhook`, após os filtros `fromMe`/`isGroup`/`#reset` e **antes** de
idempotência/debounce, adicionar:

```python
fila_atual = payload.queueId
if fila_atual is None and payload.ticketData:
    fila_atual = payload.ticketData.queueId

if (
    settings.ai_queue_id is not None
    and fila_atual is not None
    and fila_atual != settings.ai_queue_id
):
    logger.info(
        "webhook: fila=%s != fila IA=%s (chamado_id=%s) — atendimento humano, "
        "agente silencioso", fila_atual, settings.ai_queue_id, payload.chamadoId,
    )
    return {"ack": "ok"}  # 200, sem processar (sem retry)
```

- **`#reset` continua antes do gate** (números de teste resetam mesmo fora da fila 77).
- O filtro `is_handoff` existente é mantido como camada extra.

### 1.3 Defesa em profundidade (já coberta)
- Quando o **próprio agente** faz handoff, o webhook já marca `status="em_handoff"` no
  ticket (via `update_ticket_state`) e o `transfer_ticket` move para a fila 78. A partir
  daí, **ambas** as camadas bloqueiam o agente: (a) gate de fila (queueId≠77) e
  (b) filtro `is_handoff` (status em_handoff). Sem código adicional.

### 1.4 Retomada pelo agente
- Quando o humano finalizar e o operador **devolver o ticket à fila 77**, o gate volta a
  liberar e o agente retoma — automaticamente, sem código novo. (Procedimento operacional.)

### 1.5 Número de teste 555195953520
- Adicionar ao env `RESET_TEST_NUMBERS` (persistente; exige redeploy) **e/ou** via admin
  API `/admin/numeros-teste` (imediato, sem redeploy).
- ⚠️ O número precisa **casar exatamente** com o `sender` que o ChatMaster envia (DDI+DDD+
  número, sem `+`). O valor informado tem 12 dígitos (`55 51 9595 3520`); confirmar se não
  falta o 9º dígito do celular (ex.: `5551995953520`).

## 2. Decisões confirmadas (operador, 2026-06-29)
1. **Fila ausente no payload** (`queueId=None`): **processar + logar** (compat). O gate só
   bloqueia quando a fila vier explícita e ≠ 77.
2. **`#reset`** fica **antes** do gate → funciona para números de teste mesmo fora da fila 77.
3. **Fila IA = 77 / humana = 78** (confirmado).
4. **Número de teste**: usar **555195953520 exatamente como informado** (sem inserir 9º dígito).
   ⚠️ Se o `sender` real do ChatMaster vier com o 9 (`5551995953520`), o match falha — revalidar
   no teste real e ajustar se necessário.
5. **Inclusão do número**: **via admin API (imediato) E no env `RESET_TEST_NUMBERS` (persistente)**.

## 3. Testes (pytest)
- Webhook `queueId=77` → `_process_consolidated_messages` é chamado.
- Webhook `queueId=78` → ack 200 e **não** processa (spy não chamado; nenhuma mensagem enviada).
- Webhook `queueId=None` → processa (compat) — conforme decisão (1).
- `#reset` de número de teste com `queueId=78` → ainda reseta (conforme decisão (2)).
- Config: `ai_queue_id` default 77; `RESET_TEST_NUMBERS` inclui o novo número.
- `ruff` limpo; suíte inteira verde.

## 4. Validação real (WhatsApp, número 555195953520, com `#reset`)
1. Mensagem entrando pela **fila 77** → agente responde (log `webhook: motor processou`).
2. Acionar handoff (ex.: "quero falar com um humano") → bot transfere para **78**
   (log `transfer_ticket ... queueId=78`).
3. Nova mensagem (agora na **fila 78**) → agente **não** responde
   (log `fila=78 != fila IA=77 — agente silencioso`); humano atende no mesmo número.
4. Devolver o ticket à **fila 77** → agente **retoma**.
- Conferir pelos logs (agora visíveis) e/ou pelo banco.

## 5. Deploy
- Build/push/`service update` de nova tag (ex.: 1.6.0). Push e deploy executados pelo operador.

## 6. Critérios de aceite
- `queueId=77` → atende normalmente; `queueId=78` → silêncio total (zero mensagens do bot);
  retorno à fila 77 → retoma.
- Handoff do agente passa a impedir a interferência (corrige o gap do `status="pending"`).
- `#reset` funciona para o novo número de teste.
- Sem regressão; suíte verde; validação real PT confirmada.
