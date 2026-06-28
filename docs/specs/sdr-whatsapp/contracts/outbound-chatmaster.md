# Contract — Outbound + Handoff (ChatMaster API)

**Feature**: `sdr-whatsapp` | Cobre FR-016, FR-017, FR-022, FR-023, FR-024, FR-034.
Hosts (whitelist): `api2.chatmasterveloz.com` (envio), `clihelper.chatmasterveloz.com`
(tickets/CRM/docs). Token Bearer via secret — NUNCA em código/git (FR-032).

## 1. Envio de mensagem de texto (FR-016)

```
POST https://api2.chatmasterveloz.com/api/messages/sendOfficialData
Authorization: Bearer <CHATMASTER_TOKEN>   # via secret
Content-Type: application/json

{ "number": "5511967296849", "text": "<conteúdo>" }
```

- `number`: DDI+DDD+num (mesmo formato de `sender`).
- `text`: conteúdo já quebrado em bloco curto (ver §Quebra de mensagens).
- Requer janela de atendimento aberta (responsabilidade da plataforma).

### Quebra de mensagens longas (FR-015, Regra 13)

Respostas longas são divididas em blocos curtos; uma pergunta por mensagem
(exceto menus que listam opções). Cada bloco = uma chamada `sendOfficialData`,
enviadas em ordem. Apresentações oficiais longas são enviadas na íntegra,
fragmentadas apenas para respeitar limites de tamanho — sem reescrever o texto
(FR-010).

## 2. Envio de mídia / links / botões (FR-017)

Endpoints documentados em
`knowledge_base/example_webhook_json/outbound/links_documentacao_api.txt`
(`clihelper.chatmasterveloz.com/principal/apis/...`):

| Recurso | Doc |
|---------|-----|
| Template / template com variável | `.../mensagem/api-oficial-mensagem-template*` |
| Imagem/áudio/vídeo/documento por URL | `.../midias/api-oficial-enviar-*-por-url` |
| Botão URL | `.../botoes-no-whatsapp/api-oficial-botao-url` |
| Botão quick reply | `.../botoes-no-whatsapp/api-oficial-botao-quick-reply` |

Usados para: enviar link de inscrição (Hotmart por idioma), apresentações com
mídia, e botões de opção do menu inicial. Os literais de request/headers exatos
de cada endpoint são confirmados na implementação a partir da doc oficial (o
abstrato é um `OutboundClient` com método por recurso).

## 3. Gestão de ticket / Handoff (FR-022, FR-023, FR-024)

**Contrato real (API "Atualizar Ticket")** — confirmado pelo operador via
`clihelper.chatmasterveloz.com/principal/apis/ticket/api-atualizar-ticket/`:

```
POST https://api2.chatmasterveloz.com/api/tickets/updateAPI
Authorization: Bearer <CHATMASTER_TOKEN>
Content-Type: application/json
{
  "ticketId": "<id>",
  "status": "open" | "pending" | "closed",
  "userId": <id> | null,
  "queueId": <id> | null,
  "typebot_sessionId": "",
  "customA": "",
  "customB": ""
}
```

**Transferir para fila de atendimento humano**: `queueId` = id da fila,
`userId` = null (sem atendente atrelado), `status` = `"pending"`.

O `queueId` é específico do deploy e vem SEMPRE da config do operador
(`HANDOFF_QUEUE_ID_DEFAULT` / `HANDOFF_QUEUE_IDS_JSON`), mapeado a partir do
destino lógico do fluxo — o LLM nunca fornece um `queueId` arbitrário (SEC-LLM-3).

### Fluxo de handoff

1. Fluxo determina encaminhamento (presencial/licenciamento/franquia/suporte) OU
   lead pede humano explicitamente → chamar transferência de fila/conexão para o
   especialista correto.
2. Marcar ticket local `status = em_handoff` (+ `handoff_motivo`, `handoff_destino`).
3. A partir daí o agente **não envia mais mensagens** naquele ticket (FR-023,
   US3-AS5). Webhooks subsequentes desse ticket são filtrados (FR-024).
4. Registrar evento de handoff: `ticket_id`, `handoff_type` (fila/conexão),
   `destino`, `motivo` (FR-034).

### Caso especial — Paciente modelo (Caminho 5, FR-014)

NÃO transfere ticket. Envia APENAS o número da Nídia (+55 21 97423-9844) e
encerra; não responde dúvidas sobre vagas/seleção/valores (US3-AS6).

### Curso Online (Caminho 1)

Auto-atendimento: envia o link de inscrição no idioma e conclui (`status =
encerrado`); handoff de ticket NÃO é necessário (US3-AS1).

## Tratamento de erro (FR-033, edge cases)

- Falha na API ChatMaster → log estruturado de erro (`tipo`, `detalhe`,
  `ticket_id`) + mensagem genérica ao usuário ("tivemos um problema técnico,
  tentaremos novamente") sem expor detalhe técnico (US7-AS3).
- Timeout do LLM (>30s) → enviar "aguarde um momento" + log; nunca deixar o lead
  sem resposta (edge case).
- Após `em_handoff`, qualquer tentativa de envio é bloqueada na camada de envio
  (guarda dupla: filtro de webhook + checagem no `OutboundClient`).
