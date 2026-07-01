# Feature Specification: Recuperação Híbrida Ancorada com Abstenção (Onda 3)

**Feature**: `sdr-rag-hibrido`
**Created**: 2026-07-01
**Status**: Draft

## Contexto

O Consultor Virtual da GoldIncision já entrega, na Onda 1 (`sdr-turnos-obs`,
mergeada), controle de turnos e observabilidade por turno, e na Onda 2
(`sdr-fidelidade-json`, mergeada), um contrato estruturado de saída do LLM
(`RespostaEstruturada`) e um portão de fidelidade (`FidelityGate`) que confere,
antes do envio, se uma resposta gerada é sustentada pela base oficial.

O que falta — e esta feature fecha — é a etapa anterior a ambos: **como o
conteúdo de grounding chega até o LLM**. Hoje, para toda resposta de dúvida,
o sistema concatena de forma bruta e não-ranqueada tudo o que existe no banco
para aquele curso (apresentação + todas as objeções + turmas + link + todo o
FAQ), sem medir relevância e sem qualquer mecanismo de recusa quando a
pergunta do lead foge do que a base cobre. Isso cresce em custo/ruído à medida
que a base de conhecimento aumenta, e não impede — por si só — que o LLM
"estique" contexto pouco relevante para tentar responder algo que a base não
sustenta de fato.

Esta feature introduz uma camada de **recuperação (retrieval) híbrida e
ancorada**: em vez de despejar tudo, o sistema busca e classifica por
relevância real apenas o conteúdo textual livre (dúvidas, objeções, FAQ) —
determinando também, quando a relevância encontrada é baixa demais, uma
**abstenção forçada** (recusa + encaminhamento humano) em vez de gerar
qualquer resposta. Apresentações oficiais, preços e links de inscrição
continuam sendo enviados **verbatim** direto do catálogo, exatamente como
hoje — fora do escopo desta recuperação. A máquina de estados do Mapa Mestre,
a ordem das etapas, e os contratos/portões da Onda 2 permanecem intocados:
esta feature apenas troca a FONTE do conteúdo que os alimenta.

> Decisões de infraestrutura: **idempotência aplicável** — a preparação do
> conteúdo de conhecimento (unidades + suas representações vetoriais) deve
> seguir o MESMO padrão de idempotência já usado pelo catálogo de cursos/FAQ
> hoje (reprocessamento não duplica, apenas atualiza o que mudou). Demais
> categorias do checklist de infraestrutura são **N/A explícito** nesta
> entrega: sem scheduler novo (a preparação roda junto do processo de
> preparação de conteúdo já existente), sem rotação de chaves, sem refresh de
> token externo, sem novo mecanismo de mutex multi-instância (reusa o já
> existente), sem rotina de backup adicional (o armazenamento de conhecimento
> é coberto pela mesma política de backup do armazenamento principal já
> existente).

## User Scenarios & Testing

### User Story 1 - Resposta de dúvida ancorada em conteúdo relevante (Priority: P1)

Um lead faz uma pergunta livre (dúvida, objeção ou pergunta de FAQ) sobre o
curso/produto em que já está no fluxo. Em vez de receber uma resposta gerada
a partir de todo o conteúdo do curso despejado sem critério, o sistema busca
e seleciona apenas os trechos oficiais mais relevantes para aquela pergunta
específica, e o consultor virtual responde ancorado nesses trechos.

**Why this priority**: É o núcleo de valor da feature — sem recuperação
relevante, tudo o mais (abstenção, isolamento por produto, rastreabilidade)
não tem efeito prático. Sem esta story não há MVP.

**Independent Test**: Fazer uma pergunta cuja resposta correta esteja em um
único trecho oficial específico (uma objeção ou uma entrada de FAQ) e
verificar que a resposta gerada reflete apenas esse trecho relevante, sem
depender de o restante do conteúdo do curso estar presente.

**Acceptance Scenarios**:

1. **Given** um lead qualificado conversando sobre um curso específico,
   **When** ele faz uma pergunta cuja resposta está coberta por uma única
   objeção ou entrada de FAQ oficial, **Then** o consultor responde de forma
   ancorada nesse conteúdo específico, sem misturar informação de outras
   seções não relacionadas à pergunta.
2. **Given** a base de conhecimento de um curso contém dezenas de objeções e
   entradas de FAQ, **When** o lead faz uma pergunta pontual, **Then** o
   tempo de resposta não aumenta proporcionalmente ao tamanho total da base
   daquele curso.

---

### User Story 2 - Abstenção quando a base não cobre a pergunta (Priority: P1)

Um lead faz uma pergunta cujo conteúdo não existe em nenhum documento oficial
carregado para aquele curso/idioma. Em vez de o consultor virtual tentar
"se virar" com informação frouxamente relacionada, o sistema reconhece que
não há fonte suficientemente relevante e recusa educadamente, encaminhando
para atendimento humano.

**Why this priority**: É a garantia central de anti-alucinação desta feature
— sem ela, a recuperação apenas mudaria COMO o conteúdo chega ao LLM, sem
reduzir o risco de resposta inventada quando a base não cobre algo. Mesma
prioridade da User Story 1 porque as duas juntas formam o comportamento
mínimo aceitável (buscar bem OU recusar, nunca inventar).

**Independent Test**: Fazer uma pergunta claramente fora do que qualquer
documento oficial cobre (ex.: um assunto correlato mas nunca tratado nos
materiais) e verificar que o sistema sempre recusa e encaminha, nunca
produz uma resposta de conteúdo.

**Acceptance Scenarios**:

1. **Given** um lead pergunta algo sobre o curso que não está coberto por
   nenhuma apresentação, objeção ou FAQ oficial, **When** o sistema busca
   conteúdo relevante, **Then** nenhum trecho encontrado atinge o patamar
   mínimo de relevância e o sistema responde com a mensagem padrão de "não
   tenho essa informação" e aciona o encaminhamento humano.
2. **Given** o mecanismo de busca falha ou não responde a tempo, **When** o
   sistema tenta montar o conteúdo de apoio para uma resposta de dúvida,
   **Then** o comportamento padrão é o mesmo da ausência de fonte relevante
   (recusa + encaminhamento), nunca uma resposta sem grounding.

---

### User Story 3 - Isolamento por produto e idioma (Priority: P2)

Um lead está conversando sobre o Curso A, mas a base de conhecimento também
contém objeções e FAQ de outros cursos (B, C...). Ao responder a uma dúvida
desse lead, o sistema nunca traz conteúdo pertencente a outro curso, mesmo
que ele seja textualmente parecido com a pergunta feita.

**Why this priority**: Sem isolamento, o valor da recuperação relevante da
US1 é comprometido — o sistema poderia "encontrar" um trecho de alta
similaridade textual pertencente ao curso errado e respondê-lo como se fosse
válido para o curso do lead, quebrando a promessa de fidelidade à base
oficial do produto em atendimento (a mesma regra que hoje já rege objeções
por curso).

**Independent Test**: Ter dois cursos com objeções/FAQ parecidos entre si
(mesmo tema, curso diferente) e verificar que uma pergunta feita no contexto
do Curso A nunca retorna conteúdo cadastrado apenas para o Curso B.

**Acceptance Scenarios**:

1. **Given** dois cursos distintos possuem conteúdo textualmente semelhante
   sobre o mesmo tema (ex.: uma objeção de preço), **When** um lead no
   contexto do Curso A pergunta sobre esse tema, **Then** apenas conteúdo
   cadastrado para o Curso A é considerado candidato à resposta.
2. **Given** um lead está se comunicando em um idioma específico (PT, EN ou
   ES), **When** ele faz uma pergunta livre, **Then** o sistema prioriza
   conteúdo oficial nesse idioma, com um comportamento de reserva claro e
   documentado quando não existir conteúdo equivalente traduzido.

---

### User Story 4 - Rastreabilidade da fonte de cada resposta (Priority: P2)

Um responsável por qualidade de atendimento revisa conversas passadas e
precisa saber exatamente qual trecho da base oficial sustentou cada resposta
de dúvida gerada pelo consultor virtual, para auditar fidelidade e calibrar
o comportamento do sistema ao longo do tempo.

**Why this priority**: Não bloqueia o atendimento em si (por isso P2, não
P1), mas é o que torna as demais garantias auditáveis e ajustáveis — sem
rastreabilidade não é possível calibrar o patamar de relevância nem provar,
em uma auditoria, que uma resposta específica teve base real.

**Independent Test**: Selecionar uma amostra de respostas de dúvida já
enviadas e verificar que, para cada uma, é possível identificar o(s)
trecho(s) oficiais específicos que a sustentaram (ou confirmar que se tratou
de uma abstenção, sem trecho associado).

**Acceptance Scenarios**:

1. **Given** uma resposta de dúvida foi gerada e enviada ao lead, **When**
   um revisor consulta o registro daquele turno, **Then** consegue
   identificar exatamente qual(is) trecho(s) da base oficial embasaram a
   resposta.
2. **Given** um conjunto de perguntas de referência com resposta esperada
   conhecida (casos de avaliação), **When** o sistema é executado contra
   esse conjunto, **Then** é possível medir a proporção de respostas
   corretamente ancoradas e a proporção de abstenções corretas, para
   calibrar o patamar mínimo de relevância ao longo do tempo.

---

### Edge Cases

- O que acontece quando o lead faz uma pergunta que toca dois produtos ao
  mesmo tempo (ex.: compara Curso A com Curso B)? O sistema deve manter o
  isolamento por produto do contexto corrente e, se a pergunta genuinamente
  precisar de informação de outro produto, tratar como fora do escopo da
  resposta livre (encaminhar/redirecionar), nunca misturar fontes de dois
  produtos numa única resposta.
- Como o sistema lida quando o conteúdo oficial de um curso é atualizado
  (nova objeção, FAQ revisado)? A atualização deve refletir na próxima
  preparação de conteúdo sem exigir reprocessar tudo do zero, e sem duplicar
  entradas já existentes.
- O que acontece quando o lead repete, com poucas variações, uma pergunta já
  feita recentemente na mesma conversa? O sistema deve poder reaproveitar o
  resultado de busca já obtido em vez de refazer todo o processamento do
  zero, sem alterar o comportamento observável da resposta.
- Como o sistema se comporta quando não existe nenhum conteúdo cadastrado
  para o idioma do lead? Deve haver um comportamento de reserva claro e
  documentado (equivalente ao já usado hoje para outros conteúdos por
  idioma), nunca uma falha visível ao lead.
- O que acontece se a fonte de conteúdo mudar de versão (documento oficial
  revisado) enquanto uma conversa está em andamento? A próxima pergunta do
  lead deve já refletir o conteúdo atualizado; não há necessidade de manter
  compatibilidade com a versão anterior durante a conversa.

## Requirements

### Functional Requirements

- **FR-001**: O sistema MUST recuperar, para cada pergunta livre do lead
  (dúvida, objeção ou pergunta de FAQ), apenas o conteúdo oficial
  relevante para aquela pergunta específica — em vez de fornecer todo o
  conteúdo oficial disponível para o curso em atendimento.
- **FR-002**: O sistema MUST restringir os candidatos a conteúdo elegível
  ANTES de avaliar relevância semântica, considerando obrigatoriamente o
  produto/curso do contexto da conversa e o idioma do lead — nenhum
  conteúdo de outro produto pode ser considerado candidato.
- **FR-003**: O sistema MUST combinar, na busca por relevância, tanto
  correspondência por significado (paráfrases, sinônimos) quanto
  correspondência por termos exatos, e MUST consolidar as duas em uma única
  ordem de relevância antes de decidir o que usar como apoio à resposta.
- **FR-004**: O sistema MUST limitar o conteúdo de apoio efetivamente usado
  para gerar a resposta a um conjunto pequeno dos itens mais relevantes,
  nunca ao conjunto bruto de candidatos.
- **FR-005**: O sistema MUST calcular um grau de relevância do melhor
  conteúdo encontrado e MUST aplicar um patamar mínimo calibrável: quando o
  melhor conteúdo encontrado fica abaixo desse patamar, o sistema MUST se
  abster de gerar qualquer resposta de conteúdo.
- **FR-006**: Quando o sistema se abstém (FR-005), MUST responder com uma
  mensagem padrão informando que não possui aquela informação e MUST
  acionar o mesmo mecanismo de encaminhamento a atendimento humano já usado
  em outras situações de handoff — nenhum canal de encaminhamento novo.
- **FR-007**: O conteúdo oficial usado como fonte de apoio MUST estar
  organizado em unidades de significado completo (uma objeção = uma
  unidade; uma entrada de FAQ = uma unidade; uma seção coerente de um
  documento de base = uma unidade), cada uma identificada por produto, tipo
  de conteúdo, idioma e documento de origem — nunca fatiado por tamanho fixo
  de texto sem relação com o conteúdo.
- **FR-008**: As unidades de conteúdo (FR-007) MUST ser derivadas
  exclusivamente dos mesmos documentos oficiais que já alimentam o catálogo
  de conhecimento existente — não pode existir uma segunda fonte de
  conteúdo divergente da já usada hoje.
- **FR-009**: A representação de cada unidade de conteúdo usada para
  avaliar relevância semântica (FR-003) MUST ser calculada uma única vez
  durante a preparação do conteúdo e MUST ser reaproveitada em toda
  pergunta subsequente — MUST NOT ser recalculada a cada início do sistema.
- **FR-010**: A representação de uma unidade de conteúdo só MUST ser
  recalculada quando o conteúdo de origem correspondente (ou a versão do
  documento oficial) muda; reprocessar o conteúdo sem mudança real MUST NOT
  duplicar unidades já existentes (idempotência, mesmo padrão do catálogo
  de conhecimento já existente).
- **FR-011**: Toda resposta de dúvida gerada com apoio de conteúdo
  recuperado MUST manter um vínculo rastreável a qual(is) unidade(s) de
  conteúdo a sustentaram, disponível para auditoria posterior (US4).
- **FR-012**: O contrato estruturado de saída (que já lista fontes usadas) e
  o portão de verificação de fidelidade já existentes MUST validar a
  resposta contra as MESMAS unidades de conteúdo recuperadas para aquela
  pergunta — nunca contra um conjunto mais amplo ou diferente do que
  efetivamente embasou a resposta.
- **FR-013**: A recuperação de conteúdo MUST funcionar nos três idiomas
  suportados pelo atendimento (PT/EN/ES); quando não existir unidade de
  conteúdo equivalente no idioma do lead, o sistema MUST aplicar um
  comportamento de reserva claro e documentado (nunca falhar silenciosamente
  nem misturar idiomas na mesma resposta).
- **FR-014**: Conteúdo canônico enviado sempre na íntegra (apresentações
  oficiais, preços, links de inscrição) MUST permanecer fora do escopo desta
  recuperação — continua sendo enviado exatamente como hoje, sem passar por
  busca ou ranqueamento.
- **FR-015**: A ordem e a estrutura da jornada de atendimento (Mapa Mestre)
  MUST permanecer inalterada; a recuperação de conteúdo afeta somente o
  conteúdo de apoio usado dentro da etapa de dúvidas já existente.
- **FR-016**: O sistema MUST preservar, sem regressão observável, todo o
  comportamento de controle de turno, anti-loop, reengajamento, tempo de
  espera antes de processar mensagens agrupadas, trava de processamento
  concorrente e observabilidade por turno já entregues na Onda 1.
- **FR-017**: O sistema MUST preservar, sem regressão observável, o
  contrato estruturado de resposta, o portão de verificação de fidelidade e
  a extração de dados da mensagem do lead já entregues na Onda 2.
- **FR-018**: Todo turno em que a recuperação de conteúdo é acionada MUST
  ser mensurável em tempo de resposta (e em custo, quando disponível) pelo
  mesmo mecanismo de observabilidade por turno já existente.
- **FR-019**: Quando o lead repete, dentro de uma mesma conversa, uma
  pergunta idêntica ou muito semelhante a uma já processada recentemente, o
  sistema SHOULD reaproveitar o resultado de busca já obtido em vez de
  refazer todo o processamento, reduzindo tempo de resposta e custo; um
  reaproveitamento mais amplo (por resposta final, não apenas por consulta)
  MAY ser adotado futuramente, mas não é obrigatório nesta entrega.
- **FR-020**: Todo parâmetro ajustável introduzido por esta feature (patamar
  mínimo de relevância, quantidade de candidatos avaliados, escolha do
  mecanismo de representação semântica) MUST ser exposto como configuração
  documentada, junto da configuração já existente — nunca fixo no código.
- **FR-021**: Quando o mecanismo de recuperação de conteúdo estiver
  indisponível ou não responder dentro de um tempo aceitável, o sistema
  MUST se comportar como se nenhuma fonte relevante tivesse sido encontrada
  (abstenção + encaminhamento, FR-005/FR-006) — nunca cair de volta para o
  comportamento anterior de despejar todo o conteúdo sem filtro.
- **FR-022**: O sistema MUST permitir revisar, para uma amostra de turnos
  passados, qual(is) unidade(s) de conteúdo foram recuperadas e se houve
  abstenção, para permitir calibrar o patamar mínimo de relevância ao longo
  do tempo com base em casos reais (US4).
- **FR-023-INFRA-IDEMP**: A preparação/reprocessamento das unidades de
  conteúdo e de suas representações semânticas MUST ser idempotente
  (executar novamente não duplica unidades já existentes; apenas atualiza o
  que mudou), no mesmo padrão do catálogo de conhecimento já existente.
- **FR-024-INFRA-PRECONDITION**: O código desta feature (migration,
  índices, pipeline de recuperação) MUST ser desenvolvido e entregue via PR
  mesmo que a extensão `vector` (pgvector) ainda não esteja habilitada no
  Postgres do serviço `sdr-whatsapp_postgres`. A troca da imagem desse
  serviço de `postgres:16-alpine` para `pgvector/pgvector:pg16` (mesma base
  PG16, volume `sdr-whatsapp_postgres_data` compatível) é **pré-condição de
  merge/deploy**, MUST ser documentada explicitamente no `plan.md` e no
  corpo do PR, e é executada pelo operador na janela de infraestrutura dele
  — nunca automatizada por esta feature. Nenhum outro serviço/stack
  (`fia`, `n8n`, `pgadmin`, `postgres_postgres`, `envio-massa`, `fast-api`,
  `portainer`, `traefik`, `metanoia`) MUST ser referenciado ou alterado. A
  migration `CREATE EXTENSION IF NOT EXISTS vector` roda no startup do app,
  **depois** do swap de imagem — o app só deve ser efetivamente deployado
  com a nova imagem do Postgres.

### Key Entities

- **Unidade de Conhecimento**: um trecho de significado completo (uma
  objeção, uma entrada de FAQ, ou uma seção coerente de um documento de
  base) elegível para recuperação em respostas de dúvida. Carrega:
  conteúdo textual oficial, produto/curso ao qual pertence, tipo (objeção,
  FAQ, base geral, licenciamento), idioma, documento de origem, e uma
  representação semântica usada para avaliar relevância. Não inclui
  apresentações, preços ou links (que permanecem fora deste escopo).
- **Resultado de Recuperação**: o conjunto pequeno de Unidades de
  Conhecimento selecionadas como mais relevantes para uma pergunta
  específica, já restrito por produto/idioma, já combinando relevância por
  significado e por termo exato, com um grau de relevância associado usado
  para decidir entre responder ou abster.
- **Registro de Auditoria de Recuperação**: a associação, por turno de
  dúvida, entre a pergunta feita, a(s) Unidade(s) de Conhecimento
  recuperada(s) (ou ausência de resultado qualificado, no caso de
  abstenção) e a decisão final tomada — usada para rastreabilidade (US4) e
  calibração do patamar de relevância.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Ao menos 95% das perguntas de dúvida cujo conteúdo existe
  claramente na base oficial recebem resposta ancorada no trecho correto,
  medido contra um conjunto de casos de referência.
- **SC-002**: 100% das perguntas de dúvida cujo conteúdo não existe na base
  oficial resultam em abstenção + encaminhamento humano, nunca em resposta
  de conteúdo inventado, medido contra o mesmo conjunto de casos de
  referência.
- **SC-003**: Zero ocorrências, em conjunto de casos de referência com
  produtos distintos e conteúdo semelhante entre si, de uma resposta sobre
  um produto conter informação cadastrada apenas para outro produto.
- **SC-004**: O tempo de resposta percebido pelo lead para perguntas de
  dúvida não aumenta de forma perceptível à medida que a base de
  conhecimento cresce (comparado à linha de base anterior a esta feature).
- **SC-005**: Para 100% das respostas de dúvida geradas (não-abstenção), um
  revisor consegue identificar, a partir do registro do turno, qual conteúdo
  oficial a sustentou.

## Clarifications

### Sessão 2026-07-01

- **Q1** (produto/infra — **Resolvido (B), block-001 respondido em
  2026-07-01T18:03:41Z**): A extensão de busca vetorial (pgvector) no
  Postgres 16 da stack já está habilitada e disponível para uso imediato,
  ou a habilitação depende de uma ação/aprovação de infraestrutura do
  operador antes do merge?
  → **Resolvido (B):** o serviço `sdr-whatsapp_postgres` (stack
  `sdr-whatsapp`) roda hoje `postgres:16-alpine`, **sem** pgvector
  (verificado via `docker service ls` em produção). O container
  `fia_postgres` (`pgvector/pgvector:pg16`) pertence a **outro** projeto
  (stack `fia`) e não deve ser referenciado/tocado. Decisão: desenvolver
  todo o código do RAG e abrir o PR normalmente, registrando como
  **pré-condição de merge/deploy** (FR-024-INFRA-PRECONDITION) a troca da
  imagem do serviço `sdr-whatsapp_postgres` para `pgvector/pgvector:pg16`
  (mesma base PG16, volume `sdr-whatsapp_postgres_data` compatível,
  redeploy só desse serviço), a ser executada pelo operador na janela de
  infraestrutura dele. A migration `CREATE EXTENSION IF NOT EXISTS vector`
  roda no startup, depois do swap de imagem. Ver dec-013.

- **Q2** (FR-007 — delimitação de "seção coerente de documento de base"):
  Como segmentar seções de um documento de base em unidades de conhecimento?
  → **Resolvido (A, score 3):** segmentação **manual/curada pelo operador**
  (marcação explícita via API de admin), consistente com o Princípio VII
  ("Cursos como Dados") e com FR-008 (unidades derivam exclusivamente dos
  mesmos documentos oficiais que já alimentam o catálogo curado; heurística
  automática criaria uma segunda fonte divergente).

- **Q3** (SC-001/SC-002/US4 — curadoria do conjunto de casos de referência):
  A curadoria do golden set é escopo de código desta feature ou do operador?
  → **Resolvido (A, score 2):** esta feature entrega **apenas o mecanismo de
  avaliação**; a curadoria do conjunto de casos de referência é
  responsabilidade do operador, **fora do escopo de código** (o golden set já
  fica fora do CI padrão). A feature apenas troca a FONTE do conteúdo
  consumido pela recuperação.

- **Q4** (FR-022 — revisão de amostras de recuperação/abstenção): novo
  endpoint na API de admin ou consulta direta aos registros?
  → **Resolvido (A, score 3):** **consulta direta aos registros de auditoria**
  (banco/logs) é suficiente nesta entrega; **sem novo endpoint admin**. A
  rastreabilidade (FR-011/FR-022) é atendida pelos registros por turno já
  existentes (observabilidade `log_turno` da Onda 1). Endpoint dedicado seria
  escopo extra não pedido.

- **Q5** (FR-013 — comportamento de reserva quando não há unidade no idioma
  do lead): abster ou usar outro idioma?
  → **Resolvido (A, score 3):** tratar a ausência de unidade no idioma do
  lead como **ausência de fonte relevante → abster + handoff** (mesmo caminho
  de FR-005/FR-006). O Princípio II (Anti-Alucinação Rígida) não abre exceção
  de idioma; FR-002 já exige pré-filtro por produto **E idioma** antes da
  relevância, logo conteúdo em outro idioma nunca é candidato elegível, o que
  evita mistura de idiomas (proibida por FR-013).
  > Ressalva registrada: o código atual (`_load_faq`/`_scalar_idioma` em
  > `flow.py`) tem fallback para PT quando não há conteúdo no idioma. O
  > **plan** deve confirmar se esse fallback PT existente permanece para os
  > blocos canônicos verbatim (fora do RAG, sem regressão) enquanto a camada
  > de recuperação RAG adota abstenção — os dois não conflitam, pois operam em
  > caminhos distintos (verbatim vs. texto livre).
