# Padrões de implementação (agnósticos de stack)

Templates reutilizáveis para os pilares da skill. São **pseudocódigo e esquemas
conceituais** — adapte os tipos e a sintaxe ao seu banco/orquestrador.

## Índice

1. Schema do estado da sessão (Pilar 2)
2. Contrato JSON de saída do LLM (Pilar 6)
3. Roteamento determinístico vs. tarefa do LLM (Pilares 1 e 6)
4. Recuperação ancorada: blocos canônicos + RAG híbrido (Pilar 4)
5. Prompt do portão de verificação (Pilar 5)
6. Memória em camadas (Pilar 3)
7. Golden set de avaliação (Pilar 8)

---

## 1. Schema do estado da sessão (Pilar 2)

Uma linha por conversa, chaveada por um id estável do canal. Os campos de
qualificação variam por domínio — abaixo, um exemplo genérico de SDR/atendimento.

```
sessao_atendimento
------------------------------------------------------------
conversa_id            TEXTO   (PK, id estável do canal)
no_atual               TEXTO   (estado da máquina: ex. "qualificacao", "duvidas")
idioma                 TEXTO   (pt / en / es — resolve o slot de idioma)
produto_em_contexto    TEXTO   (qual oferta está sendo tratada)
intencao_inicial       TEXTO   (capturada na 1ª mensagem, se já clara)
-- slots de qualificação (adapte ao domínio) --
elegivel               BOOL?   (sim/não/desconhecido)
perfil                 TEXTO?  (segmento do lead)
opcao_escolhida        TEXTO?  (turma/plano/variante)
objecoes_tratadas      TEXTO[] (ids de objeções já respondidas — não repetir)
-- controle operacional --
turnos_no_no           INT     (para o limite de turnos do Pilar 7)
ultima_interacao       TIMESTAMP
precisa_humano         BOOL
status                 TEXTO   (ativo / encerrado / em_handoff)
```

Regra de ouro: **nunca pergunte algo cujo slot já está preenchido.** Antes de
qualquer pergunta, o orquestrador checa o slot correspondente.

---

## 2. Contrato JSON de saída do LLM (Pilar 6)

O LLM responde SEMPRE neste formato; o orquestrador parseia e age. Deixe explícito
no system prompt: "responda apenas com JSON, sem markdown, sem texto antes ou depois".

```json
{
  "intencao": "curso_online | curso_presencial | suporte | outro | mudou_assunto",
  "slots_extraidos": {
    "elegivel": true,
    "perfil": "iniciante",
    "opcao_escolhida": null
  },
  "proxima_acao": "enviar_bloco | responder_duvida | pedir_slot | abster | handoff",
  "bloco_id": "apresentacao_curso_online_pt",
  "resposta": null,
  "fonte_ids": ["faq_007"],
  "precisa_humano": false,
  "confianca": 0.0
}
```

Notas:
- `proxima_acao = enviar_bloco` → o **código** envia o `bloco_id` verbatim. O LLM
  nunca escreve o conteúdo do bloco.
- `proxima_acao = responder_duvida` → a `resposta` é a redação gerada, e `fonte_ids`
  rastreia os chunks que a embasaram (Pilar 4d) — entrada do portão de verificação.
- `proxima_acao = abster` → sem fonte boa: dispara a mensagem padrão de "não tenho
  essa informação" + handoff.
- Sempre valide o JSON. Se vier malformado, faça 1 retry com instrução de correção;
  se falhar de novo, caia no handoff em vez de improvisar.

---

## 3. Roteamento determinístico vs. tarefa do LLM (Pilares 1 e 6)

O laço de cada turno, conceitualmente:

```
ao_receber_mensagem(conversa_id, texto):
    estado = carregar_estado(conversa_id)            # Pilar 2
    contexto = montar_contexto(estado)               # curto prazo + resumo (Pilar 3)

    saida = chamar_llm(                               # papel estreito (Pilar 6)
        tarefa = tarefa_do_no(estado.no_atual),      # ex.: "classifique a intenção"
        contexto = contexto,
        mensagem = texto,
        formato = CONTRATO_JSON
    )
    validar_json(saida)

    estado = aplicar_slots(estado, saida.slots_extraidos)   # persiste o que foi extraído
    estado = transicao(estado, saida)                       # O CÓDIGO decide o próximo nó

    se saida.proxima_acao == "enviar_bloco":
        enviar( bloco_canonico(saida.bloco_id, estado.idioma) )   # verbatim (Pilar 4a)
    senão se saida.proxima_acao == "responder_duvida":
        chunks = recuperar(texto, filtros=estado)             # RAG híbrido (Pilar 4b)
        se melhor_score(chunks) < LIMIAR: abster_e_handoff()
        resposta = redigir(chunks)
        se not verificar_fidelidade(resposta, chunks): fallback()  # Pilar 5
        enviar(resposta)
    senão se saida.proxima_acao == "abster": abster_e_handoff()
    senão se saida.proxima_acao == "handoff": encaminhar_humano()

    estado.turnos_no_no = limitar(estado.turnos_no_no + 1)   # Pilar 7
    salvar_estado(estado)
```

A decisão de **qual nó vem depois** é da função `transicao()`, não do LLM. O LLM só
informa o que entendeu da mensagem.

---

## 4. Recuperação ancorada (Pilar 4)

### 4a. Blocos canônicos (verbatim)

```
blocos_canonicos
------------------------------------------------------------
bloco_id     TEXTO  (PK, ex. "apresentacao_curso_online")
idioma       TEXTO
conteudo     TEXTO  (texto oficial, enviado SEM passar pelo LLM)
versao       INT    (versione: mudou o preço, sobe a versão)
```

Envio: `enviar(blocos_canonicos[bloco_id][idioma].conteudo)`. Ponto final.

### 4b. RAG híbrido com abstenção (pseudocódigo)

```
recuperar(consulta, filtros):
    cand_vetorial = busca_vetorial(consulta, filtros, k=20)     # semântica
    cand_textual  = busca_textual(consulta, filtros, k=20)      # termos exatos
    fundidos      = reciprocal_rank_fusion(cand_vetorial, cand_textual)
    rerankeados   = reranker(consulta, fundidos)[:5]            # precisão
    retornar rerankeados

# no orquestrador:
chunks = recuperar(texto, filtros={produto, idioma})
se score(chunks[0]) < LIMIAR:        # ex.: LIMIAR = 0.45 (calibre com o golden set)
    abster_e_handoff()               # NÃO responda sem fonte
```

`filtros` aplica os metadados do chunk (produto, tipo, idioma) ANTES da busca
vetorial — evita aplicar objeção do produto errado.

### 4c. Chunking

Uma unidade semântica por chunk (uma objeção, uma entrada de FAQ, uma seção). Anexe
metadados: `{ produto, tipo: "objecao|faq|base", idioma, fonte_doc }`.

---

## 5. Prompt do portão de verificação (Pilar 5)

Chamada barata, separada, antes de enviar respostas geradas:

```
SISTEMA: Você é um verificador de fidelidade. Receberá um CONTEXTO (trechos oficiais)
e uma RESPOSTA. Decida se TODA afirmação factual da resposta (preços, datas, condições,
elegibilidade, capacidades) está sustentada pelo contexto. Não use conhecimento próprio.
Responda só com JSON: {"fiel": true|false, "afirmacoes_nao_sustentadas": ["..."]}.

USUÁRIO:
CONTEXTO:
<chunks recuperados>
RESPOSTA:
<resposta gerada>
```

Se `fiel == false` → não envie: caia no bloco canônico correspondente ou no handoff.

---

## 6. Memória em camadas (Pilar 3)

```
montar_contexto(estado):
    curto  = ultimas_n_mensagens(estado.conversa_id, n=10)
    se contagem_mensagens > LIMITE:
        resumo = resumo_progressivo(estado.conversa_id)   # condensa o antigo
        retornar resumo + curto
    retornar curto

# perfil de longo prazo (entre sessões), opcional:
perfil_lead
------------------------------------------------------------
lead_id    TEXTO (PK)
fatos      JSON   (ex.: { "elegivel": true, "interesse": "...", "ts": "..." })
```

---

## 7. Golden set de avaliação (Pilar 8)

30–50 casos reais com a resposta esperada. Rode a cada mudança de prompt/base.

```json
{
  "casos": [
    {
      "id": "preco_online_pt",
      "mensagem": "quanto custa o curso online?",
      "estado_inicial": { "no_atual": "duvidas", "produto_em_contexto": "online", "idioma": "pt" },
      "esperado": {
        "proxima_acao": "enviar_bloco",
        "bloco_id": "apresentacao_curso_online_pt",
        "nao_deve_conter_valor_diferente_de": "R$ 6.997,00"
      }
    },
    {
      "id": "fora_da_base",
      "mensagem": "vocês parcelam em 24x sem juros?",
      "estado_inicial": { "no_atual": "duvidas", "idioma": "pt" },
      "esperado": { "proxima_acao": "abster", "precisa_humano": true }
    }
  ]
}
```

Métricas a acompanhar: taxa de groundedness (respostas sustentadas pela fonte), taxa
de abstenção correta (abster quando não há fonte), zero invenções de preço/data, e
fluxo correto (não repetir slot já preenchido, não pular etapa obrigatória).
