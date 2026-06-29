# Plano — Respostas objetivas + respeito ao rate limit da WhatsApp Cloud API

> Para executar numa sessão limpa. Origem: smoke test do operador (2026-06-29).
> Projeto em `/root/sdr-goldincision`. Stack em produção: `sdr-whatsapp_app`
> (`registry.todo-tips.com/sdr-whatsapp`), Docker Swarm.

## 1. Sintomas observados (smoke test)

1. Lead pediu informações do **HG360 presencial** → bot informou e qualificou (médico). OK.
2. Lead **mudou de assunto** para o **método/Sistema GoldIncision** → bot explicou que o
   curso de Harmonização Glútea não ensina o método e ofereceu transferência. OK em
   estrutura.
3. Lead pediu **"mais informações"** → bot enviou **diversas mensagens em rajada**, uma
   atrás da outra, **ferindo o rate limit/pacing da API oficial da Meta (WhatsApp Cloud)**.

**Requisitos do operador:** (a) responder **sempre de forma objetiva e resumida**;
(b) **respeitar o rate limit** da WhatsApp Cloud API (sem rajadas).

## 2. Estado atual (auditoria, com refs)

- `app/integrations/chatmaster.py`
  - `send_message`: em `status >= 400` apenas loga e **levanta exceção** — **sem backoff/
    retry em 429**, sem honrar `Retry-After`. Um 429 derruba o restante do envio.
  - `send_message_blocks`: envia os blocos com `_INTER_BLOCK_DELAY = 0.4s` fixo, **sem cap
    de quantidade** de blocos e **sem throttle global/por destinatário**.
  - `_split_into_blocks` (`_SOFT_BLOCK_CHARS = 400`): quebra por parágrafo — apresentações
    longas viram **muitas mensagens** (a rajada observada).
- `app/core/responder.py`: `generate` chama `chat_reasoning(..., max_tokens=600, temperature=0.3)`
  → respostas potencialmente longas; o prompt não impõe limite explícito de tamanho.
- `app/core/flow.py` (Caminho 3 — Licenciamento): ao abrir dúvidas, envia a **apresentação
  verbatim do Licenciamento** (extraída de PDF, longa) → fragmentada em muitos blocos. O
  objetivo do C3 é **qualificar e conduzir à reunião**, não despejar a apresentação inteira.
- Não há configuração de pacing/rate limit (`app/config.py`).

> Nota de contexto: na sessão anterior, o split foi ajustado para **fragmentar** respostas
> longas (feedback "respostas muito longas"). Este plano **não reverte** isso — equilibra:
> conteúdo conciso + fragmentação **limitada** + **pacing** que respeita a Meta.

## 3. Mudanças propostas

### 3.1 Concisão de conteúdo (responder + prompts)
- Reduzir `max_tokens` de **600 → ~280** em `responder.generate` (dúvidas).
- Reforçar `_SYSTEM_BASE`/prompts: *"Seja OBJETIVO e RESUMIDO: no máximo 3–4 frases curtas.
  Responda apenas o que foi perguntado. NÃO repita a apresentação inteira; se o lead quiser
  todos os detalhes, ofereça conduzir a uma conversa com um especialista."*
- Mantém a regra 1 pergunta/mensagem e o verbatim das apresentações oficiais (FR-010).

### 3.2 Limite de mensagens por turno (anti-rajada)
- Novo `MAX_MSGS_PER_TURN` (default **4**, via `settings`).
- `send_message_blocks`: se o split gerar mais que o cap, enviar os primeiros `N-1` blocos e
  **consolidar o restante** num último bloco curto com convite ("posso detalhar o restante
  ou te conectar com um especialista que explica tudo — prefere qual?"), em vez de despejar
  todos. Nunca exceder o cap por turno.

### 3.3 Rate limit / pacing da WhatsApp Cloud API (chatmaster)
- **Throttle por destinatário + global**: garantir intervalo mínimo entre envios
  (`WHATSAPP_MIN_INTERVAL_MS`, default **~1000ms**) — substitui/eleva o `_INTER_BLOCK_DELAY`
  fixo de 0,4s. Implementar como `_Pacer` (último-envio por número + lock) no
  `ChatMasterClient` ou num wrapper de envio.
- **Retry com backoff em 429/5xx** no `send_message`: até 3 tentativas, base exponencial
  (1s, 2s, 4s), **honrando o header `Retry-After`** quando presente; em 429 persistente,
  abortar com log (não floodar). 4xx não-429 continua falhando rápido.
- **Cap por turno** (3.2) reduz a chance de bater o limite.
- Tornar `_INTER_BLOCK_DELAY`, `MAX_MSGS_PER_TURN` e `WHATSAPP_MIN_INTERVAL_MS` configuráveis
  por env, para alinhar ao limite real do BSP (ChatMaster) / Meta.

### 3.4 Apresentações longas (C3 Licenciamento e afins)
- No Caminho 3 (Licenciamento), substituir o **dump da apresentação verbatim** por um
  **resumo objetivo** (2–3 frases do que é o Licenciamento) + **convite à reunião** com
  especialista — fiel ao objetivo do C3 ("qualificar e marcar reunião, nunca vender").
- Se o operador quiser manter texto oficial, **curar uma versão curta** da apresentação no
  catálogo (seed/admin) para os caminhos cujo objetivo é handoff/reunião.
- Para os cursos (C1/C2), manter a apresentação oficial, mas respeitando o cap por turno
  (3.2) e o pacing (3.3).

### 3.5 Config (`app/config.py`) + `.env.example`
- `whatsapp_min_interval_ms: int = 1000`
- `max_msgs_per_turn: int = 4`
- `inter_block_delay_seconds: float = 1.0` (substitui a constante fixa)
- (responder) `reasoning_max_tokens: int = 280` (ou constante no responder)

## 4. Testes (pytest)
- `chatmaster`:
  - `send_message` recebe 429 → faz retry com backoff e respeita `Retry-After` (mock do httpx
    retornando 429 depois 200 → 1 retry, sucesso).
  - 429 persistente → aborta após N tentativas sem exceção não tratada (log).
  - `send_message_blocks` respeita o cap `MAX_MSGS_PER_TURN` (texto que geraria 8 blocos →
    no máx 4 envios; último consolida/encerra).
  - pacing: intervalo mínimo entre envios é respeitado (mock de relógio/sleep — assert no
    número de sleeps / no delay aplicado).
- `responder`: `generate` chama `chat_reasoning` com `max_tokens` reduzido (assert no arg).
- `flow` (C3): dúvida de Licenciamento → resposta **curta** + convite à reunião (não despeja
  apresentação inteira).
- `ruff` limpo; suíte inteira verde.

## 5. Validação real (WhatsApp, número de teste, com `#reset`)
- Reproduzir o cenário do smoke test: HG360 presencial → mudar p/ método GoldIncision →
  "mais informações".
- Esperado: respostas **curtas e objetivas**, **no máx ~4 mensagens por turno**, com
  **intervalo perceptível** entre elas; **sem rajada**; sem 429 nos logs (ou com retry
  bem-sucedido). Conferir pelos logs (já visíveis) e ausência de erro de envio.
- Repetir 1 caminho em EN/ES.

## 6. Deploy
- Build/push/`service update` de nova tag (ex.: 1.7.0). Push e deploy executados pelo operador.
- Ajustar, se necessário, os envs de pacing (`WHATSAPP_MIN_INTERVAL_MS`, `MAX_MSGS_PER_TURN`,
  `INTER_BLOCK_DELAY_SECONDS`) conforme o limite real do ChatMaster/Meta.

## 7. Critérios de aceite
- Respostas objetivas e resumidas (dúvidas em ≤ 3–4 frases).
- Nenhum turno envia mais que `MAX_MSGS_PER_TURN` mensagens; envios espaçados por
  `WHATSAPP_MIN_INTERVAL_MS`.
- 429 da API é tratado com backoff/retry (honrando `Retry-After`); sem rajada que fira o
  pacing da Meta.
- C3 Licenciamento conduz à reunião com resumo curto (sem dump da apresentação).
- Suíte verde + lint limpo + validação real confirmada.

## 8. Pendências / dependências
- **Limite exato do BSP/Meta**: confirmar com o ChatMaster qual o pacing/limite aceito para
  ajustar os defaults (o plano usa valores conservadores e configuráveis).
- **Conteúdo curto oficial** do Licenciamento (e demais apresentações de handoff): decisão de
  conteúdo do operador, se quiser texto oficial curto em vez do resumo gerado.
