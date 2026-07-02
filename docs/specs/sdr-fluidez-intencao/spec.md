# Feature Specification: Fluidez Agêntica de Intenção no Atendimento SDR

**Feature**: `sdr-fluidez-intencao`
**Created**: 2026-07-02
**Status**: Draft

## Contexto e motivação

O Consultor Virtual Oficial da GoldIncision conduz o lead pelos 6 caminhos
oficiais do Mapa Mestre. Hoje, quando o lead muda de ideia no meio da
jornada (ex.: está respondendo perguntas de um caminho e diz que na verdade
quer outro produto), ou quando digita livremente no menu inicial em vez de
escolher um número, ou quando sua mensagem simplesmente não é reconhecida,
o atendimento pode ficar "preso": repete a mesma pergunta ao pé da letra,
volta ao menu de 6 opções sem necessidade, ou ignora a correção do lead.

**Caso real que motiva a feature**: no menu inicial, um lead digitou
"harmonização glutea" e não foi reconhecido. Mais adiante, já dentro do
caminho errado (Sistema GoldIncision), o mesmo lead escreveu "opa... na
verdade quero o curso de harmoização glutea" — e o atendimento respondeu
repetindo, palavra por palavra, o mesmo bloco de pergunta anterior (incluindo
a saudação "Perfeito! 😊"), sem trocar de caminho nem reconhecer a correção.

Esta feature torna a **interpretação** das mensagens do lead mais fluida e
humana, mantendo a jornada oficial (Mapa Mestre) e o motor de decisão de
fluxo **totalmente determinísticos** — nenhuma capacidade de IA passa a
decidir para onde a conversa vai ou para qual fila um atendimento é
encaminhado.

## Clarifications

### Session 2026-07-01

- Q: Quando o lead não confirma (nega, ignora ou responde algo não reconhecido) a pergunta de confirmação curta de troca de caminho, o sistema deve fazer o quê? → A: Não trocar de caminho; a tentativa de confirmação conta como uma tentativa não reconhecida da pergunta pendente original, sujeita ao mesmo limite/contador de tentativas já existente (coerente com FR-010/FR-016 — preserva escopo e limite vigentes de tentativas e o encaminhamento automático a humano).
- Q: Enquanto uma conversa está com retomada pendente (interrompida por excesso de mensagens), uma mensagem do lead contendo marcador de correção explícito + produto claro deve ser tratada como o quê? → A: Ignorada para fins de troca de caminho até a retomada ser resolvida; toda mensagem nesse estado é tratada exclusivamente como resposta à retomada, mesmo contendo marcador explícito (FR-020 e o edge case de retomada dão prioridade à retomada sobre "qualquer nova detecção de troca de rumo", sem exceção documentada).
- Q: Quando a resposta livre ao menu inicial for compatível com três ou mais caminhos (não apenas dois), o sistema deve fazer o quê? → A: Cair no comportamento existente de reformulação (tratar como não reconhecido), já que FR-012 restringe a desambiguação do menu inicial a exatamente dois caminhos; para 3+ caminhos vale o catch-all de FR-010 (reformulação/encaminhamento humano), sem estender FR-008 ao menu inicial.
- Q: Qual estratégia deve escolher a variante de reformulação a cada nova tentativa, garantindo que nunca repita a variante do turno imediatamente anterior? → A: Ciclo sequencial determinístico por número da tentativa (tentativa 1 = variante 1, tentativa 2 = variante 2, reiniciando ao esgotar as variações disponíveis; por construção nunca repete a variante do turno imediatamente anterior). Escolha reprodutível e testável em conjunto de regressão (golden set) com o motor de reformulação real, em vez de seleção aleatória.

## User Scenarios & Testing

### User Story 1 - Lead corrige o rumo da conversa em qualquer ponto da jornada (Priority: P1)

Um lead que já está sendo atendido em um caminho (ex.: está respondendo
perguntas de qualificação de um caminho) percebe que quer outra coisa e diz
isso com as próprias palavras ("na verdade eu quero...", "me enganei",
"prefiro o curso X", etc.), inclusive citando o nome do produto/caminho
desejado com pequenos erros de digitação ou acentuação. O atendimento
reconhece a correção, confirma brevemente de forma natural, e passa a
conduzir o lead pelo caminho correto — sem repetir a pergunta anterior e
sem pedir de novo informações que o lead já havia fornecido antes (idioma,
qualificação profissional, especialidade, etc.).

**Why this priority**: É o cenário que motivou a feature e o de maior
impacto percebido — um lead que se sente "não-ouvido" abandona o
atendimento. Resolve diretamente o caso real relatado.

**Independent Test**: Reproduzir a conversa completa (menu → escolha de um
caminho → mensagem de correção citando outro produto) e verificar que o
atendimento reconhece a correção, não repete o bloco anterior, e conduz o
lead ao caminho correto (ou faz uma única pergunta de desambiguação quando
o produto citado for ambíguo entre dois caminhos).

**Acceptance Scenarios**:

1. **Given** um lead está respondendo a uma pergunta dentro de um caminho
   ativo, **When** ele envia uma mensagem com um marcador claro de correção
   e o nome (mesmo com pequeno erro de digitação/acentuação) de um produto
   pertencente a outro caminho, **Then** o atendimento confirma a mudança de
   forma breve e natural, no idioma do lead, e passa a conduzir a conversa
   pelo caminho correto, sem repetir a pergunta anterior.
2. **Given** a correção citada pelo lead poderia pertencer a mais de um
   caminho (ex.: menciona apenas "curso" sem especificar online ou
   presencial), **When** o atendimento processa a mensagem, **Then** ele
   faz exatamente **uma** pergunta objetiva de desambiguação — nunca volta a
   apresentar o menu completo de 6 opções.
3. **Given** um lead já havia informado sua qualificação profissional,
   idioma e especialidade antes da troca de caminho, **When** a troca de
   caminho acontece, **Then** o novo caminho não repete nenhuma dessas
   perguntas já respondidas.
4. **Given** um lead está no meio de uma jornada, **When** ele envia uma
   resposta legítima e direta à pergunta pendente (sem qualquer marcador de
   correção e sem citar outro produto), **Then** o atendimento segue
   normalmente no caminho atual — a conversa **nunca** é desviada por engano
   para outro caminho.

---

### User Story 2 - Lead digita livremente no menu inicial, mesmo com erros de digitação (Priority: P2)

Um lead que recebe o menu inicial de 6 opções não é obrigado a responder
com um número: ele pode escrever o nome do que procura ("harmonização
glutea", "quero o curso presencial", "sou aluno", etc.), inclusive com
pequenos erros de digitação ou acentuação, e ainda assim ser corretamente
direcionado ao caminho certo — ou receber uma única pergunta de
desambiguação quando a descrição for compatível com mais de um caminho.

**Why this priority**: O menu é o primeiro contato da jornada; um lead que
"trava" logo na primeira mensagem porque digitou algo em vez de escolher um
número tem alta chance de abandonar o atendimento.

**Independent Test**: Enviar, como primeira mensagem após o menu, uma
descrição textual do caminho desejado (com e sem erro de digitação) e
verificar que o lead é direcionado ao caminho correto ou recebe uma única
pergunta objetiva de desambiguação, sem o menu completo ser reapresentado.

**Acceptance Scenarios**:

1. **Given** o menu inicial foi apresentado, **When** o lead responde com o
   nome (ainda que com erro leve de digitação/acentuação) de um produto ou
   caminho específico, **Then** o atendimento avança diretamente para o
   caminho correspondente, sem exigir que o lead reenvie um número.
2. **Given** o menu inicial foi apresentado, **When** o lead responde com um
   termo compatível com mais de um caminho (ex.: menciona "curso" sem
   indicar a modalidade), **Then** o atendimento faz uma única pergunta
   direta de desambiguação em vez de reapresentar o menu completo.
3. **Given** o menu inicial foi apresentado, **When** o lead responde com um
   texto livre que, mesmo após a tentativa de reconhecimento, não indica
   claramente nenhum caminho, **Then** o atendimento segue o comportamento
   existente de reformulação (não trava indefinidamente nem falha
   silenciosamente).

---

### User Story 3 - Lead recebe reformulação humanizada quando não é compreendido (Priority: P2)

Quando o atendimento não consegue entender a mensagem do lead (não é uma
resposta reconhecida, não é uma correção de rumo, não é uma seleção de
menu), ele nunca repete a mesma mensagem, palavra por palavra, na tentativa
seguinte. Em vez disso, reconhece que não entendeu e reformula apenas a
pergunta pendente, com uma variação natural de texto — mantendo o
comportamento existente de encaminhar a um atendente humano após um número
limitado de tentativas sem sucesso.

**Why this priority**: Reduz a sensação de "atendimento robótico" e a
repetição idêntica de blocos completos (incluindo saudações), que é
justamente o comportamento observado no caso real que motivou a feature.

**Independent Test**: Enviar, em sequência, duas mensagens não reconhecidas
para a mesma pergunta pendente e verificar que a segunda mensagem do
atendimento não é idêntica à primeira, mantendo o mesmo idioma e, quando
possível, fazendo referência breve ao que o lead disse.

**Acceptance Scenarios**:

1. **Given** uma mensagem do lead não foi reconhecida como resposta,
   correção de rumo ou seleção de menu, **When** o atendimento responde,
   **Then** a resposta reformula apenas a pergunta pendente (não repete a
   introdução/saudação do bloco anterior) e é textualmente diferente da
   mensagem anterior enviada na mesma pergunta.
2. **Given** o lead atinge o número máximo de tentativas não reconhecidas
   para a mesma pergunta, **When** o atendimento processa a próxima
   mensagem, **Then** o atendimento é encaminhado a um atendente humano,
   preservando o comportamento e o limite já existentes.

---

### User Story 4 - Time de operação acompanha trocas de caminho e reformulações (Priority: P3)

A equipe responsável pela qualidade do atendimento consegue identificar,
depois do fato, quando e por que uma conversa mudou de caminho (qual
caminho de origem, qual caminho de destino, se a detecção foi por
reconhecimento direto de palavras-chave ou por classificação assistida, e
com que grau de confiança), e quando uma reformulação foi usada — sem que
isso quebre ou substitua os registros de atendimento já existentes.

**Why this priority**: Não afeta a experiência do lead diretamente, mas é
pré-condição para medir a qualidade da nova capacidade e ajustar
parâmetros com segurança antes de torná-la mais permissiva.

**Independent Test**: Revisar os registros de uma conversa em que uma troca
de caminho e uma reformulação ocorreram e confirmar que ambos os eventos
estão identificáveis nos registros, junto com os dados de atendimento já
existentes.

**Acceptance Scenarios**:

1. **Given** uma troca de caminho ocorreu durante uma conversa, **When** o
   registro daquele atendimento é consultado, **Then** é possível
   identificar o caminho de origem, o caminho de destino, o método de
   detecção e o grau de confiança envolvido.
2. **Given** uma reformulação ocorreu durante uma conversa, **When** o
   registro daquele atendimento é consultado, **Then** é possível
   identificar que uma reformulação ocorreu e qual variante foi usada.

---

### Edge Cases

- Um lead responde de forma legítima e direta a uma pergunta pendente, sem
  qualquer marcador de correção: a conversa **nunca** deve ser desviada
  para outro caminho por engano (comportamento existente que a feature não
  pode regredir).
- Um lead expressa uma intenção clara de outro caminho, mas **sem** usar
  nenhum marcador explícito de correção, no meio de uma pergunta pendente:
  o atendimento faz uma pergunta de confirmação curta antes de trocar de
  caminho, em vez de trocar silenciosamente.
- Um lead pede para "voltar" ou "ver o menu de novo": é tratado como um
  pedido de troca de rumo, não como uma resposta à pergunta pendente.
- Um lead tenta voltar a um caminho que ele já havia visitado antes na
  mesma conversa: o atendimento reinicia esse caminho do seu ponto lógico
  de início (não tenta retomar de onde a visita anterior parou).
- Um lead usa uma frase de correção, mas o produto/caminho citado não é
  reconhecível nem pelo reconhecimento direto nem pela classificação
  assistida: o atendimento cai no comportamento de reformulação existente,
  sem travar nem falhar silenciosamente.
- Uma conversa está com uma pendência de retomada após um excesso de
  mensagens (comportamento já existente de "retomar depois de excesso"): a
  retomada tem prioridade sobre a detecção de troca de caminho — uma
  mensagem simples de "pode continuar" nesse contexto não deve ser
  interpretada como troca de rumo.
- O mesmo cenário de correção de rumo, texto livre no menu e reformulação
  ocorre em português, inglês e espanhol, sempre respondendo no idioma do
  lead.
- Um lead expressa uma correção de rumo para o **mesmo** caminho em que já
  está: não deve gerar troca, reinício de contadores ou qualquer efeito
  colateral perceptível.

## Requirements

### Functional Requirements

- **FR-001**: O sistema DEVE, para cada mensagem recebida durante uma
  pergunta pendente, tentar primeiro reconhecê-la como resposta a essa
  pergunta usando as capacidades de reconhecimento já existentes, antes de
  considerar qualquer outra interpretação.
- **FR-002**: Quando a mensagem não for reconhecida como resposta à
  pergunta pendente, o sistema DEVE avaliar, de forma determinística (sem
  depender de nenhuma capacidade de IA), se a mensagem sinaliza uma
  intenção de mudar de caminho, usando reconhecimento de marcadores de
  correção e de nomes de produto/caminho.
- **FR-003**: O reconhecimento determinístico de marcadores de correção e
  de nomes de produto/caminho DEVE tolerar variações simples de escrita
  (maiúsculas/minúsculas, acentuação, pequenos erros de digitação).
- **FR-004**: Quando o reconhecimento determinístico não encontrar
  correspondência, o sistema DEVE recorrer à capacidade de classificação de
  intenção já existente, só aceitando uma troca de caminho quando o grau de
  confiança da classificação atingir um limiar mínimo configurável (padrão:
  60%).
- **FR-005**: Quando uma intenção de troca de caminho for clara e diferente
  do caminho atualmente ativo, o sistema DEVE confirmar a mudança de forma
  breve e natural, no idioma do lead, e conduzir a conversa para o novo
  caminho.
- **FR-006**: Ao trocar de caminho, o sistema DEVE preservar todas as
  informações já conhecidas sobre o lead (ex.: qualificação profissional,
  idioma, especialidade, produto de interesse) e NÃO DEVE perguntar
  novamente por informações já respondidas.
- **FR-007**: Ao trocar de caminho, o sistema DEVE zerar os contadores de
  tentativa associados à pergunta pendente do caminho abandonado, sem
  alterar os contadores de orçamento de turnos da sessão como um todo.
- **FR-008**: Quando a intenção detectada for plausível mas ambígua entre
  dois ou mais caminhos, o sistema DEVE fazer exatamente uma pergunta
  direta de desambiguação, e NUNCA reapresentar o menu inicial completo
  nesse caso.
- **FR-009**: Uma resposta legítima e direta a uma pergunta pendente NUNCA
  DEVE ser interpretada como uma troca de caminho (o comportamento
  conservador já existente para este caso deve permanecer intacto).
- **FR-010**: Quando nem o reconhecimento da resposta, nem a detecção de
  troca de caminho, nem a desambiguação resolverem a mensagem, o sistema
  DEVE cair no comportamento já existente de reformulação/encaminhamento
  humano, preservando o limite de tentativas e o encaminhamento automático
  a um atendente humano ao atingir esse limite.
- **FR-011**: O menu inicial DEVE reconhecer respostas em texto livre (não
  apenas seleção numérica) para identificar o caminho desejado, usando o
  mesmo reconhecimento determinístico de palavras-chave usado para a
  detecção de troca de caminho no meio da jornada.
- **FR-012**: Quando a resposta em texto livre ao menu inicial for ambígua
  entre exatamente dois caminhos específicos (ex.: curso online vs.
  presencial), o sistema DEVE fazer uma pergunta direta de desambiguação em
  vez de reapresentar o menu.
- **FR-013**: Pequenos erros de digitação na resposta em texto livre ao
  menu inicial NÃO DEVEM impedir o lead de ser corretamente direcionado
  quando o caminho pretendido for, ainda assim, reconhecível.
- **FR-014**: Quando uma mensagem não for compreendida, o sistema NUNCA
  DEVE reenviar o mesmo bloco de mensagem enviado no turno anterior; DEVE,
  em vez disso, enviar uma reformulação apenas da pergunta pendente (sem
  repetir a introdução/saudação do bloco original).
- **FR-015**: As reformulações DEVEM ter ao menos 2 a 3 variações de texto
  por idioma suportado e, quando possível, fazer referência breve ao que o
  lead disse. A seleção da variante a cada nova tentativa DEVE seguir um
  ciclo sequencial determinístico pelo número da tentativa (tentativa 1 =
  variação 1, tentativa 2 = variação 2, reiniciando o ciclo ao esgotar as
  variações disponíveis), garantindo por construção que a variante do
  turno imediatamente anterior nunca se repita.
- **FR-016**: O comportamento existente de contagem de tentativas e
  encaminhamento automático a humano após o número máximo de tentativas
  DEVE permanecer com o mesmo escopo e limite já vigentes.
- **FR-017**: O sistema DEVE registrar, para cada evento de troca de
  caminho, o caminho de origem, o caminho de destino, o método de detecção
  (reconhecimento direto ou assistido por classificação) e o grau de
  confiança envolvido, de forma aditiva aos registros de atendimento já
  existentes, sem alterar sua estrutura atual.
- **FR-018**: O sistema DEVE registrar, para cada reformulação usada, qual
  variante foi enviada, de forma aditiva aos registros de atendimento já
  existentes.
- **FR-019**: Nenhuma das capacidades acima DEVE alterar a estrutura
  determinística da jornada oficial, a autoridade de roteamento (que
  permanece fora do controle de qualquer capacidade de IA), o texto oficial
  de conteúdos enviados na íntegra, ou o comportamento existente de
  fundamentação e recusa em respostas informativas.
- **FR-020**: Todas as salvaguardas conversacionais já existentes DEVEM
  permanecer intactas, incluindo: uma pergunta por mensagem (exceto menus),
  suporte a português/inglês/espanhol, tratamento da mensagem do lead como
  dado não confiável para fins de decisão de fluxo, e a prioridade da
  retomada de conversas interrompidas por excesso de mensagens sobre
  qualquer nova detecção de troca de rumo.
- **FR-021**: Ao retornar a um caminho já visitado anteriormente na mesma
  conversa, o sistema DEVE reiniciar esse caminho a partir do seu ponto
  lógico de início, e NÃO DEVE tentar retomar do ponto em que a visita
  anterior parou.

> Decisões de infraestrutura: N/A explícito — a feature reaproveita o
> armazenamento de sessão já existente para guardar o caminho anterior;
> nenhum scheduler novo, rotação de chaves, refresh de token externo, lock
> multi-instância ou rotina de backup é introduzido.

### Key Entities

- **Evento de Troca de Caminho**: representa uma transição reconhecida de
  um caminho para outro no meio de uma conversa. Atributos: caminho de
  origem, caminho de destino, método de detecção (direto ou assistido),
  grau de confiança, momento em que ocorreu.
- **Tentativa de Reformulação**: representa uma mensagem não compreendida e
  a variação de texto escolhida para perguntar novamente. Atributos: número
  da tentativa, variante de texto usada, idioma.
- **Perfil do Lead (existente, estendido)**: conjunto de atributos já
  conhecidos sobre o lead (qualificação profissional, idioma, especialidade,
  produto de interesse) que deve ser preservado através de trocas de
  caminho.

## Success Criteria

### Measurable Outcomes

- **SC-001**: No cenário real relatado (correção de rumo citando outro
  produto no meio de uma pergunta pendente), o lead é conduzido ao caminho
  correto ou recebe uma única pergunta de desambiguação no mesmo turno em
  que fez a correção, sem que a mensagem do turno anterior seja repetida.
- **SC-002**: 0% das respostas em texto livre com erro leve de digitação no
  menu inicial resultam em reapresentação do menu completo, quando o
  caminho pretendido é, ainda assim, reconhecível.
- **SC-003**: 0% das respostas legítimas e diretas a uma pergunta pendente
  resultam em troca indevida de caminho (medido por conjunto de casos de
  regressão).
- **SC-004**: 0% dos leads recebem a mesma mensagem repetida, palavra por
  palavra, em dois turnos consecutivos quando sua mensagem não é
  compreendida.
- **SC-005**: 0% das trocas de caminho resultam em uma pergunta de
  qualificação já respondida sendo feita novamente.
- **SC-006**: 100% dos eventos de troca de caminho e de reformulação
  ocorridos ficam identificáveis nos registros de atendimento existentes,
  sem lacunas.
- **SC-007**: Os comportamentos acima são verificados em conversas conduzidas
  em português, inglês e espanhol.
