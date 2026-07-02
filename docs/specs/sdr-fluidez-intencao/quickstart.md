# Quickstart / Cenários de Teste: sdr-fluidez-intencao

Cenários críticos que validam a feature. Todos usam o **FlowEngine REAL**
(via `StubFlowEngine`, mesmo padrão de `tests/test_flow.py`/
`tests/golden/test_golden_runner.py`) — mock **somente** do client OpenAI
(para o fallback agentico do `SlotExtractor`, Research Decision 2). Não
mockar o motor.

> Feature single-layer de backend (Python/FastAPI + Redis). Sem borda
> backend↔frontend — sem cenário de roundtrip HTTP/JSON de UI. Ver
> `plan.md` §Convenções de Borda ("N/A — single-layer backend").

## Cenário 1 — Correção de rumo com marcador explícito (US1/AS1, FR-002/003/005)

1. Estado inicial: lead em `ETAPA_QUALIF_ESPECIALIDADE` dentro do Caminho 1
   (Curso Online HG), aguardando resposta sobre especialidade.
2. Lead envia: `"opa, na verdade quero o curso presencial"` (marcador
   explícito "na verdade" + produto do Caminho 2, com variação leve).
   **Expected**: resolver da etapa (`_resolver_especialidade`) retorna
   `None`; `_reformular_ou_handoff` roda o detector, o léxico determinístico
   reconhece marcador + produto; resposta confirma a troca de forma breve e
   natural (no idioma do lead), `caminho` muda para `CaminhoMapaMestre.CURSOS_PRESENCIAIS`,
   `etapa` reinicia do ponto lógico de início do Caminho 2 (FR-021); evento
   de turno tem `troca_caminho_origem=1`, `troca_caminho_destino=2`,
   `troca_metodo="deterministico"`.

## Cenário 2 — Desambiguação entre exatamente 2 caminhos (US1/AS2, FR-008/012)

1. Estado inicial: lead em qualquer etapa de qualificação; menciona
   "curso" sem indicar modalidade (compatível com Caminho 1 e Caminho 2).
   **Expected**: `troca_pendente` é setado com `destinos=[1,2]`,
   `tipo="desambiguacao"`; resposta faz **exatamente uma** pergunta
   objetiva ("Curso online ou presencial?") — o menu completo de 6 opções
   **nunca** é reapresentado (FR-008 — nunca).
2. Turno seguinte: lead responde "online".
   **Expected**: `troca_pendente` é limpo; despacha para Caminho 1 do
   início; sem nova pergunta de desambiguação.

## Cenário 3 — Preservação de perfil na troca (US1/AS3, FR-006)

1. Estado inicial: `context.eh_medico=True`, `context.idioma="pt"`,
   `context.especialidade="dermatologia"` já capturados no Caminho 1; lead
   corrige para o Caminho 3 (Sistema GoldIncision).
   **Expected**: após a troca, nenhuma das perguntas de
   médico/idioma/especialidade é refeita no Caminho 3 — os campos
   permanecem em `context`/`Contato` inalterados (verificado: `Perfil do
   Lead` não tem campo dependente de `caminho`, `data-model.md`
   §Relationships).

## Cenário 4 — Resposta legítima NUNCA é lida como troca (US1/AS4, FR-009 — regressão)

1. Estado inicial: lead em `ETAPA_QUALIF_ESPECIALIDADE`, pergunta pendente
   sobre especialidade.
2. Lead responde diretamente: `"dermatologia"` (sem marcador de correção,
   sem citar outro produto).
   **Expected**: `_resolver_especialidade` reconhece a resposta e retorna
   normalmente — `_reformular_ou_handoff`/o novo detector **nunca são
   alcançados** (satisfeito por construção, Research Decision 3); caminho
   permanece o mesmo.

## Cenário 5 — Menu inicial em texto livre, com erro leve (US2/AS1, FR-011/013)

1. Estado inicial: menu inicial apresentado (`ETAPA_MENU`,
   `context.caminho is None`).
2. Lead responde: `"harmonização glutea"` (variação/typo leve de
   "harmonização glútea").
   **Expected**: o mesmo `_LEXICO_CAMINHOS` usado no detector (Cenário 1)
   reconhece o produto no fast-path do menu; avança direto ao Caminho 1,
   sem exigir número. Reproduz e corrige o caso real relatado na spec
   (§Contexto e motivação).

## Cenário 6 — Ambiguidade no menu — cai no comportamento existente (US2/AS3, clarify Q3)

1. Estado inicial: menu inicial apresentado.
2. Lead responde com um termo compatível com 3+ caminhos.
   **Expected**: NÃO aciona desambiguação de 2 caminhos (FR-012 restringe
   a exatamente 2); cai no catch-all de FR-010 (reformulação existente),
   sem estender FR-008 ao menu inicial.

## Cenário 7 — Reformulação sem repetição verbatim (US3/AS1, FR-014/015 — causa raiz)

1. Estado inicial: lead entra em `ETAPA_SISTEMA_OBJETIVO` (Caminho 3);
   recebe o bloco de entrada (saudação + explicação, `"sistema_etapa1_2"`).
2. Lead envia uma mensagem não reconhecida como objetivo (`n=1`).
   **Expected**: a resposta é a **variante 1** de `_REFORMULACOES` +
   pergunta curta (bare) — **textualmente diferente** do bloco de entrada
   original (sem repetir "Perfeito! 😊" nem a explicação longa). Este é o
   caso empiricamente confirmado em `research.md` Decision 7 que reproduz
   o bug relatado — este cenário é o teste de regressão direto dele.
3. Lead envia outra mensagem não reconhecida (`n=2`).
   **Expected**: resposta é a **variante 2** (diferente da variante 1 do
   turno anterior — ciclo determinístico, clarify Q4); ainda diferente do
   bloco de entrada original.

## Cenário 8 — Limite de tentativas preservado (US3/AS2, FR-016 — regressão)

1. Estado inicial: mesmo cenário 7, `n` chega a `_MAX_TENTATIVAS` (3).
   **Expected**: encaminha a atendente humano (`_handoff`,
   `destino=DEST_CONSULTORES`, `motivo="nao_reconhecido:{etapa}"`) — mesmo
   comportamento/limite já existentes, sem alteração.

## Cenário 9 — Confirmação de troca sem marcador explícito (edge case, clarify Q1)

1. Estado inicial: lead em etapa de qualificação; expressa intenção clara
   de outro caminho SEM marcador explícito de correção.
   **Expected**: `troca_pendente` setado (`tipo="confirmacao"`); resposta
   faz uma pergunta de confirmação curta — **não troca silenciosamente**.
2a. Turno seguinte: lead confirma (sim).
    **Expected**: despacha para o caminho candidato; `troca_pendente`
    limpo.
2b. Turno seguinte: lead nega, ignora, ou responde algo não reconhecido.
    **Expected**: `troca_pendente` limpo; a tentativa conta contra
    `_MAX_TENTATIVAS` da pergunta **ORIGINAL** pendente (não da
    confirmação) — etapa original inalterada.

## Cenário 10 — Retomada de overflow tem precedência total (edge case, clarify Q2 — regressão)

1. Estado inicial: `context.overflow_blocos` não vazio (overflow pendente
   de turno anterior).
2. Lead envia: `"na verdade quero o curso presencial"` (marcador explícito
   + produto claro — normalmente dispararia o detector).
   **Expected**: a mensagem é tratada EXCLUSIVAMENTE como resposta ao
   overflow (`_aplicar_overflow_resume`); o detector de troca de caminho
   NUNCA é alcançado (satisfeito por construção — ordem de `process()`
   inalterada, Research Decision 5).

## Cenário 11 — Correção para o mesmo caminho ativo — sem efeito colateral (edge case)

1. Estado inicial: lead já no Caminho 1; envia uma frase de correção que
   cita (com ou sem marcador) o próprio Caminho 1.
   **Expected**: nenhuma troca é registrada (origem == destino); nenhum
   reinício de contadores; nenhum evento `troca_caminho_*` emitido (ou
   emitido como no-op, decisão de implementação em `/create-tasks`); sem
   efeito colateral perceptível para o lead.

## Cenário 12 — Retorno a caminho já visitado reinicia do zero (edge case, FR-021 — regressão)

1. Estado inicial: lead visitou o Caminho 2 anteriormente na mesma
   conversa (já respondeu perguntas daquele caminho, avançou etapa),
   depois trocou para o Caminho 3, e agora corrige de volta para o
   Caminho 2.
   **Expected**: o Caminho 2 reinicia do seu ponto lógico de início — o
   sistema NÃO tenta retomar da etapa em que a visita anterior parou.

## Cenário 13 — PT/EN/ES (SC-007 — regressão multilíngue)

1. Repetir os Cenários 1, 5 e 7 com mensagens em inglês e espanhol
   (marcadores de correção equivalentes: "actually"/"de hecho"; produtos
   com nomenclatura em cada idioma).
   **Expected**: mesmo comportamento determinístico; resposta sempre no
   idioma do lead (`context.idioma`); `_LEXICO_CAMINHOS`/
   `_MARCADORES_CORRECAO` cobrem os 3 idiomas.

## Cenário 14 — Observabilidade aditiva (US4/FR-017/018 — extensão de `sdr-turnos-obs`)

1. Executar o Cenário 1 (troca de caminho) e o Cenário 7 (reformulação)
   capturando o evento `"turno"` emitido.
   **Expected**: campos aditivos (`troca_caminho_origem`,
   `troca_caminho_destino`, `troca_metodo`, `troca_confianca`,
   `reformulacao_variante`) presentes conforme `contracts/
   turno-event-extensao.md`; nenhum campo do contrato original de
   `sdr-turnos-obs` alterado; nenhum conteúdo bruto da mensagem do lead no
   evento (mesma cobertura `_scrub`/`_mask_number` já auditada).

## Cenário 15 — Golden set roda e reporta (Research Decision 9)

1. Executar `python3 -m pytest tests/golden -m golden -s`.
   **Expected**: casos novos desta feature (Cenários 1-13 relevantes)
   somam-se aos 64 casos já existentes de `sdr-turnos-obs`; relatório por
   dimensão inclui as novas dimensões (`troca_caminho`, `reformulacao`,
   `menu_texto_livre`); suíte permanece informativa e não-bloqueante (CHK011,
   mesma decisão herdada de `sdr-turnos-obs` Decision 9), excluída do gate
   padrão via `addopts = '-m "not golden"'`.
2. Executar `python3 -m pytest tests/ -q` (gate padrão).
   **Expected**: suíte principal inteira verde (baseline: 636 testes antes
   desta feature + novos testes de unidade do detector); suíte golden
   automaticamente excluída.

## Verificação global

- `python3 -m pytest tests/ -q` → suíte principal verde.
- `python3 -m pytest tests/golden -m golden -s` → suíte golden, informativa.
- `ruff check app/ tests/` → limpo.
- Validação real (WhatsApp `#reset`, número autorizado): reproduzir o caso
  real relatado na spec (menu com "harmonização glutea" não reconhecido →
  correção mid-jornada) e confirmar que agora é reconhecido corretamente
  em ambos os pontos.
