# Quickstart & Cenários de Teste — Agente SDR WhatsApp

**Feature**: `sdr-whatsapp` | **Spec**: [spec.md](./spec.md)

Cada cenário: passos numerados → **Expected**. Cobrem happy paths + error cases
dos critérios de sucesso (SC-001..SC-008).

## Setup local

1. `cp .env.example .env` → preencher `OPENAI_API_KEY`, `CHATMASTER_TOKEN`,
   `ADMIN_TOKEN`, URLs de Postgres/Redis locais.
2. Subir Postgres + Redis locais (docker compose de dev) → rodar Alembic
   migrations → rodar seed (`python -m app.seed`).
3. `uvicorn app.main:app --reload` → app em `http://localhost:8000`.
4. **Expected**: `GET /health` retorna `200 {"status":"ok"}`.

---

## Cenário 1 — Intenção clara sem requalificação (SC-001, US1)

1. `POST /webhook/chatmaster` com `mensagem:[{type:text,text:"Quanto custa o curso online?"}]`,
   `sender:"5511999990001"`, `chamadoId:900001`, `fromMe:false`.
2. Aguardar janela de debounce (8s).
3. **Expected**: o agente responde com o preço oficial do Curso Online (da Base
   Oficial) **sem** antes perguntar "Você é médico?"; em seguida (após
   qualificação no fluxo) oferece o link de inscrição no idioma da conversa, em
   < 15s. Nenhuma informação inventada.

---

## Cenário 2 — Menu inicial quando intenção não é clara (US1-AS1)

1. `POST /webhook/chatmaster` com `text:"ola"`, novo `chamadoId:900002`.
2. **Expected**: agente envia menu inicial com as 6 opções numeradas em < 10s.

---

## Cenário 3 — Elegibilidade inflexível (US1-AS4, FR-009)

1. Caminho 2 (presenciais); lead informa **não** ter registro médico ativo.
2. **Expected**: agente informa que a formação é exclusiva para médicos, agradece
   e encerra **sem** oferecer alternativas.

3. (variação) Lead médico com experiência **só facial** no Caminho 2.
4. **Expected**: NÃO elegível ao HG360 → indica HG Módulo 1 (iniciantes)
   (US1-AS5).

---

## Cenário 4 — Memória persistente, sem repetir perguntas (SC-002, US2)

1. Enviar "sou médico, tenho experiência em facial" (`chamadoId:900004`) →
   agente responde (HG Módulo 1).
2. Encerrar a sessão; reabrir 30 min depois (mesmo contato, novo evento) e enviar
   "quero saber mais sobre o módulo".
3. **Expected**: o agente **NÃO** pergunta de novo "você é médico?" nem "tem
   experiência corporal?"; usa as variáveis já capturadas (FR-021).

---

## Cenário 5 — Debounce de rajada (SC-005, FR-003)

1. Enviar 3 mensagens curtas do mesmo `chamadoId:900005` em < 8s
   ("oi", "quero", "o curso online").
2. **Expected**: após a janela, o agente processa e responde **uma única vez** ao
   conjunto consolidado (não 3 respostas).

---

## Cenário 6 — Idempotência de reenvio (FR-037)

1. Enviar o mesmo evento (mesmo `chamadoId` + mesmo conteúdo) duas vezes
   (simula retry do n8n).
2. **Expected**: o agente gera **uma única** resposta; o segundo evento é
   descartado pela chave de idempotência (TTL 24h).

---

## Cenário 7 — Handoff para humano (SC-003, US3)

1. Caminho 2, médico elegível a HG Módulo 1; lead responde "sim" a "encaminhar
   ao consultor?".
2. **Expected**: a API de transferência de fila/conexão é chamada **antes** de
   qualquer outra mensagem; o ticket vira `em_handoff`; o agente não envia mais
   nada nesse ticket (US3-AS5).
3. (variação) Lead diz "quero falar com um humano" a qualquer momento →
   **Expected**: interrompe o fluxo e transfere imediatamente (US3-AS4).

---

## Cenário 8 — Paciente modelo → Nídia (US3-AS6, FR-014)

1. Mensagem identificada como Caminho 5 (paciente modelo).
2. **Expected**: agente envia **apenas** o WhatsApp da Nídia (+55 21 97423-9844)
   e encerra; NÃO transfere ticket; não responde dúvidas sobre vagas/seleção.

---

## Cenário 9 — Gestão dinâmica de curso sem redeploy (SC-004, US4)

1. `POST /admin/cursos` (Bearer ADMIN_TOKEN) com curso "HG Avançado" completo
   (apresentação + objeções + 1 turma).
2. **Expected**: `201` + `id`.
3. Iniciar conversa nova compatível com o curso.
4. **Expected**: o agente menciona/oferece o curso novo **sem redeploy**.
5. `POST /admin/cursos` sem token → **Expected**: `401`, nenhuma operação.
6. `DELETE /admin/cursos/{id}` → **Expected**: `204`; conversas novas não
   mencionam o curso (US4-AS4).

---

## Cenário 10 — Multilíngue + áudio (SC-007, US5)

1. Enviar mensagem de voz (`mediaType:"audio"`, `mediaUrl` .opus) em inglês
   "I'm interested in the online course".
2. **Expected**: o agente transcreve, responde **em inglês** e oferece o link em
   inglês (pay.hotmart.com/Q95039051K).
3. (error) Transcrição falha → **Expected**: agente informa que não conseguiu
   processar o áudio e pede repetição em texto (FR-005).
4. (variação) Lead troca de PT para EN no meio → **Expected**: agente passa a
   responder em inglês e mantém a preferência (US5-AS5).

---

## Cenário 11 — Anti-alucinação (SC-008, Princípio II)

1. Perguntar algo fora da Base Oficial (ex. "qual é o CNPJ da GoldIncision?").
2. **Expected**: resposta "não possuo essa informação" + encaminhamento a
   especialista; nunca estima/improvisa.

---

## Cenário 12 — fromMe e tipo desconhecido (edge cases)

1. Evento com `fromMe:true` → **Expected**: nenhuma resposta, nenhum efeito
   (FR-002).
2. Evento com `mediaType` fora de {text,audio,video,image,document} →
   **Expected**: descarte silencioso + log de aviso.

---

## Cenário 13 — Roundtrip End-to-End (validação de contrato inbound)

> Obrigatório (skill /plan §5.3): chamada REAL ao backend local, sem mock do
> parser, comparando o shape do payload contra `contracts/webhook-inbound.md`.

1. Subir o app local (FastAPI) e os exemplos de
   `knowledge_base/example_webhook_json/` (json_message, json_audio, json_video,
   json_document).
2. `POST /webhook/chatmaster` com cada payload real (sem alterar o JSON).
3. Capturar como o parser Pydantic interpreta cada um (campos extraídos:
   `sender`, `chamadoId`, `mensagem[].mediaType`, `mediaUrl`, `fromMe`,
   `ticketData.status`).
4. **Expected**: todos os exemplos reais são parseados sem erro; os campos
   extraídos batem com o contrato (nenhum campo obrigatório perdido por
   divergência de nome/case). Falha aqui = drift de contrato a corrigir antes de
   prosseguir.

---

## Cenário 14 — Empacotamento isolado (SC-006, US6)

1. `docker build -t registry.todo-tips.com/sdr-whatsapp:latest .` →
   **Expected**: build sem erros.
2. `docker push registry.todo-tips.com/sdr-whatsapp:latest` → **Expected**:
   imagem disponível no registry.
3. Inspecionar `stack.yml` → **Expected**: 3 serviços (app/postgres/redis) em
   overlay própria; só `app` na rede do Traefik; nenhum secret em texto claro
   (FR-032); healthcheck e labels Traefik presentes.
4. (NÃO executar `docker stack deploy` — fora de escopo desta entrega, FR-031.)
