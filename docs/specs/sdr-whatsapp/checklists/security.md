# Security Checklist: sdr-whatsapp

**Purpose**: Validar a QUALIDADE (completude, clareza, consistência) dos
requisitos de segurança e de exposição de rede da feature — em especial a
decisão block-001 (webhook por overlay interna; Traefik só `/admin/*`+`/health`).
**Created**: 2026-06-28
**Feature**: [spec.md](../spec.md) · [plan.md](../plan.md) · [contracts/webhook-inbound.md](../contracts/webhook-inbound.md)

> Items `{auto}` resolvidos pelo agente com citação. Items `{humano}` aguardam o
> dono do produto. `[Gap]`/`[Ambiguity]`/`[Conflict]` viram `/clarify` ou
> `/create-tasks`.

## Exposição de Rede (webhook interno vs. Traefik)

- [x] CHK001 - O modelo de exposição do webhook está definido sem ambiguidade (interno via overlay, não público)? [Clareza, contracts/webhook-inbound.md §Exposição de rede + §Segurança] {auto} — Resolvido: webhook-inbound.md:56 "POST /webhook/chatmaster | interna apenas (overlay compartilhada n8n↔agente)" e :65 "Não há label/router Traefik para /webhook/chatmaster".
- [x] CHK002 - Está especificado que SOMENTE `/admin/*` e `/health` são roteados pelo Traefik? [Completude, plan.md §Considerações de Segurança + webhook-inbound.md tabela] {auto} — Resolvido: plan.md:62 "só `app` na rede do Traefik e somente para /admin/*+/health"; tabela em webhook-inbound.md §Exposição de rede.
- [x] CHK003 - Está definido que apenas o NOSSO serviço é atachado à overlay compartilhada (n8n nunca modificado)? [Clareza/Isolamento, Princípio VI, plan.md:62 + webhook-inbound.md:30] {auto} — Resolvido: ambos os docs afirmam "atachar SOMENTE o nosso serviço; n8n nunca modificado".
- [x] CHK004 - A spec (FR-001/FR-029) e a US6-AS2 refletem o modelo de exposição de rede pós-block-001 (webhook por overlay interna)? [Conflict→Resolvido, Spec §FR-029 + §Clarifications] {auto} — Resolvido nesta onda: FR-029 atualizado ("webhook NÃO DEVE ter rota pública no Traefik; recebido pela overlay interna compartilhada com o n8n") + nova entrada em §Clarifications "Exposição de rede do webhook (block-001 / dec-015)".
- [x] CHK005 - O nome da overlay compartilhada está tratado como item confirmável em runtime (não inventado)? [Clareza/Dependência, plan.md §Itens confirmáveis] {auto} — Resolvido: plan.md "Nome da overlay compartilhada com o n8n (resolve block-001): a CONFIRMAR via inspeção".

## Autenticação e Autorização

- [x] CHK006 - O requisito de autenticação da admin API (token, deny-by-default) está especificado e mensurável? [Completude, Spec §FR-025, US4-AS5] {auto} — Resolvido: FR-025 "API REST de admin protegida por token"; US4-AS5 "sem token válido → 401".
- [x] CHK007 - A defesa em profundidade do webhook (X-Webhook-Token, comparação tempo-constante) está definida como opcional e configurável sem redeploy? [Clareza, contracts/webhook-inbound.md SEC-WH-1 defesa em profundidade] {auto} — Resolvido: webhook-inbound.md:36 "X-Webhook-Token ... tempo constante ... WEBHOOK_TOKEN ... sem redeploy".
- [x] CHK008 - Os requisitos de mitigação de timing attack / brute-force do token admin estão definidos? [Completude, plan.md SEC-ADM-1/2 + admin-courses.md] {auto} — Resolvido: plan.md:186 "comparação tempo-constante + rate limiting"; admin-courses.md SEC-ADM-2.
- [ ] CHK009 - O apetite de risco para deixar o webhook acessível a qualquer serviço da overlay compartilhada (movimento lateral intra-overlay) é aceitável sem X-Webhook-Token obrigatório? [Risco] {humano} — Decisão do dono do produto: tornar X-Webhook-Token obrigatório vs. opcional.

## Proteção de Dados e Entrada não-confiável

- [x] CHK010 - Os requisitos de não-exposição de Postgres/Redis a redes externas estão especificados? [Completude, Spec §FR-029, plan.md Princípio VI] {auto} — Resolvido: FR-029 "Postgres e Redis NÃO devem ser expostos a redes externas".
- [x] CHK011 - Os requisitos de manuseio de secrets (nunca em git/stack.yml; Docker secrets) são verificáveis? [Mensurabilidade, Spec §FR-032, US6-AS5] {auto} — Resolvido: FR-032 + US6-AS5 "stack.yml não contém secret em texto claro".
- [x] CHK012 - A mitigação de SSRF no download de mídia (allowlist de host, bloqueio de IP privado/metadata) está especificada? [Completude, contracts/webhook-inbound.md SEC-WH-2] {auto} — Resolvido: webhook-inbound.md SEC-WH-2 (allowlist `object.sp2.eveo.com.br`, bloqueio 169.254.169.254).
- [x] CHK013 - O tratamento de conteúdo do lead como não-confiável (prompt injection) está definido? [Completude, plan.md SEC-LLM-1, internal-flow.md] {auto} — Resolvido: plan.md:188 SEC-LLM-1 "separação estrutural sistema/usuário".
- [x] CHK014 - Os limites de tamanho de corpo/itens em `mensagem[]` estão definidos como requisito? [Clareza, contracts/webhook-inbound.md SEC-WH-4] {auto} — Resolvido: SEC-WH-4 "limites de tamanho de corpo e de itens".

## Consumo, Disponibilidade e Idempotência

- [x] CHK015 - Rate limiting / teto de gasto LLM (consumo ilimitado) é requisito especificado? [Completude, contracts/webhook-inbound.md SEC-WH-3, plan.md] {auto} — Resolvido: SEC-WH-3 "limite por origem + teto de gasto LLM".
- [x] CHK016 - A idempotência do webhook (chamadoId + hash, TTL 24h) está especificada e mensurável? [Mensurabilidade, Spec §FR-037-INFRA-IDEMP] {auto} — Resolvido: FR-037 "idempotente por chamadoId + hash ... TTL 24 horas".
- [x] CHK017 - A serialização de operações concorrentes por ticket (lock Redis) é requisito? [Completude, Spec §FR-035-INFRA-MUTEX] {auto} — Resolvido: FR-035-INFRA-MUTEX "serializadas via TTL de lock Redis".
- [x] CHK018 - O comportamento de resposta a payload inválido/sem credencial (200 ack + log, evitando retry do n8n) é consistente entre contrato e responses? [Consistência, contracts/webhook-inbound.md §Responses + SEC-WH-1 defesa] {auto} — Resolvido: §Responses (200 ack a malformado) e SEC-WH-1 defesa (token inválido → descarte com ack 200) coerentes.

## Isolamento de Infraestrutura

- [x] CHK019 - O requisito de não afetar stacks/serviços de terceiros está definido e verificável? [Mensurabilidade, Spec §US6-AS4/AS6, FR-028] {auto} — Resolvido: US6-AS4 "nenhum container de stacks existentes aparece como alterado"; AS6 "apenas serviços da stack sdr-whatsapp removidos".
- [x] CHK020 - O escopo de entrega (build+push sem `docker stack deploy` live) está especificado de forma inequívoca? [Clareza, Spec §FR-031] {auto} — Resolvido: FR-031 "VAI ATÉ build + push ... docker stack deploy NÃO é executado pelo agente".

## Notes

- CHK004 (`[Conflict]`) resolvido nesta onda: spec FR-029 + §Clarifications
  alinhados ao modelo de exposição pós-block-001.
- 1 item `{humano}` aberto (CHK009): obrigatoriedade do `X-Webhook-Token` —
  decisão do dono do produto antes de `/execute-task`.
- 19 de 20 items `{auto}` resolvidos com citação; 0 gaps de requisito abertos.
