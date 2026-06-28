# Briefing & Discovery — Agente SDR de Atendimento WhatsApp (GoldIncision)

> Documento fundacional do projeto-alvo. Fonte de verdade para a pipeline SDD
> (specify → clarify → plan → checklist → create-tasks → execute-task → review-task).

## Visão

Construir o **Consultor Virtual Oficial da GoldIncision**: um agente SDR
(Sales Development Representative) de atendimento via WhatsApp que conduz cada
lead por um fluxo de atendimento consultivo, premium e sem atrito, qualificando
o contato e encaminhando-o ao produto, formação ou especialista humano mais
adequado ao seu perfil.

O agente é **consultivo, não vendedor agressivo**: o objetivo central é prestar
um atendimento correto, profissional e elegante, conduzindo o usuário pelo
**Mapa Mestre do Atendimento** oficial, respondendo exclusivamente com base nos
documentos oficiais da marca (anti-alucinação rígida) e garantindo uma jornada
fluida — sem repetir perguntas já respondidas e sem o comportamento robótico de
exigir requalificação a cada mensagem.

O sistema roda como serviço containerizado no **Docker Swarm já existente**,
isolado dos demais serviços, com imagem gerida pelo **registry interno** e
exposição via **Traefik**.

## Usuários-alvo

1. **Leads / Médicos (usuário final no WhatsApp)** — médicos com registro ativo
   interessados em formações de Harmonização Glútea (curso online ou presenciais)
   ou no Sistema GoldIncision (Licenciamento/Franquia). Também investidores
   (apenas para Franquia). Conversam em português, inglês ou espanhol. Esperam
   atendimento rápido, cordial, premium e sem fricção.
2. **Alunos** — já matriculados, precisam de suporte (acesso, certificado, grupo
   técnico, pagamento, dúvidas). São triados e encaminhados à equipe humana.
3. **Pacientes modelo** — sempre encaminhados ao WhatsApp oficial da Nídia
   (+55 21 97423-9844). O agente nunca responde dúvidas dessa natureza.
4. **Operador / Administrador (cliente: Paulo — paulo@todo-tips.com)** —
   gerencia o catálogo de cursos (adicionar/editar/remover cursos, apresentações,
   bancos de objeções e regras de elegibilidade) via API REST de admin protegida
   por token; acompanha métricas e logs.
5. **Especialistas humanos / Consultores GoldIncision** — recebem os tickets
   encaminhados (handoff) para dar continuidade a inscrições, licenciamento e
   franquia.

## Escopo funcional

### 1. Recepção de mensagens (inbound)
- Endpoint webhook HTTP que recebe eventos da plataforma ChatMaster/Whaticket
  (`chatmasterveloz`) repassados via n8n. Estrutura real em
  `knowledge_base/example_webhook_json/` (campos: `mensagem[]`, `sender`,
  `chamadoId`, `acao`, `name`, `fromMe`, `companyId`, `queueId`, `ticketData{...}`).
- Suporte a tipos de mensagem: `text`, `audio` (opus), `video`, `document`,
  `image` — com `mediaUrl`. Áudio do lead deve ser transcrito (OpenAI Whisper)
  para entrar no fluxo conversacional.
- **Debounce / agrupamento de rajadas**: o usuário costuma enviar várias
  mensagens curtas em sequência. O agente deve aguardar uma janela curta
  (configurável, ~5–10s) e consolidar antes de responder, evitando respostas
  fragmentadas e duplicadas.
- Ignorar mensagens `fromMe: true` (enviadas pelo próprio número/operador).

### 2. Motor conversacional consultivo (LLM)
- LLM: **OpenAI (GPT)**, com modelo de raciocínio para o fluxo e modelo barato
  para tarefas auxiliares (classificação de intenção, sumarização). Chave lida de
  secret `OPENAI_API_KEY` (nunca versionada).
- Implementa fielmente o **Mapa Mestre do Atendimento**
  (`knowledge_base/documentos_agente/MAPA MESTRE DO ATENDIMENTO.docx`):
  - **Identificação da intenção** primeiro; menu inicial só quando a intenção não
    estiver clara. Se o usuário já chega com intenção clara, entrar direto no
    fluxo correspondente (sem reapresentar menu, sem requalificar à toa).
  - **Caminho 1** — Curso Online de Harmonização Glútea (qualificação: médico?).
  - **Caminho 2** — Cursos Presenciais (qualificação médico → experiência →
    especialidade → trilha HG Módulo 1 / HG360 SP ou Barcelona).
  - **Caminho 3** — Sistema GoldIncision (Licenciamento / Franquia): qualificar e
    conduzir a reunião com especialista; nunca vender; explicar que a técnica
    GoldIncision não é ensinada por curso avulso.
  - **Caminho 4** — Aluno/suporte: triar e encaminhar à equipe.
  - **Caminho 5** — Paciente modelo: encaminhar à Nídia.
  - **Caminho 6** — Outro assunto.
- Consulta a base de conhecimento na hierarquia oficial: Mapa Mestre → Base
  Oficial do produto → Banco de Objeções → FAQ. Nunca inventa, estima ou
  completa lacunas; quando a informação não existe, informa e encaminha a humano.
- Suporte multilíngue: responde sempre no idioma do usuário (PT/EN/ES). Envia
  links e apresentações na variante de idioma correta.

### 3. Envio de mensagens (outbound)
- API oficial de envio:
  `POST https://api2.chatmasterveloz.com/api/messages/sendOfficialData`
  header `Authorization: Bearer <token>`, body `{ "number": "<DDI+DDD+num>",
  "text": "<conteúdo>" }`. Requer janela de atendimento aberta.
- Demais endpoints (documentados em
  `knowledge_base/example_webhook_json/outbound/links_documentacao_api.txt`):
  envio de template (com/sem variável), imagem/áudio/vídeo/documento por URL,
  botões (URL e quick reply). O agente deve poder enviar links de inscrição,
  apresentações e botões de opção do menu.
- Quebrar mensagens longas em blocos curtos (Regra 13: respostas curtas; uma
  pergunta por mensagem, exceto quando o fluxo prevê múltiplas opções).

### 4. Gerenciamento de tickets e handoff humano
- API de tickets (`https://clihelper.chatmasterveloz.com/principal/apis/ticket/`):
  criar, obter, atualizar, atualizar tags, encerrar, **transferir para fila** e
  **transferir entre conexões**.
- Handoff: quando o fluxo determinar encaminhamento (inscrição presencial,
  licenciamento, franquia, suporte de aluno, ou pedido explícito de humano), o
  agente transfere o ticket para a fila/conexão do especialista e para de atuar
  naquele ticket. Para paciente modelo, direciona à Nídia.
- O ticket de entrada traz `chamadoId`/`ticketData.id`, `queueId`, `companyId` —
  usados para correlacionar conversa, ticket e contato.

### 5. Memória de conversa e janelas longas
- Persistir histórico completo por contato/ticket em **Postgres** (próprio da
  stack). Manter **resumo rolante (rolling summary)** da conversa para sustentar
  janelas longas sem estourar contexto do LLM nem custo.
- **Redis** (próprio da stack) para janela curta / debounce / cache de sessão e
  estado de fluxo corrente (etapa do Mapa Mestre em que o lead está).
- Nunca repetir perguntas já respondidas (Regra 9). Persistir variáveis
  capturadas (idioma, é médico?, especialidade, experiência, produto de
  interesse, etapa do funil).

### 6. Gestão dinâmica de cursos (adicionar/remover)
- **API REST de admin (CRUD)** protegida por token, com tabela em Postgres.
  Permite criar/editar/remover **cursos** e seus artefatos: apresentação oficial
  (texto exato, por idioma), banco de objeções, regras de elegibilidade, turmas
  (datas/cidades), links de inscrição e mídias.
- Cursos são **dados, não código**: adicionar/remover um curso não exige novo
  deploy. O motor conversacional lê o catálogo do banco em tempo de execução.
- Seed inicial a partir dos documentos oficiais em
  `knowledge_base/documentos_agente/` (apresentações, bancos de objeções, FAQ,
  Mapa Mestre, Regras Gerais).

### 7. Observabilidade
- Logs estruturados, rastreio de tokens/custo do LLM, métricas básicas
  (mensagens in/out, handoffs, latência) e healthcheck para o Swarm/Traefik.

## Integrações externas (whitelist)

- `api2.chatmasterveloz.com` — envio de mensagens/mídias/botões (API oficial).
- `clihelper.chatmasterveloz.com` — documentação e APIs de tickets/CRM.
- `api.openai.com` — LLM (chat + transcrição de áudio).
- `object.sp2.eveo.com.br` — download de mídias recebidas (áudio/vídeo/documento).
- `hotmart.com` / `pay.hotmart.com` — links de inscrição do curso online.
- Registry interno: `registry.todo-tips.com` — gestão de imagem.

## Infraestrutura existente (alvo de deploy)

- **Docker Swarm ativo** (este nó é manager). Versão Docker 29.x.
- **Registry** interno: `registry.todo-tips.com` (stack `infra-registry`,
  imagem `registry:2`).
- **Traefik** v3.5.3 (stack `traefik_traefik`) como reverse-proxy/roteador.
- **Postgres** e **n8n** já em produção (o webhook chega via `n8n.todo-tips.com`).
- Stack de referência: `fast-api-homologacao` (Python/FastAPI, imagem no
  registry, atrás do Traefik) — padrão a espelhar.
- Redes overlay existentes incluem `network_main` (provável rede pública do
  Traefik) — a confirmar na fase de plan via inspeção do serviço Traefik.

## Restrições

- **Isolamento absoluto**: a solução NÃO pode alterar, reiniciar ou interferir em
  nenhum container/serviço/stack existente. Stack própria, autocontida
  (app + Postgres próprio + Redis próprio), rede overlay própria; só ingressa na
  rede do Traefik para roteamento. Não tocar no Postgres/Redis compartilhados.
- **Deploy**: nesta entrega, ir até **build + push no `registry.todo-tips.com`**
  e gerar o `stack.yml` revisável. **NÃO** executar `docker stack deploy` (deploy
  live fica a cargo do operador após revisão).
- **Anti-alucinação (Regras Gerais do Agente)**: responder exclusivamente com a
  Base Oficial; nunca inventar valores, políticas, contratos ou orientação
  médica/técnica; quando faltar informação, encaminhar a humano.
- **Privacidade/segredos**: token do ChatMaster e `OPENAI_API_KEY` via Docker
  secrets / variáveis de ambiente; nunca versionar segredos no repositório.
  (O token de Authorization fornecido pelo operador será injetado como secret no
  deploy, não escrito em código nem em git.)
- **Hierarquia documental** (Regra 5): Mapa Mestre → Base Oficial → Banco de
  Objeções → FAQ. Em conflito, prevalecem os documentos oficiais (Regra 30).
- **Comunicação premium**: cordial, profissional, objetiva, elegante; emojis
  moderados; respostas curtas; uma pergunta por mensagem (salvo menus).
- **Elegibilidade inflexível**: cursos/licenciamento exclusivos para médicos com
  registro ativo; experiência exclusivamente facial não conta como Harmonização
  Corporal; nunca flexibilizar critérios.
- **Idioma do projeto/código**: documentação e mensagens ao operador em
  português; identificadores/código no idioma original.
- O agente nunca se identifica como pessoa da equipe; quando perguntado, declara
  ser o Consultor Virtual Oficial da GoldIncision (Regra 27).
- Projeto-alvo reside em `/root/sdr-goldincision` (fora de zonas proibidas do
  path-guard); documentos-fonte espelhados em `knowledge_base/`.

## Prioridades

1. **P0 — Núcleo conversacional fiel ao Mapa Mestre + anti-alucinação**: receber
   webhook, identificar intenção, conduzir os caminhos 1–6, responder pela Base
   Oficial e enviar resposta via API oficial. É o coração do produto.
2. **P0 — Memória eficiente e janelas longas**: histórico em Postgres + resumo
   rolante + Redis; nunca repetir perguntas; persistir estado do funil.
3. **P0 — Handoff humano correto**: transferência de ticket para
   fila/especialista e direcionamentos fixos (Nídia / consultor).
4. **P0 — Gestão dinâmica de cursos (CRUD admin)**: adicionar/remover cursos sem
   redeploy; seed dos documentos oficiais.
5. **P1 — Multilíngue (PT/EN/ES)** e envio de links/apresentações por idioma.
6. **P1 — Empacotamento Swarm isolado**: Dockerfile, stack.yml, secrets, build +
   push no registry; healthcheck e labels de Traefik. Sem deploy live.
7. **P2 — Mídia inbound (transcrição de áudio), botões/quick replies,
   observabilidade (métricas/custo/tokens)**.

## Critérios de sucesso

- Um lead que envia "Quanto custa o curso online?" recebe resposta correta da
  Base Oficial sem ser requalificado de forma robótica, e o fluxo segue para o
  fechamento (oferta do link de inscrição no idioma certo).
- Conversas longas mantêm contexto coerente sem repetir perguntas e sem estourar
  custo/contexto do LLM.
- Operador adiciona um novo curso (com apresentação, objeções, elegibilidade e
  turmas) via API de admin e o agente passa a oferecê-lo sem redeploy.
- Pedidos de handoff (inscrição presencial, licenciamento, franquia, suporte,
  paciente modelo, "quero falar com humano") resultam em transferência/encaminhe
  corretos.
- A stack sobe isolada (Postgres + Redis próprios), imagem publicada no registry,
  `stack.yml` pronto para revisão — sem afetar nenhum serviço existente.
- O agente nunca emite informação fora da Base Oficial; lacunas viram handoff.
