# Research: sdr-fluidez-intencao

Documento do Phase 0 do `/plan`. A spec não deixou nenhum `[NEEDS
CLARIFICATION]` (4 perguntas foram resolvidas na etapa `clarify`, dec-007/
008/009/012). As decisões abaixo resolvem escolhas de abordagem técnica com
trade-offs reais, ancoradas no código existente (`app/core/flow.py`,
`app/core/intent.py`, `app/core/interpret.py`) e na constitution.

## Decision 1: Léxico compartilhado de marcadores de correção e produtos/caminhos

**Decision**: Nova constante `_LEXICO_CAMINHOS` (dict `int → set[str]` de
variantes normalizadas de nome de produto/caminho, incluindo erros leves de
digitação/acentuação comuns) e `_MARCADORES_CORRECAO` (set de tokens/frases
por idioma que sinalizam correção explícita: "na verdade", "me enganei",
"prefiro", "quero mudar", "actually", "in fact", "de hecho", etc.), ambas em
`app/core/flow.py`, reusando o helper de normalização já existente `_norm()`
(minúsculas, sem acento, sem pontuação — `app/core/flow.py:2805`) e o mesmo
padrão de matching por substring/token já usado em `_detectar_escolha_turma`/
`_detectar_especialidade`/`_opcao_numerica`. Usada em DOIS pontos: (a) o
fast-path de texto livre do menu inicial (FR-011/012/013) e (b) o detector de
troca de caminho no meio da jornada (FR-002/003).

**Rationale**: FR-011 exige explicitamente "o **mesmo** reconhecimento
determinístico de palavras-chave usado para a detecção de troca de caminho".
Fonte única evita drift entre os dois usos (o mesmo tipo de bug que motivou
esta feature: comportamento inconsistente entre pontos do código que deveriam
ser idênticos). Segue o padrão já estabelecido no arquivo (normalização +
matching por token, nunca regex complexa ou fuzzy-matching probabilístico).

**Alternatives considered**:
- Dois léxicos separados (um por site de uso) → duplicação, risco de drift,
  viola FR-011 explicitamente.
- Fuzzy-matching por distância de edição (Levenshtein) → mais tolerante a
  erro de digitação, mas não-determinístico na prática (limiares de
  distância variam por tamanho de palavra) e mais caro de testar/auditar do
  que substring/token match com variantes explícitas; o projeto já resolve
  "pequeno erro de digitação" hoje em `_detectar_escolha_turma` com variantes
  explícitas (`{"sp", "sampa"}`), sem biblioteca de fuzzy-matching.

## Decision 2: Fallback de classificação assistida — reusar `SlotExtractor` (Pilar 8), NÃO `IntentClassifier.classify()`

**Decision**: Nova função assíncrona no `FlowEngine` (`_resolver_troca_caminho_agentica`)
que usa o `SlotExtractor` já existente (`app/core/interpret.py`) com um novo
`slot_schema` (`_SLOT_SCHEMA_TROCA_CAMINHO`) pedindo ao LLM barato
(gpt-4o-mini, Structured Outputs) extrair o caminho-alvo (um dos 6 valores
normalizados, ou `None`) + `confianca: float 0..1` — mesmo shape de
`SlotQualificacao`. Aceite via `SlotExtractor.aceitar(slot, limiar)` com novo
env `INTENT_SWITCH_CONFIDENCE_THRESHOLD` (default `0.6`, pydantic-settings).

**Rationale**: `IntentClassifier.classify()` (`app/core/intent.py`) tem
contrato de retorno **travado em 2-tupla** `(ClassificacaoIntencao, Idioma)`
— gotcha documentado em `CLAUDE.md` ("vários testes mockam esse contrato,
não mudar para 3-tupla"). Verificado empiricamente
(`app/core/intent.py:109-164`): o classificador já resolve confiança
internamente (`"alta"|"baixa"`) e **colapsa qualquer confiança "baixa" para
`AMBIGUA` antes de retornar** (linha 156) — não existe valor numérico
exposto, nem limiar configurável, e mudar isso quebraria o contrato
protegido. Em contraste, `SlotExtractor` (`app/core/interpret.py:88-162`) é
literalmente descrito como "o FALLBACK agentico chamado SOMENTE quando o
fast-path determinístico... não resolve" e já expõe `confianca: float`
gateado por `settings.slot_confidence_threshold` — o **mesmo shape
estrutural** exigido por FR-004 ("só aceitando... quando o grau de confiança
da classificação atingir um limiar mínimo configurável"). Reusar evita um 2º
sistema de classificação LLM paralelo.

**Alternatives considered**:
- Mudar `IntentClassifier.classify()` para 3-tupla com confiança numérica →
  quebra contrato protegido por gotcha; risco alto de regressão nos testes
  existentes que mockam a 2-tupla.
- Método novo `IntentClassifier.classify_com_confianca()` paralelo ao
  existente → duplica lógica de prompt/parsing já resolvida no
  `SlotExtractor`, sem necessidade.
- Mapear `alta`/`baixa` para `1.0`/`0.0` com limiar fixo não-configurável →
  não atende "limiar mínimo configurável (padrão 60%)" de FR-004; também
  seria redundante, pois `classify()` já rebaixa "baixa" para `AMBIGUA`
  internamente — o chamador nunca veria uma confiança "baixa" de qualquer
  forma.

## Decision 3: Ponto de inserção do detector — dentro de `_reformular_ou_handoff`, não nos ~10 call sites individualmente

**Decision**: `_reformular_ou_handoff` (`app/core/flow.py:1448`) ganha um
novo parâmetro obrigatório `user_message: str`. Internamente, **antes** de
incrementar o contador anti-loop (`_tent_bump`), roda o pipeline do detector:
(1) léxico determinístico (Decision 1); (2) se não casar, fallback agentico
confidence-gated (Decision 2). Se detectar troca clara e inequívoca →
despacha para o novo caminho via `_despachar_caminho` (preservando perfil,
zerando o contador da etapa abandonada via `_tent_clear`, reiniciando o
caminho-alvo do zero — FR-021). Se ambíguo entre exatamente dois caminhos →
pergunta de desambiguação (Decision 6, nova etapa transiente). Se não houver
marcador explícito mas a intenção for clara → pergunta de confirmação curta
(edge case da spec) antes de trocar. Caso contrário, cai no comportamento
já existente (reformulação/handoff, Decision 7). Os ~10 call sites
(`grep -n _reformular_ou_handoff app/core/flow.py`) precisam ser atualizados
para passar `user_message` — mudança mecânica, sem lógica nova em cada site.

**Rationale**: Único choke-point garante que TODO fluxo que hoje cai em
"não reconhecido" ganha a nova capacidade sem duplicar lógica em ~10
lugares (DRY, menor superfície de regressão). Satisfaz **por construção**:
FR-001 (o resolver específico do nó — ex.: `_resolver_especialidade` —
sempre tenta primeiro; o detector só roda depois de `_reformular_ou_handoff`
ser chamado, ou seja, depois que o resolver já retornou `None`) e FR-009
(uma resposta legítima nunca é lida como troca, porque essa função só é
alcançada quando o resolver ESPECÍFICO da etapa já falhou em reconhecer
como resposta válida — uma resposta reconhecida nunca chega aqui).

**Alternatives considered**:
- Editar cada handler individualmente para chamar o detector antes de
  `_reformular_ou_handoff` → ~10 pontos de manutenção, alto risco de um
  handler futuro esquecer a chamada (o mesmo tipo de risco que
  `_ETAPAS_AGUARDANDO_RESPOSTA` já existe para mitigar no fix #9).
- Rodar o detector no início de `_process_core`, antes do despacho ao
  handler → perderia a garantia estrutural de FR-001/FR-009 (o resolver do
  nó ainda não teria tido a chance de reconhecer a resposta legítima),
  reintroduzindo exatamente o risco que o fix #9 já mitiga na classificação
  global.

## Decision 4: Supressão de `_ETAPAS_AGUARDANDO_RESPOSTA` preservada SOMENTE na classificação global — não no novo detector

**Decision**: A checagem `context.etapa in _ETAPAS_AGUARDANDO_RESPOSTA`
(`app/core/flow.py:1305`, fix #9) permanece **intocada** e continua
bloqueando troca de caminho via reclassificação GLOBAL de intenção
(`app/core/flow.py:~1250-1300`, executada em `_process_core` antes do
despacho ao handler). O novo detector (Decision 3) é um mecanismo
**diferente**, que roda **depois** que o resolver específico da etapa já
falhou — e é projetado para operar justamente durante etapas em
`_ETAPAS_AGUARDANDO_RESPOSTA` (é o cerne de US1: o lead corrige *dentro* de
uma etapa de qualificação).

**Rationale**: Evita reintroduzir o bug #9 original (reclassificação global
ampla capturando respostas legítimas — ex.: "pode continuar" — como falsa
troca de caminho) enquanto habilita exatamente o comportamento pedido. Os
dois mecanismos coexistem porque disparam em pontos e com evidências
diferentes: reclassificação global = intenção ampla via LLM sem contexto de
"resolver já falhou"; novo detector = léxico determinístico explícito +
fallback confidence-gated, só após falha comprovada do resolver da etapa
corrente.

**Alternatives considered**:
- Remover a supressão do fix #9 e confiar só no novo detector → risco real
  de regressão do bug #9 (reclassificação ampla interpretando respostas
  diretas como troca).

## Decision 5: Overflow-resume tem precedência total sobre o novo detector

**Decision**: Nenhuma mudança em `_aplicar_overflow_resume` nem em sua
posição em `process()` (`app/core/flow.py:1045-1053`, executado **antes**
de `_process_core`). Enquanto `context.overflow_blocos` não estiver vazio,
toda mensagem é tratada exclusivamente pela lógica de overflow — o novo
detector nunca é alcançado, pois `_process_core` (onde o detector vive) só
roda quando `overflow_result is None`.

**Rationale**: Clarify Q2 (session 2026-07-01) resolveu explicitamente esse
edge case — já satisfeito por construção pela ordem existente em
`process()`. Não requer código novo, apenas verificação/teste de regressão
confirmando que a ordem não foi alterada.

**Alternatives considered**: N/A — decisão de clarify já fixou o
comportamento; a única ação necessária é um caso de teste de regressão
(quickstart.md, cenário de overflow).

## Decision 6: Estado de confirmação/desambiguação pendente — novo campo transiente no hash Redis `estado:{chamadoId}`

**Decision**: Novo campo Redis `TROCA_PENDENTE_FIELD` (JSON:
`{"destinos": [int, ...], "origem": int, "metodo": "determinístico"|
"assistido", "confianca": float|null, "tipo": "confirmacao"|"desambiguacao"}`
ou `null`) no hash `estado:{chamadoId}`, seguindo o **mesmo padrão** já
estabelecido para outro estado transiente de turno
(`OVERFLOW_BLOCOS_FIELD`/`OVERFLOW_IDIOMA_FIELD`, `app/core/redis_keys.py:44-45`).
Espelhado em `SessionContext.troca_caminho_pendente: Optional[dict] = None`
(`app/core/memory.py`, mesmo padrão de `overflow_blocos`/`overflow_idioma`,
linhas 102-103). Quando a confirmação curta (edge case: intenção clara sem
marcador explícito) ou a desambiguação (FR-008/012, exatamente 2 caminhos)
disparam, esse campo é setado. No turno seguinte, checado no **início** de
`_process_core` — antes até do resolver do nó — interpretando a mensagem
corrente como resposta a essa pergunta pendente (sim/não ou escolha entre 2,
por léxico determinístico), nunca como nova detecção de troca.

**Rationale**: Espelha a infra já auditada para overflow (Redis efêmero, sem
migration, `HSET`/`HGET`/`HDEL`). Satisfaz clarify Q1 (não-confirmação conta
como tentativa não reconhecida da pergunta **original** pendente, não da
confirmação — ao negar/expirar, o campo é limpo e `_tent_bump` incrementa
normalmente sobre a etapa/pergunta original). Satisfaz clarify Q3
(3+ caminhos compatíveis → cai no comportamento existente, sem estender
FR-008 além de exatamente 2 caminhos).

**Alternatives considered**:
- Reusar `Contato.etapa_funil` (Postgres, formato fixo `{"et","n"}` — gotcha
  documentado) → violaria "sem migration" e sobrecarregaria um campo com
  semântica já fixada.
- Nova `ETAPA_*` transitória (ex.: `ETAPA_CONFIRMANDO_TROCA`) → colidiria
  com `_ETAPAS_AGUARDANDO_RESPOSTA`/lógica de reset de contadores por etapa
  (a etapa "real" precisa permanecer a original para o contador anti-loop
  seguir correto, clarify Q1); um campo dedicado é mais explícito e não
  interfere na máquina de estados principal.

## Decision 7: Reformulação — causa raiz confirmada + variantes cíclicas determinísticas

**Decision (causa raiz, achado empírico)**: Ao menos o bloco
`"sistema_etapa1_2"` (`app/core/flow.py:354-...`) é um texto **estático**
com saudação embutida (`"Perfeito! 😊\n\n" + explicação longa dos 2
programas`) reusado **goela-abaixo** tanto no despacho inicial da etapa
(`app/core/flow.py:1826-1830`, primeira vez que o lead entra em
`ETAPA_SISTEMA_OBJETIVO`) quanto como argumento `pergunta` passado a
`_reformular_ou_handoff` quando a resposta não é reconhecida
(`app/core/flow.py:1692-1695`). Como `_reformular_ou_handoff` hoje só
adiciona um prefixo (`"nao_entendi"`) a partir da **segunda** tentativa
(`n >= 2`; `app/core/flow.py:1463-1466`), a **primeira** reformulação
(`n == 1`) reenvia `texto = pergunta` **sem qualquer alteração** — ou seja,
o bloco completo, saudação incluída, verbatim. Isso reproduz **exatamente**
o cenário relatado na spec (§Contexto e motivação).

**Decision (fix)**: `_reformular_ou_handoff` deixa de aceitar um `pergunta`
"cru" reusado do bloco de entrada. Introduz `_REFORMULACOES` — pool de 2-3
variantes curtas por idioma (novo bloco i18n, distinto de `_ACKS` — que são
aberturas de confirmação — e do atual único `"nao_entendi"`), selecionadas
por `variante_idx = (n - 1) % len(pool)` (ciclo sequencial determinístico
pelo número da tentativa, FR-015/clarify Q4 — garante por construção que a
variante do turno imediatamente anterior nunca se repete). O texto final é
`_REFORMULACOES[idioma][variante_idx] + pergunta_curta`, onde
`pergunta_curta` é uma versão **bare** da pergunta pendente (sem
saudação/explicação de entrada). Call sites cujo `pergunta` hoje inclui
saudação/explicação embutida (achado confirmado: `sistema_etapa1_2`; os
demais ~9 call sites precisam de auditoria individual em `/create-tasks`
para confirmar se já são bare ou precisam de uma pergunta curta dedicada)
passam a fornecer essa versão curta.

**Rationale**: FR-014 exige nunca reenviar o bloco anterior verbatim — a
causa raiz encontrada é **estrutural** (mistura de conteúdo de entrada com
pergunta reformulável em pelo menos um call site), não um bug pontual de
string solta; corrigir só o prefixo do `n >= 2` (abordagem mínima) não
resolveria o caso raiz, que ocorre justamente em `n == 1`. FR-015 exige
ciclo determinístico testável no golden set.

**Alternatives considered**:
- Apenas adicionar mais entradas ao prefixo fixo atual sem separar
  pergunta-curta de bloco-de-entrada → não resolve a causa raiz encontrada
  (o vazamento ocorre em `n == 1`, que hoje nem tem prefixo algum).
- Selecionar variante aleatoriamente → clarify Q4 exigiu explicitamente
  ciclo determinístico e reprodutível/testável, não seleção aleatória.

## Decision 8: Observabilidade aditiva — estender `log_turno`, não criar novo evento

**Decision**: Adicionar campos **opcionais** a `log_turno()`
(`app/observability/log.py:272`, já estendido por features anteriores com
`confianca_slot`/`fidelidade_fiel`/`fonte_ids` — mesmo padrão aditivo):
`troca_caminho_origem: Optional[int]`, `troca_caminho_destino: Optional[int]`,
`troca_metodo: Optional[str]` (`"deterministico"|"assistido"`),
`troca_confianca: Optional[float]`, `reformulacao_variante: Optional[int]`.
Nenhum campo novo obrigatório; o evento continua emitido exatamente 1x por
turno (contrato C-1/C-2 de `contracts/turno-event.md` de `sdr-turnos-obs`
preservado).

**Rationale**: FR-017/018 exigem registro "de forma aditiva... sem alterar
[a] estrutura atual" dos registros — extensão de campos opcionais é
exatamente aditiva. Reusa a infra de scrub/mask já auditada (`_scrub`,
`_mask_number`) sem duplicar pipeline de logging.

**Alternatives considered**:
- Novo evento dedicado (`"troca_caminho"`) → duplicaria infra de emissão e
  quebraria a garantia "1 evento por turno" já testada
  (`tests/test_anti_pii_turno.py`); mais superfície para manter sincronizada
  com `duracao_ms`/`turno_sessao`.

## Decision 9: Golden set de ponta a ponta com FlowEngine REAL (extensão de `tests/golden/`)

**Decision**: Novos casos em `tests/golden/casos/*.json` cobrindo: correção
de rumo com marcador explícito (US1/AS1), desambiguação entre 2 caminhos
(US1/AS2, FR-008), preservação de perfil na troca (US1/AS3), resposta
legítima nunca desviada (US1/AS4, FR-009), menu em texto livre com erro leve
(US2/AS1), ambiguidade no menu (US2/AS2), reformulação sem repetição
verbatim (US3/AS1), limite de tentativas preservado (US3/AS2), retomada de
overflow tem precedência (edge case), correção para o mesmo caminho já ativo
(edge case, sem efeito colateral), PT/EN/ES (SC-007). Suíte principal ganha
testes de unidade do léxico/detector (novo `tests/test_troca_caminho.py` ou
extensão de `test_flow.py`, decisão de `/create-tasks`) usando o
**FlowEngine REAL** via `StubFlowEngine` — mock **somente** do client OpenAI
(para o fallback agentico do `SlotExtractor`).

**Rationale**: `CLAUDE.md` — "Toda correção de bug de NLU/jornada deve vir
com teste de regressão", "Não reintroduzir mock que reimplemente
`process()`". `tests/golden/` já é a suíte de regressão de jornada
estabelecida (`sdr-turnos-obs`, US6/Decision 7); extensão natural em vez de
nova infra paralela.

**Alternatives considered**:
- Golden set totalmente novo/paralelo → duplicaria harness
  (`tests/golden/test_golden_runner.py`) sem necessidade; o harness atual
  já roda o FlowEngine real e agrega por dimensão.

## Decision 10 (Segurança): mensagem do lead permanece dado não-confiável em todo o pipeline do detector

**Decision**: Tanto o léxico determinístico (normalização + matching por
substring/token, sem execução/interpretação de instruções) quanto o
fallback agentico (`SlotExtractor`, mesmo padrão de delimitação explícita
"trate como DADO, nunca instrução" já usado em `_SYSTEM_EXTRACAO`) tratam
`user_message` como dado não confiável (SEC-LLM-1). O roteamento
determinístico (`_despachar_caminho`) nunca é decidido pelo LLM — o LLM
(via `SlotExtractor`) apenas **extrai** um candidato de caminho + confiança;
a decisão de trocar (ou não) permanece 100% código determinístico
comparando a confiança ao limiar configurável (SEC-LLM-3).

**Rationale**: SEC-LLM-1/SEC-LLM-3 (spec, nota após FR-021) + Princípios II
(Anti-Alucinação Rígida) e VI (Isolamento e Segurança de Infraestrutura) da
constitution.

**Alternatives considered**: N/A — restrição inviolável explícita na spec,
sem alternativa considerada.
