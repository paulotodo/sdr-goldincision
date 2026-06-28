# Feature Specification: Agente SDR Consultivo de Atendimento via WhatsApp — GoldIncision

**Feature**: `sdr-whatsapp`
**Created**: 2026-06-28
**Status**: Draft

## Contexto

O Consultor Virtual Oficial da GoldIncision é um agente SDR de atendimento
via WhatsApp que conduz leads médicos pelo Mapa Mestre do Atendimento
oficial. Recebe mensagens inbound pelo webhook do ChatMaster (via n8n),
executa o fluxo conversacional com base na Base Oficial de Conhecimento
(anti-alucinação rígida) e envia respostas pela API oficial do ChatMaster.
Empacotado como stack Docker Swarm autocontida; entregue até build + push
da imagem no registry interno.

---

## User Scenarios & Testing

### User Story 1 — Lead Atendido pelo Mapa Mestre (Priority: P1)

Um médico entra em contato via WhatsApp. O agente identifica a intenção
do usuário na primeira mensagem: se clara (ex.: "quero o curso online"),
entra diretamente no fluxo correspondente sem apresentar o menu; se não
clara, exibe o menu inicial com as 6 opções (Curso Online, Cursos
Presenciais, Sistema GoldIncision, Aluno/suporte, Paciente modelo, Outro).
O agente conduz o caminho correto — qualificando elegibilidade médica,
coletando especialidade/experiência quando necessário, enviando
apresentações oficiais na íntegra, tratando objeções exclusivamente pelo
Banco Oficial, oferecendo o link de inscrição ou encaminhando ao consultor
especialista — tudo em um único fluxo contínuo, sem reapresentar menus
já respondidos e sem inventar informações ausentes da Base Oficial.

**Why this priority**: É o núcleo do produto. Sem o fluxo conversacional
fiel ao Mapa Mestre, o sistema não entrega valor algum. P0 de negócio.

**Independent Test**: Enviar mensagem "Quanto custa o curso online?" ao
webhook; verificar que o agente responde com o preço oficial (R$ 6.997,00)
sem perguntar "Você é médico?" antes, e depois oferece o link de inscrição
no idioma da conversa.

**Acceptance Scenarios**:

1. **Given** mensagem de entrada sem intenção clara, **When** o webhook é
   recebido, **Then** o agente envia o menu inicial com as 6 opções
   numeradas, em até 10 segundos.

2. **Given** mensagem "Quero o curso online", **When** recebida, **Then**
   o agente entra diretamente no Caminho 1 (qualificação médica) sem
   apresentar o menu; pergunta se é médico com registro ativo.

3. **Given** usuário confirmou ser médico (Caminho 1), **When** agente
   enviou a apresentação oficial do Curso Online, **Then** oferece o link
   de inscrição no idioma da conversa (PT: Hotmart, EN: pay.hotmart.com/Q95039051K, ES: pay.hotmart.com/N95711232T).

4. **Given** usuário não é médico (qualquer caminho), **When** informa
   não ter registro ativo, **Then** o agente informa que a formação é
   exclusiva para médicos, agradece o interesse e encerra sem oferecer
   alternativas.

5. **Given** Caminho 2 (presenciais), usuário confirma ser médico,
   **When** informa atuar apenas com harmonização facial, **Then** o agente
   considera como NÃO elegível ao HG360 e indica HG Módulo 1 (iniciantes).

6. **Given** Caminho 2, usuário tem especialidade em Dermatologia ou
   Cirurgia Plástica ou Cirurgia Vascular, **When** confirmado, **Then**
   agente indica HG360 e apresenta as duas turmas disponíveis (São Paulo e
   Barcelona) para o usuário escolher.

7. **Given** Caminho 3 (Sistema GoldIncision), **When** usuário menciona
   interesse, **Then** agente PRIMEIRO esclarece que a técnica GoldIncision
   NÃO é ensinada por curso avulso, depois apresenta Licenciamento e
   Franquia como os dois programas oficiais.

8. **Given** Caminho 4 (Aluno/suporte), **When** identificado, **Then**
   agente coleta a categoria de necessidade e informa que encaminhará à
   equipe responsável (sem tentar resolver a demanda).

9. **Given** Caminho 5 (Paciente modelo), **When** identificado, **Then**
   agente encaminha EXCLUSIVAMENTE para o WhatsApp da Nídia
   (+55 21 97423-9844) e não responde nenhuma dúvida sobre vagas/seleção.

10. **Given** usuário envia objeção comercial (ex.: "está muito caro"),
    **When** recebida em qualquer caminho, **Then** agente consulta e aplica
    EXCLUSIVAMENTE o Banco Oficial de Objeções do produto em atendimento;
    não improvisa resposta.

11. **Given** informação solicitada não existe na Base Oficial, **When**
    agente constatou a ausência, **Then** informa que não possui essa
    informação e encaminha para especialista humano.

---

### User Story 2 — Memória Persistente e Janelas Longas (Priority: P1)

Um lead retorna à conversa após horas ou dias. O agente recupera o contexto
completo da sessão anterior — intenção identificada, elegibilidade declarada,
idioma, etapa do funil — sem fazer perguntas já respondidas. Em conversas
longas no mesmo dia, o histórico é sumarizado de forma rolante para evitar
estouro de contexto do LLM, mas sem perder a linha narrativa da jornada.
O agente também consolida múltiplas mensagens curtas enviadas em sequência
(debounce) antes de responder.

**Why this priority**: A jornada sem atrito é princípio constitucional (III).
Repetir perguntas já respondidas é um dos anti-padrões mais irritantes e
explicitamente proibido pela Regra 9.

**Independent Test**: Enviar conversa com "sou médico, tenho experiência
em facial" (resposta: HG Módulo 1) → encerrar sessão → reabrir 30 min
depois → enviar "quero saber mais sobre o módulo" → verificar que o agente
NÃO pergunta novamente "você é médico?" nem "você tem experiência corporal?".

**Acceptance Scenarios**:

1. **Given** usuário enviou "sou médico" na mensagem anterior, **When**
   envia nova mensagem sobre o curso, **Then** o agente não faz novamente
   a pergunta de elegibilidade médica.

2. **Given** conversa ativa com 30+ mensagens de contexto, **When** nova
   mensagem é recebida, **Then** o agente responde de forma coerente com a
   jornada, sem erros de contexto, usando o resumo rolante.

3. **Given** usuário envia 3 mensagens curtas em menos de 8 segundos,
   **When** o debounce expira, **Then** o agente processa e responde apenas
   uma vez ao conjunto consolidado (não gera 3 respostas independentes).

4. **Given** novo ticket para o mesmo contato (mesmo número), **When**
   criado, **Then** o agente recupera as variáveis capturadas anteriormente
   (idioma, é médico, especialidade, etapa do funil) e as reutiliza.

5. **Given** usuário muda de assunto (ex.: falava sobre curso online e diz
   "na verdade tenho interesse no licenciamento"), **When** detectado,
   **Then** o agente redireciona imediatamente para o fluxo correspondente
   sem reiniciar do zero, preservando dados já coletados.

---

### User Story 3 — Handoff para Humano (Priority: P1)

Quando o fluxo determina encaminhamento (inscrição em curso presencial,
licenciamento, franquia, suporte de aluno) ou o usuário pede explicitamente
"quero falar com um humano", o agente transfere o ticket para a fila/conexão
correta do especialista humano via API de tickets e cessa de atuar naquele
ticket. Para casos de paciente modelo, o encaminhamento é exclusivamente
para o WhatsApp da Nídia.

**Why this priority**: Sem handoff correto, o agente tenta resolver demandas
fora do seu escopo, violando o Princípio V da Constitution.

**Independent Test**: Iniciar Caminho 2 como médico elegível ao HG Módulo 1,
responder "sim" para "Gostaria que eu encaminhasse seu interesse para um
consultor?"; verificar que a API de transferência foi chamada e o agente
não envia mais mensagens no ticket.

**Acceptance Scenarios**:

1. **Given** usuário no Caminho 1 responde "sim" para receber o link de
   inscrição, **When** link enviado, **Then** agente conclui o atendimento
   (Curso Online é auto-atendimento; handoff de ticket não é necessário
   aqui — apenas para presenciais/licenciamento/franquia).

2. **Given** usuário confirmou interesse em HG Módulo 1 ou HG360, **When**
   responde "sim" para encaminhamento ao consultor, **Then** agente chama
   a API de transferência de fila/conexão e para de responder no ticket.

3. **Given** usuário no Caminho 3 (Licenciamento ou Franquia), **When**
   apresentação enviada e usuário demonstra interesse em reunião, **Then**
   agente convida para reunião com especialista e transfere o ticket.

4. **Given** usuário diz "quero falar com um humano" a qualquer momento,
   **When** detectado, **Then** agente interrompe o fluxo atual e transfere
   o ticket imediatamente para o especialista correto.

5. **Given** ticket transferido, **When** agente tenta enviar nova mensagem
   no mesmo ticket, **Then** sistema NÃO envia (atendimento já sob
   responsabilidade humana).

6. **Given** mensagem identificada como de paciente modelo, **When**
   Caminho 5 identificado, **Then** agente envia APENAS o número da Nídia
   e encerra; NÃO transfere ticket de atendimento (canal é direto).

---

### User Story 4 — Gestão Dinâmica de Cursos via API de Admin (Priority: P1)

O operador (Paulo) adiciona um novo curso ao catálogo via API REST de admin:
informa nome, apresentação oficial (por idioma), banco de objeções, regras
de elegibilidade, turmas (datas e cidades), links de inscrição e mídias
associadas. Sem nenhum redeploy, o agente passa a oferecer o novo curso no
fluxo conversacional, consultando o catálogo em tempo de execução.

**Why this priority**: O catálogo de cursos muda sem que o operador precise
alterar código ou reiniciar o sistema (Princípio VII / Constitution). É
requisito crítico de extensibilidade.

**Independent Test**: Criar via API um curso fictício "HG Avançado" com
apresentação e banco de objeções; em seguida iniciar uma conversa de teste;
verificar que o agente menciona o novo curso quando o fluxo for compatível,
sem redeploy.

**Acceptance Scenarios**:

1. **Given** operador envia `POST /admin/cursos` com dados completos de
   um novo curso, **When** processado com sucesso, **Then** retorna 201 com
   o id do curso criado; o curso aparece no catálogo imediatamente.

2. **Given** curso criado, **When** lead elegível acessa o fluxo
   correspondente, **Then** o agente utiliza a apresentação oficial e o
   banco de objeções do novo curso sem redeploy.

3. **Given** operador envia `PUT /admin/cursos/{id}` com nova data de turma,
   **When** processado, **Then** o agente passa a mencionar a nova data nas
   conversas subsequentes.

4. **Given** operador envia `DELETE /admin/cursos/{id}`, **When** processado,
   **Then** o curso é removido do catálogo e conversas novas não o mencionam.

5. **Given** requisição à API de admin sem token válido, **When** recebida,
   **Then** retorna 401 e nenhuma operação é realizada.

6. **Given** `GET /admin/cursos`, **When** chamado com token válido, **Then**
   retorna lista completa de cursos ativos com seus dados (apresentações,
   elegibilidade, turmas, links).

7. **Given** seed inicial do sistema, **When** o banco está vazio, **Then**
   os cursos dos documentos oficiais (HG Módulo 1, HG360 SP, HG360 Barcelona,
   Curso Online, Licenciamento Internacional, Franquia) já estão pré-carregados.

---

### User Story 5 — Suporte Multilíngue e Envio de Mídias (Priority: P2)

Um médico estrangeiro envia mensagens em inglês ou espanhol. O agente
detecta o idioma, responde no mesmo idioma e, quando chega ao momento de
enviar a apresentação oficial do produto, envia a variante de idioma
correta do material (texto PT, EN ou ES). Para leads que enviam mensagens
de voz (áudio opus), o agente transcreve o áudio antes de processar.

**Why this priority**: P1 do briefing. Importante para leads internacionais
(Licenciamento e Franquia são disponíveis para mercado internacional), mas
não bloqueia o core do produto.

**Independent Test**: Enviar mensagem de voz (áudio opus) em inglês "I'm
interested in the online course"; verificar transcrição, resposta em inglês
e link de inscrição em inglês (pay.hotmart.com/Q95039051K).

**Acceptance Scenarios**:

1. **Given** usuário envia mensagens em inglês, **When** agente responde,
   **Then** todas as respostas do agente são em inglês, incluindo as
   apresentações oficiais na variante EN.

2. **Given** usuário envia mensagens em espanhol, **When** agente responde,
   **Then** todas as respostas são em espanhol, usando a variante ES dos
   materiais onde existir.

3. **Given** usuário envia mensagem de áudio (opus), **When** recebida,
   **Then** o agente transcreve o áudio e processa o texto resultante como
   se fosse uma mensagem de texto normal.

4. **Given** usuário solicita o link do Curso Online em inglês, **When**
   enviado, **Then** o link é o específico em inglês
   (pay.hotmart.com/Q95039051K), não o link em português.

5. **Given** usuário troca de idioma no meio da conversa (ex.: começa em PT
   e muda para EN), **When** detectado, **Then** o agente passa a responder
   em inglês e mantém essa preferência pelo restante da sessão.

6. **Given** tipo de mensagem inbound é `video`, `image` ou `document`
   (não áudio), **When** recebido, **Then** o agente reconhece o tipo de
   mídia e responde adequadamente (ex.: solicita que o usuário descreva o
   documento em texto se necessário, sem tentar processar o conteúdo binário).

---

### User Story 6 — Stack Isolada Pronta para Deploy (Priority: P2)

O operador recebe a imagem publicada no registry interno e um `stack.yml`
revisável, executa `docker stack deploy` por conta própria e o serviço
sobe com o agente respondendo via webhook, Postgres e Redis próprios
inicializados, e health check verde no Traefik. Nenhum serviço ou stack
existente é afetado.

**Why this priority**: Requisito de entregável, mas o deploy live é
responsabilidade do operador após revisão do `stack.yml`.

**Independent Test**: Copiar o `stack.yml` gerado para um ambiente de
staging equivalente; executar `docker stack deploy`; verificar que o
serviço sobe sem erros, o healthcheck responde 200 e o webhook responde
a uma mensagem de teste.

**Acceptance Scenarios**:

1. **Given** Dockerfile e código-fonte da aplicação presentes no repositório,
   **When** executado `docker build` e `docker push registry.todo-tips.com/sdr-whatsapp:latest`,
   **Then** build conclui sem erros e imagem disponível no registry.

2. **Given** imagem no registry, **When** `stack.yml` implantado via
   `docker stack deploy`, **Then** serviços `app`, `postgres` e `redis`
   sobem em rede overlay isolada; apenas o serviço `app` ingressa na
   rede do Traefik.

3. **Given** stack em execução, **When** Traefik roteia para o endpoint
   de healthcheck, **Then** resposta HTTP 200 dentro de 3 segundos.

4. **Given** stack em execução, **When** `docker stack ps` executado,
   **Then** nenhum container de stacks existentes (traefik, n8n, postgres
   compartilhado, etc.) aparece como alterado/reiniciado.

5. **Given** `stack.yml`, **When** inspecionado, **Then** não contém
   nenhum secret em texto claro; secrets (`OPENAI_API_KEY`, token
   ChatMaster) estão referenciados como Docker secrets ou variáveis
   de ambiente externos ao `stack.yml`.

6. **Given** stack derrubada via `docker stack rm sdr-whatsapp`, **When**
   executado, **Then** apenas os serviços da stack `sdr-whatsapp` são
   removidos; demais stacks e serviços permanecem intactos.

---

### User Story 7 — Observabilidade e Rastreabilidade (Priority: P3)

O operador acessa logs estruturados que permitem rastrear, por ticket,
o fluxo executado, as chamadas ao LLM (modelo, tokens, custo estimado),
os handoffs realizados e os erros ocorridos. Métricas básicas (mensagens
recebidas/enviadas, handoffs, latência de resposta) ficam disponíveis
para inspeção.

**Why this priority**: Útil para operação, mas não bloqueia o produto
funcional. P2 do briefing.

**Independent Test**: Processar uma conversa completa (Caminho 1: médico,
interesse no curso online, envia link); verificar nos logs estruturados
que há entradas para: recebimento do webhook, chamada ao LLM com tokens,
envio da resposta, custo estimado.

**Acceptance Scenarios**:

1. **Given** mensagem processada, **When** log consultado, **Then** há
   uma entrada estruturada (JSON) contendo: ticket_id, contact_number,
   stage (etapa do Mapa Mestre), model_used, tokens_in, tokens_out,
   latency_ms, timestamp.

2. **Given** handoff executado, **When** log consultado, **Then** há
   entrada com: ticket_id, handoff_type (fila/conexão), destino, motivo.

3. **Given** erro na chamada ao LLM ou API ChatMaster, **When** ocorrer,
   **Then** há entrada de log com: tipo de erro, detalhes, ticket_id,
   e o agente informa ao usuário que houve um problema técnico e tentará
   novamente (sem expor detalhes técnicos).

---

### Edge Cases

- **Mensagem `fromMe: true`**: o agente ignora completamente mensagens
  enviadas pelo próprio número/operador e não gera resposta.
- **Rajada de mensagens** (mais de 5 em sequência com < 2s de intervalo):
  debounce agrupa e processa uma única vez após a janela expirar.
- **Ticket já em handoff humano**: se o agente recebe webhook de um ticket
  já transferido para especialista, não responde (verifica estado do ticket
  antes de agir).
- **Tipo de mensagem desconhecido**: se tipo não for `text`, `audio`, `video`,
  `image` ou `document`, o agente ignora silenciosamente (sem erro
  visível ao usuário) e registra log de aviso.
- **Falha na transcrição de áudio**: se OpenAI Whisper retornar erro, o
  agente informa ao usuário que não conseguiu processar o áudio e pede
  que repita em texto.
- **Informação solicitada não existe na Base Oficial**: agente declara
  que não possui a informação e encaminha para especialista — nunca
  estima ou improvisa.
- **Usuário pergunta se o agente é humano**: agente declara ser o
  Consultor Virtual Oficial da GoldIncision; nunca afirma ser pessoa da
  equipe.
- **Múltiplos tickets do mesmo contato abertos simultaneamente**: sistema
  processa cada ticket pelo seu `chamadoId`; contexto de memória é por
  contato, mas estado de fluxo é por ticket.
- **Timeout de resposta do LLM (>30s)**: agente envia mensagem de
  "aguarde um momento" ao usuário e registra o evento; não deixa o
  usuário sem resposta.

---

## Requirements

### Functional Requirements

#### Recepção de Mensagens (Inbound)

- **FR-001**: O sistema DEVE expor endpoint webhook HTTP que aceite o
  payload do ChatMaster/Whaticket conforme estrutura documentada em
  `knowledge_base/example_webhook_json/` (campos: `mensagem[]`,
  `sender`, `chamadoId`, `acao`, `name`, `fromMe`, `companyId`,
  `queueId`, `ticketData`).

- **FR-002**: O sistema DEVE ignorar completamente mensagens onde
  `fromMe: true` sem gerar resposta ou efeito colateral.

- **FR-003**: O sistema DEVE implementar debounce configurável
  (default 8 segundos) agrupando mensagens consecutivas do mesmo
  contato antes de processar como uma única entrada.

- **FR-004**: O sistema DEVE suportar os tipos de mensagem inbound:
  `text`, `audio` (opus — via `mediaUrl`), `video`, `image`,
  `document`. Tipos desconhecidos são descartados silenciosamente.

- **FR-005**: Para mensagens de áudio (`mediaType: audio`), o sistema
  DEVE transcrever o arquivo antes de encaminhar ao motor conversacional.
  Em caso de falha na transcrição, DEVE informar o usuário e solicitar
  repetição em texto.

#### Motor Conversacional (Mapa Mestre)

- **FR-006**: O sistema DEVE implementar fielmente os 6 caminhos do
  Mapa Mestre do Atendimento (`knowledge_base/documentos_agente/MAPA
  MESTRE DO ATENDIMENTO.docx`): (1) Curso Online, (2) Cursos
  Presenciais, (3) Sistema GoldIncision, (4) Aluno/suporte, (5)
  Paciente modelo, (6) Outro.

- **FR-007**: O sistema DEVE identificar a intenção do usuário na
  primeira mensagem. Se a intenção estiver clara, entrar diretamente
  no fluxo correspondente sem apresentar o menu inicial.

- **FR-008**: O sistema DEVE consultar a Base Oficial de Conhecimento
  na hierarquia: Mapa Mestre → Base Oficial do produto → Banco de
  Objeções → FAQ. NUNCA gerar respostas fora desta hierarquia.

- **FR-009**: O sistema DEVE verificar elegibilidade médica em todos
  os caminhos que a exigem (Cursos 1, 2; Licenciamento). Resposta
  "apenas facial" = NÃO elegível ao HG360. Elegibilidade nunca é
  flexibilizada.

- **FR-010**: O sistema DEVE enviar apresentações oficiais na íntegra,
  sem reescrever, resumir ou adaptar o texto dos documentos oficiais.

- **FR-011**: O sistema DEVE aplicar EXCLUSIVAMENTE o Banco Oficial
  de Objeções do produto em atendimento para tratar objeções
  comerciais. Nenhuma resposta improvisada a objeções é permitida.

- **FR-012**: O sistema DEVE responder no mesmo idioma do usuário
  (português, inglês ou espanhol) e enviar links e apresentações na
  variante de idioma correta.

- **FR-013**: O sistema DEVE declarar-se "Consultor Virtual Oficial
  da GoldIncision" quando questionado sobre sua identidade.

- **FR-014**: Para o Caminho 5 (Paciente Modelo), o sistema DEVE
  encaminhar EXCLUSIVAMENTE para o WhatsApp da Nídia (+55 21 97423-9844)
  e nunca responder dúvidas sobre vagas, seleção, valores ou datas de
  pacientes modelo.

- **FR-015**: O sistema DEVE enviar respostas em blocos curtos (Regra
  13), fazendo apenas uma pergunta por mensagem (exceto menus).

#### Envio de Mensagens (Outbound)

- **FR-016**: O sistema DEVE enviar mensagens via
  `POST https://api2.chatmasterveloz.com/api/messages/sendOfficialData`
  com header `Authorization: Bearer <token>` e body
  `{"number": "<DDI+DDD+num>", "text": "<conteúdo>"}`.

- **FR-017**: O sistema DEVE suportar o envio de links de inscrição
  (Hotmart PT/EN/ES) e botões de quick reply quando o fluxo determinar
  apresentação de opções ao usuário.

#### Memória e Estado

- **FR-018**: O sistema DEVE persistir o histórico completo de cada
  conversa por contato/ticket em armazenamento persistente.

- **FR-019**: O sistema DEVE manter resumo rolante da conversa para
  sustentar janelas longas sem estourar o contexto do LLM.

- **FR-020**: O sistema DEVE persistir e reutilizar variáveis
  capturadas: idioma, elegibilidade médica (é médico?), especialidade,
  experiência em harmonização corporal, produto de interesse, etapa
  atual do funil.

- **FR-021**: O sistema DEVE nunca repetir perguntas já respondidas
  na mesma sessão ou em sessões anteriores do mesmo contato.

#### Handoff Humano

- **FR-022**: O sistema DEVE transferir o ticket para a fila/conexão
  do especialista correto via API de tickets
  (`https://clihelper.chatmasterveloz.com/principal/apis/ticket/`)
  quando o fluxo determinar encaminhamento ou o usuário solicitar
  atendimento humano explicitamente.

- **FR-023**: Após transferência do ticket, o sistema NÃO DEVE enviar
  mais mensagens naquele ticket.

- **FR-024**: O sistema DEVE verificar o estado do ticket antes de
  responder; se já transferido para humano, não atuar.

#### Gestão de Cursos (Admin API)

- **FR-025**: O sistema DEVE expor API REST de admin protegida por
  token com operações CRUD para cursos e seus artefatos: nome,
  apresentação oficial (por idioma), banco de objeções, regras de
  elegibilidade, turmas (data/cidade), links de inscrição por idioma,
  mídias associadas.

- **FR-026**: O motor conversacional DEVE ler o catálogo de cursos
  do banco de dados em tempo de execução, sem necessidade de redeploy
  ao adicionar ou remover cursos.

- **FR-027**: O sistema DEVE executar seed inicial do catálogo a partir
  dos documentos oficiais da `knowledge_base/documentos_agente/`, pré-
  carregando: Curso Online de HG, HG Módulo 1, HG360 São Paulo, HG360
  Barcelona, Licenciamento Internacional, Franquia GoldIncision.

#### Empacotamento e Infraestrutura

- **FR-028**: O sistema DEVE ser empacotado como stack Docker Swarm
  autocontida com: serviço de aplicação, Postgres próprio, Redis
  próprio, em rede overlay própria.

- **FR-029**: O serviço de aplicação DEVE ingressar na rede do Traefik
  apenas para roteamento HTTP. Os serviços Postgres e Redis NÃO devem
  ser expostos a redes externas.

- **FR-030**: O sistema DEVE expor endpoint de healthcheck que o Traefik
  e o Swarm possam verificar periodicamente.

- **FR-031**: O entregável DEVE incluir: Dockerfile funcional, `stack.yml`
  revisável com todos os serviços configurados, labels Traefik corretos.
  O pipeline VAI ATÉ build + push no `registry.todo-tips.com`.
  O `docker stack deploy` NÃO é executado pelo agente.

- **FR-032**: Secrets sensíveis (`OPENAI_API_KEY`, token ChatMaster)
  NUNCA devem aparecer em texto claro no `stack.yml`, Dockerfile ou
  no repositório git. Devem ser referenciados via Docker secrets ou
  variáveis de ambiente fornecidas externamente.

#### Observabilidade

- **FR-033**: O sistema DEVE emitir logs estruturados (JSON) contendo
  no mínimo: `ticket_id`, `contact_number`, `stage`, `timestamp`,
  `latency_ms`. Para chamadas ao LLM: `model_used`, `tokens_in`,
  `tokens_out`.

- **FR-034**: O sistema DEVE registrar eventos de handoff com:
  `ticket_id`, `handoff_type`, `destino`, `motivo`.

#### Decisões de Infraestrutura Auditáveis

- **FR-035-INFRA-MUTEX**: Para debounce e estado de sessão Redis,
  operações de leitura/escrita concorrentes no mesmo ticket DEVEM ser
  serializadas (via TTL de lock Redis ou transação atômica). Risco de
  resposta duplicada em rajada alta sem serialização.

- **FR-036-INFRA-SCHED**: Não há scheduler periódico nesta feature. O
  agente é ativado exclusivamente por webhook inbound.
  `autoSchedule = 'none'`.

- **FR-037-INFRA-IDEMP**: O webhook DEVE ser idempotente por
  `chamadoId` + hash do conteúdo da mensagem: reenvio do mesmo
  evento pelo n8n não deve gerar resposta duplicada ao usuário.
  TTL da chave de idempotência: 24 horas.

### Key Entities

- **Ticket**: unidade de atendimento, identificada por `chamadoId`/
  `ticketData.id`. Contém: número do contato, estado do fluxo atual,
  etapa do Mapa Mestre, status (aberto / em handoff / encerrado).

- **Contato**: identificado pelo número (`sender`). Armazena variáveis
  persistentes de qualificação: idioma detectado, elegibilidade médica,
  especialidade, experiência corporal, produto de interesse.

- **Sessão de Conversa**: histórico de mensagens de um ticket. Inclui
  mensagens completas para janela curta e resumo rolante para janela longa.

- **Curso**: item do catálogo gerenciado pelo admin. Atributos: id, nome,
  tipo (online/presencial), elegibilidade, apresentações por idioma,
  banco de objeções por idioma, turmas (data, cidade, vagas), links de
  inscrição por idioma, mídias, ativo (bool).

- **Turma**: instância específica de um curso presencial. Atributos:
  data de início, cidade, país, capacidade, vagas disponíveis, lote de
  preço vigente.

- **Banco de Objeções**: coleção de pares objeção/resposta associados
  a um curso específico. Consultado pelo motor antes de qualquer
  resposta a objeção comercial.

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: Um lead que envia mensagem com intenção clara (ex.:
  "Quanto custa o curso online?") recebe resposta correta baseada na
  Base Oficial em menos de 15 segundos, sem ser requalificado
  desnecessariamente.

- **SC-002**: Conversas com mais de 50 mensagens mantêm contexto
  coerente — o agente não repete perguntas já respondidas em nenhuma
  das 50 mensagens anteriores.

- **SC-003**: 100% das solicitações de handoff (explícitas ou por fluxo)
  resultam em transferência de ticket realizada antes de o agente
  enviar qualquer outra mensagem.

- **SC-004**: O operador adiciona um novo curso completo (com
  apresentação, objeções e uma turma) via API de admin em menos de
  5 minutos; conversas novas passam a refletir o curso sem nenhum
  redeploy.

- **SC-005**: O sistema processa rajadas de até 5 mensagens em
  sequência (< 2s de intervalo) como uma única entrada, gerando
  exatamente uma resposta consolidada.

- **SC-006**: A `stack.yml` gerada sobe em ambiente Docker Swarm
  equivalente sem alterações manuais; nenhum serviço pré-existente é
  afetado.

- **SC-007**: Mensagens de áudio em português, inglês ou espanhol são
  transcritas corretamente em mais de 90% dos casos; a resposta é
  entregue no mesmo idioma do áudio.

- **SC-008**: O agente nunca emite informação fora da Base Oficial. Em
  testes de pergunta-fora-do-escopo (ex.: "qual é o CNPJ da
  GoldIncision?"), a resposta é sempre "não possuo essa informação"
  + encaminhamento para especialista.

## Clarifications

> Nenhuma ambiguidade crítica identificada. Os documentos oficiais da
> GoldIncision e o briefing cobrem os requisitos com suficiente precisão.
> Todos os detalhes de implementação (tecnologias, estrutura de código,
> DDL, endpoints exatos) serão resolvidos na fase `/plan`.
