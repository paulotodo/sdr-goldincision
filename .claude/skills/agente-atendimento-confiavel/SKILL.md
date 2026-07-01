---
name: agente-atendimento-confiavel
description: >-
  Metodologia agnóstica de stack para arquitetar agentes de atendimento, SDR,
  qualificação de leads e suporte que NÃO alucinam e mantêm controle de sessão em
  conversas longas. Use sempre que o usuário for construir, projetar, depurar ou
  melhorar um agente de atendimento, chatbot de vendas, agente de qualificação de
  leads, agente de suporte ou assistente conversacional — especialmente quando
  mencionar alucinação, controle de sessão, memória, RAG, fluxo de atendimento,
  máquina de estados, ou quando o agente repetir perguntas, pular etapas, inventar
  preços/datas/políticas ou perder o contexto. Aplica-se a qualquer stack (n8n,
  LangGraph, Python, Make, Dify, etc.). Build reliable customer-service / SDR /
  lead-qualification / support agents — anti-hallucination, session control,
  memory, RAG, conversation flow, state machine.
---

# Agente de Atendimento Confiável

Esta skill é uma metodologia para construir agentes conversacionais de atendimento,
vendas (SDR) e suporte que sejam **confiáveis em produção**: que não inventem
informação e não percam o controle da sessão em conversas longas. É **agnóstica de
stack** — vale para n8n, LangGraph, Python puro, Make, Dify ou qualquer orquestrador.

## Quando usar

Use ao projetar, construir, depurar ou revisar qualquer agente que conversa com
clientes/leads: SDRs de qualificação, atendentes de suporte, assistentes de vendas,
recepcionistas virtuais. Use com prioridade quando o sintoma for **alucinação**
(inventa preço, data, política, link, capacidade) ou **perda de sessão** (repete
perguntas já respondidas, pula etapas obrigatórias, reapresenta menu, esquece o
caminho em que estava, se contradiz).

Não use para agentes autônomos de tarefa/código sem interação conversacional, nem
para chamadas de LLM stateless de turno único — aqui o foco é a conversa longa,
multi-turno, com fluxo e qualificação.

## O princípio central (leia antes de tudo)

A maioria dos agentes de atendimento falha pela mesma razão: o LLM acumula quatro
papéis ao mesmo tempo — **roteia o fluxo, "lembra" o contexto, gera o conteúdo e
decide a próxima etapa**. Essa sobrecarga é a fonte tanto da alucinação quanto da
perda de sessão. A correção que sustenta toda esta skill:

> **Rebaixe o LLM a uma camada de linguagem. Promova código determinístico,
> estado estruturado e recuperação ancorada a donos do controle.**

Cada papel que você tira do LLM e devolve para código ou para um estado em banco
remove uma classe inteira de erro. O LLM passa a fazer só o que modelo de linguagem
faz bem: classificar intenção, extrair informação de texto e redigir naturalmente.
Ele para de **decidir** e de ser **fonte da verdade**.

Saiba distinguir os dois tipos de alucinação, porque as correções diferem:
- **Factualidade** — a saída contradiz o mundo. Corrige-se com ancoragem (RAG).
- **Fidelidade** — a saída contradiz a própria fonte fornecida (recebe o preço certo
  e mesmo assim reescreve errado). É o erro mais caro em atendimento comercial.
  Corrige-se com conteúdo verbatim + portão de verificação.

Alucinação não é problema de modelo; é problema de **design de sistema**. Trocar por
um modelo "mais inteligente" não resolve sozinho — escolha o modelo pela calibração e
construa os controles abaixo em volta dele.

## Fluxo de construção

Construa (ou conserte) nesta ordem de prioridade — cada passo remove uma fonte de erro
maior que o anterior:

1. **Blocos canônicos verbatim** (Pilar 4a) — menor esforço, mata o pior tipo de
   alucinação na hora: preço, data, política e link nunca mais são "gerados".
2. **Máquina de estados + estado estruturado** (Pilares 1 e 2) — resolve perda de
   sessão, repetição de perguntas e etapa pulada.
3. **Recuperação ancorada com abstenção** (Pilar 4b) — fecha a alucinação nas
   respostas livres (FAQ, objeções, dúvidas).
4. **Portão de verificação** (Pilar 5) — rede de segurança para preço/data/elegibilidade.
5. **Memória em camadas, controle operacional e observabilidade** (Pilares 3, 7, 8) —
   maturidade, naturalidade e escala.

Não tente fazer tudo de uma vez. Os passos 1 e 2 sozinhos costumam eliminar a maioria
das reclamações.

## Os 8 pilares

### 1. Externalize o fluxo: máquina de estados FORA do prompt

O mapa de atendimento (saudação → qualificação → apresentação → dúvidas → fechamento →
handoff) deve viver como **lógica de código**, não como instrução em linguagem natural
no prompt. Quando o LLM "decide em prosa" qual a próxima etapa, ele pula qualificação
obrigatória, reapresenta o menu e repete perguntas — porque a decisão é dele.

Modele o atendimento como estados explícitos com transições determinísticas. O código
decide o nó; o LLM só executa a tarefa estreita daquele nó. Assim "intenção já
identificada → entra direto no fluxo" vira um `if`, não uma esperança.
→ Schema e exemplo em `references/padroes-implementacao.md`.

### 2. Estado estruturado é a memória de controle (não o histórico de chat)

O erro mais comum é fazer o LLM "lembrar" relendo a transcrição a cada turno. Em sessão
longa isso falha: o histórico é truncado e ele esquece a qualificação, ou o contexto
incha e ele se contradiz.

Mantenha um registro estruturado por conversa (chaveado por um id de conversa estável),
com os **slots** que o fluxo realmente usa — nó atual, idioma, produto em contexto, e os
campos de qualificação do seu domínio (ex.: é elegível? qual perfil? qual opção
escolheu?). Esse objeto é a verdade da sessão: o LLM extrai o slot da mensagem, o código
persiste. "Manter memória do contexto" deixa de ser instrução frágil e vira leitura de
banco — barata, determinística e imune à janela de contexto.

### 3. Memória em camadas

O estado do Pilar 2 cuida do controle. Para naturalidade e personalização, use três
camadas distintas:
- **Curto prazo** — janela deslizante das últimas N mensagens, enviada literal.
- **Resumo contínuo** — ao passar de um limite, um resumo progressivo condensa o que já
  aconteceu e substitui o histórico antigo (controla custo e o efeito "perdido no meio").
- **Longo prazo** — perfil do lead (fatos extraídos) que persiste entre sessões.

O estado da arte saiu do "guardar histórico" para memória **estruturada, com escopo e
temporal** (cada fato com janela de validade). Comece simples e nativo do seu banco;
só adote uma camada de memória dedicada quando a personalização cross-sessão virar
gargalo real. Não terceirize memória antes de precisar.

### 4. Recuperação ancorada (o coração do anti-alucinação)

**4a — Conteúdo oficial verbatim NÃO passa pelo LLM.** Apresentações, preços, datas,
condições e links são exatamente o que não pode ser reescrito. Não deixe o modelo
gerá-los: guarde cada um como **bloco canônico com ID** e envie-o literal, por código.
O LLM só decide *qual* bloco mandar; o texto sai intacto. Quando o fato vem de uma busca
determinística em vez de geração livre, não há espaço para alucinar. Este único ajuste
elimina preço errado, data trocada e link inventado de uma vez.

**4b — Para respostas livres (FAQ/objeções/dúvidas): recuperação híbrida + rerank +
limiar + abstenção.** Combine busca semântica (vetorial) com busca por palavra-chave
(termos exatos importam), funda os resultados, acrescente reranking e — crucial — um
**limiar de relevância**. Se nada vier acima do limiar, **o agente não responde**: ele
se abstém e aciona o handoff humano ("não tenho essa informação"). Abstenção forçada é o
que impede o modelo de preencher lacuna com conhecimento próprio.

**4c — Chunking por unidade semântica, não por tamanho fixo.** Um chunk = uma objeção,
uma entrada de FAQ, uma seção coerente — com metadados (produto, tipo, idioma) para
pré-filtrar antes da busca vetorial. Isso respeita a hierarquia de documentos e impede
aplicar a objeção do produto A num lead do produto B.

**4d — Atribuição.** Toda resposta gerada carrega internamente a fonte (qual chunk a
embasou). Serve ao Pilar 5 e à depuração.

### 5. Portão de verificação de fidelidade

Antes de enviar qualquer resposta **gerada** (não os blocos canônicos), cheque: ela usa
apenas fatos presentes nos chunks recuperados? A técnica madura é a auto-verificação
(cadeia de verificação): o modelo confere cada afirmação contra o contexto; melhor ainda
é um segundo modelo pequeno como verificador. Aplique obrigatoriamente quando a resposta
toca preço, data, elegibilidade ou condição comercial. Falhou na verificação → cai no
fallback (bloco canônico ou handoff). Nunca envia uma resposta não verificada.

### 6. Saída estruturada + guardrails

Faça o LLM responder sempre em **JSON com contrato fixo** (intenção, slots extraídos,
próxima ação, bloco/resposta, precisa_humano) e deixe o orquestrador parsear. Isso mata o
"decidir em prosa". Combine com temperatura baixa (0–0.2) nas etapas factuais; bloqueio de
saída que cite valores/condições fora dos blocos oficiais; e checagem de que o idioma da
resposta bate com o slot de idioma. → Contrato em `references/padroes-implementacao.md`.

### 7. Controle operacional de sessão longa

Além do estado, a sessão longa precisa de regras operacionais: **limite máximo de turnos
por nó** (evita loop — sempre defina limites de iteração), timeout de inatividade com
reengajamento, e handoff explícito quando o usuário pedir humano. Para robustez real,
use **execução durável**: o estado do workflow persiste, então se o sistema reiniciar no
meio de uma conversa de dias, ela retoma de onde parou em vez de recomeçar.

### 8. Observabilidade e avaliação contínua

Não se corrige alucinação que não se enxerga. Registre cada turno: intenção detectada,
chunks recuperados com score, bloco/resposta enviada e fonte. Monte um **golden set** —
30 a 50 conversas reais com a resposta correta — rodado a cada mudança de prompt ou base,
medindo groundedness e fidelidade. É o que transforma "acho que melhorou" em número e
pega regressão antes do cliente pegar. → Formato em `references/padroes-implementacao.md`.

## Anti-padrões a evitar

Estes são exatamente os hábitos que produzem alucinação e perda de sessão:

- **Fluxo no prompt.** Deixar o LLM decidir a próxima etapa em texto. → Pilar 1.
- **Memória por transcrição.** Confiar que reler o histórico mantém o contexto. → Pilar 2.
- **Gerar fatos.** Deixar o LLM redigir preço/data/política. → Pilar 4a.
- **RAG sem abstenção.** Sempre responder, mesmo sem fonte boa. → Pilar 4b.
- **Chunk de tamanho fixo.** Quebrar objeções/FAQ no meio. → Pilar 4c.
- **Enviar sem verificar.** Confiar que o modelo respeitou o contexto. → Pilar 5.
- **Saída em prosa.** Sem contrato JSON, sem como o código agir com segurança. → Pilar 6.
- **Sem limite de turnos.** Loop infinito quando o usuário insiste. → Pilar 7.
- **Sem logs nem eval.** Não dá para saber se piorou. → Pilar 8.
- **"Modelo mais inteligente resolve."** Não resolve. O design é que resolve.

## Como mapear num stack

A metodologia é agnóstica; o mapeamento é direto:
- **Orquestrador / máquina de estados** → nós de fluxo (n8n, LangGraph, código próprio).
- **Estado da sessão e perfil** → banco relacional (Postgres, MySQL) chaveado por id de conversa.
- **Blocos canônicos** → tabela/arquivos versionados, enviados literal.
- **RAG híbrido** → banco vetorial + busca textual (ex.: pgvector + full-text), com rerank.
- **Portão de verificação** → uma chamada de LLM barata antes do envio.
- **Canal** → WhatsApp/web/telefone via a plataforma de mensagens.

## Antes de colocar em produção

Rode o checklist de prontidão antes de subir: `references/checklist.md`.

## Arquivos de referência

- `references/padroes-implementacao.md` — templates agnósticos de stack: schema do estado
  da sessão, contrato JSON de saída do LLM, recuperação híbrida (pseudocódigo), prompt do
  portão de verificação e formato do golden set de avaliação.
- `references/checklist.md` — checklist de prontidão para produção, organizado por pilar.
