# Feature Specification: Controle de Turnos Robusto e Observabilidade de Turno

**Feature**: `sdr-turnos-obs`
**Created**: 2026-07-01
**Status**: Draft

## Contexto

O Consultor Virtual da GoldIncision (agente SDR via WhatsApp) já implementa
diversos mecanismos de controle de conversa (contador anti-loop por etapa,
teto de mensagens por reação, pacing de envio, idempotência, lock por
ticket, gate de fila, debounce de rajada). Uma avaliação contra os 8 pilares
da metodologia "Agente Confiável" identificou que o **Pilar 7 (controle de
turnos)** está parcialmente coberto e o **Pilar 8 (observabilidade e
avaliação)** carece de registro por-turno e de um conjunto de casos de
regressão (golden set). Esta feature fecha os gaps remanescentes de
controle de turnos (G1–G6) e torna cada turno observável, **sem refazer**
nenhum mecanismo já existente.

> Decisões de infraestrutura: aplicável — feature introduz política de
> contadores efêmeros em Redis, timeout lazy, recovery de fila no startup e
> ajuste de TTL de lock. Ver FR-INFRA abaixo.

## User Scenarios & Testing

### User Story 1 - Orçamento de turnos com escalonamento gracioso (Priority: P1)

Um lead permanece muitas mensagens numa mesma etapa (por exemplo, fazendo
perguntas repetidas ou sem convergir) ou estende a conversa por muitos
turnos ao longo de toda a jornada. O sistema deve reconhecer esse padrão e
oferecer, primeiro, uma saída graciosa (oferecer conectar com um
especialista) e, no limite de segurança da sessão inteira, encaminhar o
atendimento a um humano — sem nunca deixar a conversa girar indefinidamente
nem cortar abruptamente uma dúvida legítima.

**Why this priority**: É o gap mais crítico verificado (G1) — hoje só existe
contador de respostas não-reconhecidas por etapa, que zera ao avançar; um
lead pode ficar preso indefinidamente em uma etapa respondendo "algo" a
cada turno (sempre reconhecido) sem jamais disparar escalonamento.

**Independent Test**: A partir de uma sessão em uma etapa qualquer, simular
turnos sucessivos reconhecidos pelo motor até atingir o teto de nó — deve
resultar em nudge contextual (não handoff). Continuar até o teto de sessão —
deve resultar em handoff ao destino lógico do caminho corrente.

**Acceptance Scenarios**:

1. **Given** uma sessão na etapa X com contador de turnos-no-nó abaixo do
   teto, **When** o lead envia mais um turno reconhecido, **Then** o
   contador incrementa e o fluxo segue normalmente, sem intervenção.
2. **Given** uma sessão na etapa X que atinge o teto de turnos-no-nó,
   **When** o próximo turno é processado, **Then** o sistema emite um
   nudge cordial oferecendo conexão com especialista, sem encerrar o
   atendimento nem impedir o lead de continuar.
3. **Given** uma sessão que atinge o teto de turnos-de-sessão, **When** o
   próximo turno é processado, **Then** o sistema encaminha o atendimento
   ao destino lógico do caminho atual (nunca decidido pelo modelo de
   linguagem), registrando o motivo do encaminhamento.
4. **Given** uma sessão na etapa de dúvidas abertas, **When** o lead
   acumula turnos além do teto padrão de nó mas abaixo do teto elevado
   específico de dúvidas, **Then** nenhum nudge é disparado (a etapa de
   dúvidas tolera mais turnos antes de sugerir o especialista).
5. **Given** o mecanismo de contagem de respostas-não-reconhecidas já
   existente, **When** um turno reconhecido é processado, **Then** o
   contador de turnos desta feature incrementa independentemente do
   contador de tentativas não-reconhecidas (os dois não se fundem nem se
   substituem).

---

### User Story 2 - Retomada e expiração de sessão por inatividade (Priority: P1)

Um lead interrompe a conversa e retorna depois de um tempo. Se o retorno for
em algumas horas, o sistema deve reconhecer a pausa e retomar de onde parou,
de forma cordial. Se o retorno for muitos dias depois, o sistema deve tratar
como um novo atendimento — mas sem esquecer quem é o lead (perfil, idioma,
especialidade já capturados não são re-perguntados).

**Why this priority**: Gap G2 — hoje não há qualquer noção de inatividade;
o bot sempre retoma exatamente no meio do fluxo, mesmo depois de dias,
soando robótico e desatualizado.

**Independent Test**: A partir de uma sessão em fluxo com timestamp de
última interação simulado no passado, dois cenários: (a) gap moderado →
mensagem de retomada sem perda de contexto; (b) gap muito longo → reinício
de etapa com perfil preservado.

**Acceptance Scenarios**:

1. **Given** uma sessão em fluxo cuja última interação ocorreu há mais que
   o limiar de reengajamento (e menos que o limiar de expiração), **When**
   o lead envia uma nova mensagem, **Then** o sistema reconhece o gap com
   uma retomada cordial antes de prosseguir, sem reiniciar a jornada.
2. **Given** uma sessão cuja última interação ocorreu há mais que o
   limiar de expiração de sessão, **When** o lead envia uma nova mensagem,
   **Then** o sistema trata como sessão nova (retorna à saudação inicial)
   **preservando** os dados de perfil já capturados (é médico, idioma,
   especialidade, experiência) e não repete perguntas já respondidas.
3. **Given** uma sessão com última interação recente (abaixo de ambos os
   limiares), **When** o lead envia uma nova mensagem, **Then** nenhuma
   mensagem de retomada é emitida — comportamento inalterado.

---

### User Story 3 - Durabilidade do agrupamento de mensagens em restart (Priority: P2)

O sistema agrupa (debounce) rajadas de mensagens do lead por alguns segundos
antes de processar, para evitar responder a mensagens fragmentadas. Se o
processo reiniciar (deploy, crash) exatamente durante essa janela de
agrupamento, as mensagens pendentes não podem ser perdidas nem ficar "presas"
até a próxima mensagem do lead — devem ser processadas assim que o sistema
volta a operar.

**Why this priority**: Gap G3 — perda silenciosa de turno é o pior tipo de
falha (o lead não recebe resposta e não sabe por quê); porém tem menor
frequência de ocorrência que G1/G2 (só se manifesta durante deploy).

**Independent Test**: Simular uma rajada pendente de agrupamento persistida,
reiniciar o processo, e confirmar que o agrupamento pendente é processado
exatamente uma vez (nem perdido, nem duplicado).

**Acceptance Scenarios**:

1. **Given** uma rajada de mensagens agrupada mas ainda não processada no
   momento de um restart, **When** o sistema volta a operar, **Then** a
   rajada pendente é processada automaticamente, sem exigir nova mensagem
   do lead.
2. **Given** a janela de agrupamento já havia expirado no momento do
   restart, **When** o sistema volta a operar, **Then** o processamento
   ocorre imediatamente na inicialização.
3. **Given** uma rajada pendente processada durante a recuperação de
   restart, **When** por qualquer motivo o processo de recuperação for
   executado mais de uma vez, **Then** a rajada é processada exatamente
   uma vez (idempotência preservada).

---

### User Story 4 - Robustez de concorrência durante turno lento (Priority: P2)

Alguns turnos demoram mais que o normal (resposta do modelo de linguagem
lenta, múltiplos envios pacing-limitados, retentativas por limite de taxa).
O mecanismo que impede processamento concorrente do mesmo atendimento não
pode expirar no meio de um turno legítimo, sob risco de o mesmo turno ser
processado duas vezes.

**Why this priority**: Gap G4 — risco real mas de menor frequência que G1/G2;
directamente relacionado à confiabilidade do sistema sob carga variável.

**Independent Test**: Simular um turno cuja duração de processamento se
aproxima do pior caso conhecido (resposta lenta + múltiplos envios +
retentativas) e confirmar que o mecanismo de exclusividade por atendimento
continua válido até o fim do processamento.

**Acceptance Scenarios**:

1. **Given** um turno em processamento cuja duração se aproxima do pior
   caso estimado, **When** o processamento é concluído, **Then** o
   mecanismo de exclusividade por atendimento continuou válido durante
   toda a duração (não expirou prematuramente).
2. **Given** a decisão de elevar o teto do mecanismo de exclusividade,
   **When** a duração real de turnos é medida via observabilidade (US5),
   **Then** o valor fixado é justificado por dados observados, não por
   suposição.

---

### User Story 5 - Observabilidade de cada turno (Priority: P1)

Cada turno de conversa processado pelo sistema deve gerar um registro
estruturado que permita, posteriormente, responder perguntas como "quantos
turnos até o handoff?", "em que etapas os leads mais travam?", "a mudança
de prompt piorou o fluxo?". Sem esse registro, ajustar os limiares das
demais stories (US1–US4) depende de intuição, não de dados.

**Why this priority**: Habilita ajuste orientado por dado de todos os
demais limiares (P1 pois sem isso as decisões de tuning ficam às cegas);
também é pré-requisito para a US6 (golden set).

**Independent Test**: Processar um turno completo (do recebimento da
mensagem até a resposta) e verificar que um único registro estruturado foi
emitido contendo identificação do atendimento, contadores de turno, etapas
de entrada/saída, intenção, idioma, quantidade de blocos enviados, ação
tomada, destino de encaminhamento (se houve) e duração.

**Acceptance Scenarios**:

1. **Given** um turno processado com sucesso, **When** o processamento
   termina, **Then** exatamente um registro estruturado de turno é emitido
   contendo todos os campos definidos em FR-011.
2. **Given** um turno que resulta em nudge (US1), **When** o registro é
   emitido, **Then** o campo de ação reflete o nudge e o motivo.
3. **Given** um turno que resulta em handoff (US1 ou elegibilidade),
   **When** o registro é emitido, **Then** o campo de destino de
   encaminhamento está preenchido com o destino lógico usado.

---

### User Story 6 - Conjunto de regressão de jornada (golden set) (Priority: P2)

A equipe precisa de uma forma rápida de verificar, a cada mudança de prompt,
regra ou dado da base de conhecimento, se os comportamentos essenciais da
jornada continuam corretos (não repetir pergunta já respondida, não pular
etapa, recusar quando não sabe, nunca inventar preço). Hoje essa verificação
depende de teste manual via WhatsApp.

**Why this priority**: Complementa a US5 (dados de produção); menor
prioridade porque a suíte de testes existente já cobre boa parte do
comportamento — o golden set cobre casos derivados de conversas reais que a
suíte unitária pode não capturar.

**Independent Test**: Executar o conjunto de casos e obter um relatório com
taxa de acerto por dimensão (fluxo correto, abstenção correta, ausência de
preço inventado), sem depender de execução manual.

**Acceptance Scenarios**:

1. **Given** o conjunto de casos de referência (derivados de cenários reais
   de teste e dos caminhos oficiais da jornada), **When** o conjunto é
   executado, **Then** um relatório indica, por caso, se a ação esperada
   coincide com a ação obtida.
2. **Given** um caso do conjunto cuja etapa/estado inicial já tem um slot
   preenchido, **When** o caso é executado, **Then** o relatório aponta
   falha se o sistema voltar a perguntar o slot já preenchido.
3. **Given** um caso fora da Base Oficial de Conhecimento, **When** o caso
   é executado, **Then** o relatório aponta falha se o sistema responder
   com informação não verificável na Base (preço, prazo, condição
   inventados).

---

### Edge Cases

- O que acontece se o lead enviar uma mensagem exatamente no instante em
  que o teto de turnos-de-sessão é atingido, simultaneamente ao teto de
  turnos-no-nó? (Precedência: teto de sessão prevalece — é o teto de
  segurança mais alto.)
- Como o sistema se comporta se os contadores de turno em Redis forem
  perdidos (ex.: Redis reiniciado sem persistência) no meio de uma sessão
  longa? (Tratar como novo início de contagem — não bloquear o
  atendimento; perda de contador não é falha crítica, é degradação aceita.)
- O que acontece se a etapa de dúvidas abertas for interrompida por um
  handoff de teto de sessão antes de atingir seu próprio limiar elevado?
  (O teto de sessão é global e prevalece sobre o limiar específico de
  dúvidas.)
- Como o sistema trata um lead que retorna dentro da janela de
  reengajamento mas cuja etapa aguarda uma resposta específica (ex.: menu)?
  (A mensagem de retomada é emitida e a etapa/pergunta pendente continua
  válida — não se reapresenta o menu inteiro se não for necessário.)
- O que acontece se o recovery de agrupamento pendente no restart encontrar
  uma rajada cujo atendimento já foi encerrado ou transferido a humano
  nesse meio tempo? (O gate de fila existente prevalece: se o atendimento
  está sob controle humano, a rajada recuperada não gera resposta do bot.)
- Como o registro de observabilidade lida com um turno que falha (exceção
  não tratada) antes de completar? (Ainda assim deve emitir um registro
  parcial identificando a falha, para não haver lacuna silenciosa nos
  dados.)

## Requirements

### Functional Requirements

**Orçamento de turnos (US1)**

- **FR-001**: O sistema MUST contar turnos de conversa por sessão de
  atendimento (total) e por etapa/nó da jornada, de forma independente do
  contador existente de respostas não-reconhecidas.
- **FR-002**: O sistema MUST incrementar os contadores de turno exatamente
  uma vez por turno processado, independentemente do turno ter sido
  reconhecido ou não pela classificação de intenção.
- **FR-003**: O sistema MUST emitir um nudge contextual (oferta de conexão
  com especialista) quando o contador de turnos de uma etapa atinge um
  limiar configurável, sem interromper a possibilidade de o lead continuar
  a conversa.
- **FR-004**: O sistema MUST encaminhar o atendimento a um destino humano
  quando o contador de turnos da sessão inteira atinge um limiar
  configurável de segurança, usando sempre um destino lógico
  pré-configurado (nunca decidido pelo modelo de linguagem).
- **FR-005**: O sistema MUST aplicar um limiar de nudge diferenciado (mais
  alto) para a etapa de dúvidas abertas, para não penalizar perguntas
  legítimas do lead.
- **FR-006**: O sistema MUST registrar, junto ao nudge ou handoff disparado
  por orçamento de turnos, o motivo específico (limiar de nó ou limiar de
  sessão) para fins de observabilidade (ver FR-011).
- **FR-007**: Os limiares de FR-003, FR-004 e FR-005 MUST ser configuráveis
  sem necessidade de alterar código.

**Timeout de inatividade e reengajamento (US2)**

- **FR-008**: O sistema MUST registrar o instante da última interação de
  cada sessão de atendimento, atualizado a cada turno processado.
- **FR-009**: O sistema MUST detectar, no início do processamento de um
  novo turno, se o intervalo desde a última interação excede um limiar de
  reengajamento configurável; em caso positivo e a sessão estando em meio
  a um fluxo, MUST emitir uma retomada cordial antes de prosseguir, sem
  reiniciar a jornada nem perder o estado corrente.
- **FR-010**: O sistema MUST detectar, no início do processamento de um
  novo turno, se o intervalo desde a última interação excede um limiar de
  expiração de sessão configurável (maior que o de reengajamento); em caso
  positivo, MUST tratar a sessão como nova (retornando à etapa inicial),
  **preservando** todos os dados de perfil do contato já capturados
  (elegibilidade médica, idioma, especialidade, experiência, interesse) e
  sem repetir perguntas já respondidas.
- **FR-INFRA-01**: Os limiares de FR-009 e FR-010 MUST ser configuráveis
  sem necessidade de alterar código; a detecção MUST ocorrer de forma
  incidental ao processamento normal do turno (sem exigir processo/worker
  dedicado adicional).

**Durabilidade do agrupamento de mensagens (US3)**

- **FR-011**: O sistema MUST, ao iniciar (ou reiniciar), identificar
  agrupamentos de mensagens pendentes de processamento e retomar seu
  processamento automaticamente — reagendando o disparo se a janela de
  agrupamento ainda não expirou, ou processando imediatamente se já
  expirou.
- **FR-012**: O processamento de um agrupamento recuperado no início do
  sistema MUST ser idempotente — processado exatamente uma vez mesmo que a
  rotina de recuperação seja executada mais de uma vez.

**Robustez de concorrência (US4)**

- **FR-013**: O mecanismo de exclusividade de processamento por
  atendimento MUST cobrir a duração observada do pior caso de turno
  (resposta do modelo + múltiplos envios com pacing + retentativas), seja
  por um teto de validade elevado, seja por renovação durante o
  processamento.
- **FR-014**: O sistema MUST documentar explicitamente que a distribuição
  do mecanismo de espaçamento de envios entre múltiplas instâncias
  concorrentes do sistema (necessária apenas ao escalar horizontalmente)
  é uma pré-condição não implementada nesta feature — apenas registrada
  como decisão consciente adiada.

**Observabilidade de turno (US5)**

- **FR-015**: O sistema MUST emitir exatamente um registro estruturado por
  turno processado, contendo: identificador do atendimento, número do
  turno na sessão, etapa de entrada, etapa de saída, intenção
  classificada, idioma, quantidade de blocos de mensagem enviados, ação
  tomada, destino de encaminhamento (quando houver), duração do
  processamento e número de tentativas.
- **FR-016**: O registro estruturado de turno MUST ser emitido mesmo em
  caso de falha no processamento do turno (registro parcial identificando
  a falha), para não haver lacunas silenciosas na observabilidade.

**Conjunto de regressão de jornada (US6)**

- **FR-017**: O sistema MUST disponibilizar um conjunto de casos de
  referência derivados de cenários reais de teste e dos caminhos oficiais
  da jornada, cada um definindo mensagem de entrada, estado inicial e
  resultado esperado (ação, etapa de destino, e ausência de repetição de
  dado já capturado).
- **FR-018**: O sistema MUST permitir executar o conjunto de casos de
  referência de forma independente da suíte de testes principal,
  produzindo um relatório com taxa de acerto por dimensão avaliada (fluxo
  correto, abstenção correta quando aplicável, ausência de informação
  inventada).

**Preservação de mecanismos existentes**

- **FR-019**: O sistema MUST preservar, sem alteração de comportamento,
  todos os mecanismos de controle já existentes: contador de tentativas
  não-reconhecidas por etapa, teto de mensagens do bot por turno, pacing
  de envio, retentativa honrando limite de taxa, idempotência por
  mensagem, exclusividade por atendimento (exceto ajuste de FR-013), gate
  de fila quando atendimento humano assumiu, e agrupamento de rajada de
  entrada.
- **FR-020**: O sistema MUST continuar respeitando as restrições
  invioláveis do agente: resposta exclusiva com base na Base Oficial de
  Conhecimento (Regra 30), apresentações enviadas sem passar por geração
  de texto livre, destino de encaminhamento sempre proveniente de
  configuração pré-definida (nunca do modelo de linguagem), elegibilidade
  médica inflexível, uma pergunta por mensagem, e resposta no idioma do
  lead.

### Key Entities

- **Contador de Turno por Sessão**: representa o total de turnos ocorridos
  em um atendimento desde seu início (ou desde o último reinício por
  expiração), usado para o teto de segurança da sessão inteira.
- **Contador de Turno por Nó**: representa o total de turnos ocorridos
  dentro da etapa/nó corrente da jornada, usado para o nudge de escape;
  reinicia ao mudar de etapa.
- **Marca de Última Interação**: representa o instante do último turno
  processado de uma sessão, usado para detectar inatividade.
- **Registro de Turno**: representa o evento observável de um turno
  processado — identificação, contadores, etapas, intenção, idioma, ação
  tomada, destino de encaminhamento, duração e tentativas.
- **Caso de Referência (golden set)**: representa um cenário de conversa
  com mensagem de entrada, estado inicial e resultado esperado, usado
  para regressão de comportamento de jornada.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Uma sessão que ultrapassa o limiar de turnos de uma etapa
  recebe um nudge de especialista antes de ultrapassar o limiar de
  segurança da sessão inteira, em 100% dos casos simulados.
- **SC-002**: Uma sessão que ultrapassa o limiar de segurança de turnos da
  sessão é encaminhada a um destino humano válido (nunca vazio, nunca
  decidido livremente) em 100% dos casos simulados.
- **SC-003**: Um lead que retorna após o intervalo de reengajamento
  configurado recebe uma retomada cordial reconhecendo a pausa, sem perda
  de contexto de fluxo, em 100% dos casos simulados.
- **SC-004**: Um lead que retorna após o intervalo de expiração de sessão
  configurado inicia um novo atendimento sem que nenhum dado de perfil já
  capturado (elegibilidade, idioma, especialidade, experiência) seja
  re-perguntado.
- **SC-005**: Um reinício do sistema durante uma janela de agrupamento de
  mensagens pendente não resulta em nenhuma mensagem perdida — 100% das
  rajadas pendentes são processadas exatamente uma vez.
- **SC-006**: Um turno cuja duração de processamento se aproxima do pior
  caso conhecido não é reprocessado por expiração do mecanismo de
  exclusividade.
- **SC-007**: 100% dos turnos processados geram exatamente um registro
  estruturado de observabilidade, permitindo responder "quantos turnos até
  o handoff" e "em que etapa mais se trava" sem necessidade de leitura
  manual de logs brutos.
- **SC-008**: O conjunto de regressão de jornada (30 a 50 casos) é
  executável sob demanda e reporta taxa de acerto por dimensão avaliada,
  sem exigir execução manual via WhatsApp para cada validação de mudança.
  *Clarificação (não mudança de escopo — research.md Decision 9 / CHK011):*
  nesta Onda 1 o relatório é informativo, sem patamar mínimo bloqueante de
  taxa de acerto.
- **SC-009**: Todos os mecanismos de controle pré-existentes (verificados
  na avaliação de linha de base) continuam funcionando sem regressão após
  a introdução desta feature.
- **SC-010**: Validação real em ambiente de produção (via canal
  autorizado de teste) confirma, em português e em pelo menos um outro
  idioma suportado, os comportamentos de nudge/handoff, retomada por
  inatividade e não-perda de turno em restart.

## Out of Scope

- Portão de verificação de fidelidade pós-geração de resposta (Pilar 5).
- Contrato de saída estruturada (JSON) do modelo de linguagem para as
  fases de dúvida (Pilar 6).
- Recuperação e busca vetorial (RAG híbrido) para FAQ/objeções (Pilar 4).
- Distribuição do mecanismo de espaçamento de envios entre múltiplas
  instâncias (necessário apenas ao escalar horizontalmente) — apenas
  documentado como pré-condição futura (FR-014).
- Encerramento proativo de sessões abandonadas via processo agendado
  dedicado (mencionado como opcional na avaliação de linha de base) — a
  detecção lazy no retorno do lead (US2) cobre o caso de uso essencial.
- Entidade durável de turno para analytics históricos — o registro
  estruturado (US5) é suficiente para o escopo desta feature.
