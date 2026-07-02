# Prompt /feature-00c — Fluidez agêntica de intenção (troca de caminho + menu robusto + reformulação humanizada)

> **Uso:** o bloco "PROMPT PARA /feature-00c" abaixo é autocontido e pronto para colar
> numa **sessão limpa** invocando `/feature-00c`. Baseado na skill
> `agente-atendimento-confiavel` (máquina de estados fora do prompt + interpretação
> agêntica + golden set) e em diagnóstico **verificado em código** (refs arquivo:linha).
> O projeto vive em `/root/sdr-goldincision`.

---

## Contexto (estado do sistema no momento deste plano)

- Produção: app `registry.todo-tips.com/sdr-whatsapp:2.1.2` + `pgvector/pgvector:pg16`
  (stack `sdr-whatsapp`). `master` HEAD `225b844`.
- Já entregues (NÃO refazer): Onda 1 (controle de turnos + `log_turno`), Onda 2 (contrato
  JSON `RespostaEstruturada` + `FidelityGate` + `SlotExtractor`), Onda 3 (RAG híbrido +
  abstenção), PR #16 (overflow resumível "pode continuar" + glue conversacional), PR #17
  (seed idempotente por `conteudo_hash`).
- Golden set em `tests/golden/` (`@pytest.mark.golden`, fora do CI padrão). Suíte: 636
  passed + golden 64 + `ruff check .` limpo.

## Bug reportado (transcript real da validação em produção, 01/07/2026)

```
[Goldincision] menu 1-6 ...
[Lead] 3
[Goldincision] (caminho 3) "...Licenciamento ... Franquia ... Qual destas opções
               representa melhor o seu objetivo? 1/2/3"
[Lead] opa... na verdade quero o curso de harmoização glutea
[Goldincision] (REPETIU O MESMO BLOCO VERBATIM, incl. "Perfeito! 😊")
```

Além disso: no menu inicial, digitar "harmonização glutea" (com/sem typo) por várias
vezes **não** foi reconhecido (o lead só progrediu digitando "3").

## Diagnóstico verificado (refs em `app/core/flow.py` / `app/core/intent.py`)

1. **Troca de caminho existe mas está suprimida em quase toda a jornada.**
   `IntentClassifier.classify` roda a cada turno (`flow.py:1250`) e há "troca de caminho
   conservadora" (`flow.py:1300-1323`) — porém ela é bloqueada quando
   `context.etapa ∈ _ETAPAS_AGUARDANDO_RESPOSTA` (`flow.py:168-180`, o "fix #9", que
   evita reiniciar a jornada quando o lead responde a uma pergunta). Como **11 etapas**
   (praticamente todo nó interativo: qualif_medico, qualif_experiencia, especialidade,
   escolha_turma, fechamento, link, sistema_objetivo, sistema_licenciamento,
   sistema_franquia, sistema_diagnostico, aluno_menu) estão nessa lista, uma correção
   explícita do lead ("opa, na verdade quero o curso…") **nunca troca de caminho**.
2. **Não-reconhecido = repetir o bloco.** Quando o resolver do nó não entende,
   `_reformular_ou_handoff` (`flow.py:1448`) re-envia a MESMA `pergunta` (na 1ª
   tentativa, idêntica; na 2ª, prefixa "Desculpe, acho que não entendi bem."). No
   transcript o handler do nó reexecutou por inteiro (re-enviou até o "Perfeito! 😊") —
   experiência de bot, não de atendente.
3. **Menu inicial depende só do LLM para texto livre.** `flow.py:1283-1298` tem
   fast-path determinístico apenas para NÚMERO ("3", "3️⃣", "tres"). Texto livre vai ao
   `IntentClassifier` (gpt-4o-mini); com typo ("harmoização") ou frase incomum a
   confiança cai → `AMBIGUA` → re-exibe o menu (`flow.py:1328-1339`), sem tentativa de
   casamento por palavra-chave nem pergunta de desambiguação (ex.: online vs presencial).

**Princípio da correção** (skill agente-atendimento-confiavel): a trilha continua
determinística (máquina de estados manda), mas a **interpretação** do que o lead disse
fica agêntica — o nó primeiro tenta entender a resposta esperada; se não casa, antes de
"não entendi", pergunta-se "isso é uma NOVA intenção?"; só depois reformula (variando o
texto). Correção explícita troca de trilha com um ack humano.

---

## PROMPT PARA /feature-00c (colar numa sessão limpa)

> short-name sugerido: `sdr-fluidez-intencao`

```
Feature: Fluidez agêntica de intenção para o agente SDR GoldIncision — troca de caminho
mid-jornada, menu robusto a texto livre/typo e reformulação humanizada. Projeto em
/root/sdr-goldincision. A trilha (Mapa Mestre) continua DETERMINÍSTICA; o que muda é a
INTERPRETAÇÃO das mensagens do lead, seguindo a skill agente-atendimento-confiavel
(máquina fora do prompt + interpretação agêntica + golden set). LEIA CLAUDE.md,
docs/constitution.md e o código atual de app/core/flow.py (refs abaixo) antes de planejar.

BUG REAL QUE MOTIVA A FEATURE (reproduzir como caso de regressão): no menu, digitar
"harmonização glutea" não era reconhecido; dentro do caminho 3 (Sistema GoldIncision,
etapa sistema_objetivo), a mensagem "opa... na verdade quero o curso de harmoização
glutea" fez o agente REPETIR o mesmo bloco verbatim (incl. "Perfeito! 😊") em vez de
trocar para o curso. Causas verificadas: (a) a troca de caminho por intenção
(flow.py:1300-1323) é suprimida quando etapa ∈ _ETAPAS_AGUARDANDO_RESPOSTA
(flow.py:168-180, "fix #9"), que cobre praticamente todos os nós; (b)
_reformular_ou_handoff (flow.py:1448) repete a mesma pergunta; (c) o menu só tem
fast-path para números (flow.py:1283-1298) — texto livre depende do IntentClassifier e
typo vira AMBIGUA → menu de novo.

OBJETIVO: o lead pode mudar de ideia em QUALQUER ponto da jornada e ser levado à trilha
certa com um reconhecimento natural; o menu entende texto livre com typos; e o agente
nunca repete o mesmo bloco verbatim quando não entende. Tudo SEM dar poder de decisão de
fluxo ao LLM (roteamento continua no código) e SEM quebrar o fix #9 (resposta legítima a
uma pergunta jamais troca de caminho).

REQUISITOS FUNCIONAIS:
1. ORDEM DO TURNO num nó de pergunta (o coração da correção — implementar como pipeline
   determinístico no FlowEngine):
   (a) resolver do nó PRIMEIRO (fast-path + SlotExtractor já existentes): se a resposta
       é reconhecida como resposta à pergunta, segue o fluxo normal — isso PRESERVA o
       fix #9 por construção (resposta legítima nunca chega ao detector de troca);
   (b) se o resolver NÃO reconheceu: rodar o DETECTOR DE TROCA DE INTENÇÃO:
       - fast-path determinístico (ZERO LLM): marcadores de correção ("na verdade",
         "quero o curso", "prefiro", "me enganei", "não é isso", "voltar", "menu",
         "mudei de ideia" + equivalentes EN/ES) e nomes de produto/caminho normalizados
         (sem acento, case-insensitive, tolerantes a typo leve por
         normalização+substring: "harmonizacao glutea", "harmoização", "curso online",
         "presencial", "licenciamento", "franquia", "paciente modelo", "sou aluno");
       - fallback: IntentClassifier já existente (gpt-4o-mini, app/core/intent.py) sobre
         a mensagem; aceitar só com confiança ≥ limiar (env, default 0.6);
   (c) intenção clara e ≠ caminho atual → TROCAR: ack humanizado curto no idioma do lead
       ("Claro! Vamos falar do curso de harmonização glútea então 😊") + entrar no novo
       caminho PRESERVANDO o perfil/slots já conhecidos (não re-perguntar eh_medico,
       idioma, especialidade — reusar _perfil_conhecido); se a intenção for plausível
       mas ambígua entre caminhos (ex.: "harmonização glútea" sem online/presencial) →
       1 pergunta de desambiguação (Regra: 1 pergunta por mensagem), NUNCA o menu de 6;
   (d) só se (b)/(c) não resolverem → _reformular_ou_handoff (mantendo
       _MAX_TENTATIVAS=3 e o handoff no teto).
2. MENU ROBUSTO a texto livre: adicionar fast-path determinístico por palavras-chave
   normalizadas (mesmo léxico do detector do RF-1) ANTES do IntentClassifier; "curso"
   ambíguo entre online/presencial → pergunta de desambiguação direta em vez de
   re-exibir o menu; typo leve não pode prender o lead no menu.
3. REFORMULAÇÃO HUMANIZADA: nunca re-enviar o MESMO bloco verbatim em turnos
   consecutivos. Na 1ª falha, reconhecer + reformular só a PERGUNTA (não reexecutar a
   introdução do nó — nada de repetir "Perfeito! 😊"); usar 2-3 variantes i18n curtas de
   reformulação (PT/EN/ES) que citem brevemente o que o lead disse quando possível.
   Manter o contador anti-loop e o handoff em _MAX_TENTATIVAS=3 intactos.
4. ACK DE TRANSIÇÃO + CONTINUIDADE: ao trocar de caminho, zerar tentativas/contadores
   por-nó do caminho anterior (coexistindo com o orçamento de turnos da Onda 1, que NÃO
   zera turnos_sessao), registrar o caminho anterior no estado (Redis, sem migration) e
   entrar no novo caminho pulando o que o perfil já responde. Default: entrar no início
   lógico do caminho novo (não tentar retomar etapa antiga do caminho anterior — fica
   como evolução futura).
5. OBSERVABILIDADE (aditiva, sem quebrar o schema do log_turno das Ondas 1/2/3):
   registrar troca_caminho {de, para, gatilho: fastpath|llm, confianca} e
   reformulacao_variante — base para calibrar o limiar e medir quantas trocas o
   detector "salvou".
6. GOLDEN SET (regressão obrigatória): (i) o transcript real deste relato — menu → "3"
   → "opa... na verdade quero o curso de harmoização glutea" → deve trocar para o curso
   (ou desambiguar online/presencial), NUNCA repetir o bloco do caminho 3; (ii)
   "harmonização glutea" (e com typo "harmoização") direto no menu → entra/desambigua;
   (iii) ANTI-REGRESSÃO do fix #9: em qualif_medico, responder "sou médico" NÃO troca de
   caminho; em sistema_objetivo, responder "1" segue normal; (iv) variantes EN/ES dos
   casos i-ii.

NÃO-OBJETIVOS: reescrever a jornada/Mapa Mestre; retomar a etapa antiga ao voltar para
um caminho já visitado (evolução futura); mudar RAG/portão/contrato JSON; humanizar
copies dos blocos verbatim (continuam saindo do DB).

DECISÕES/DEFAULTS (o clarify pode confirmar): limiar de confiança do detector = 0.6
(env, ex.: INTENT_SWITCH_CONFIDENCE_THRESHOLD); correção explícita (marcador de correção
+ produto claro) troca direto sem confirmação; intenção clara SEM marcador de correção,
num nó aguardando resposta → 1 pergunta de confirmação curta ("Você prefere que eu te
apresente o curso de harmonização glútea? 😊") em vez de troca silenciosa; estado extra
em Redis (sem migration); léxico de produtos/caminhos como constante testável no código
(não no prompt do LLM).

RESTRIÇÕES INVIOLÁVEIS: máquina de estados determinística (LLM nunca decide destino de
handoff/queueId — allowlist SEC-LLM-3); mensagem do lead = dado não-confiável
(SEC-LLM-1); apresentações/preços/links verbatim do DB intactos; anti-alucinação e
abstenção do RAG para dúvidas factuais intactas; 1 pergunta por mensagem (exceto menus);
idioma PT/EN/ES; PRESERVAR integralmente Ondas 1/2/3 e fixes #16 (overflow resume — o
detector de troca NÃO pode capturar "pode continuar" durante overflow pendente; overflow
resume roda ANTES) e #17 (seed idempotente); anti-loop _MAX_TENTATIVAS=3 não fundido com
contadores de turno; teto max_msgs_per_turn=4; _Pacer+429; idempotência; lock; gate
IA=77; debounce 8s. Fast-path determinístico SEMPRE antes de LLM (custo/latência);
gpt-4o-mini para classificação. Testes com FlowEngine REAL (mock só do client OpenAI,
nunca o motor); toda mudança com teste de regressão; lição das entregas anteriores: os
casos novos de golden set devem rodar o motor real de ponta a ponta (não mocks de
sequência). Suíte inteira verde + ruff check . limpo; envs novos em config + stack.yml +
.env.example. Entrega por PR (master protegido, CI obrigatório) — NÃO mergear; deploy é
do operador (build-push.sh + service update, só a stack sdr-whatsapp).

CRITÉRIOS DE ACEITE: o transcript reportado passa com troca fluida (ack + caminho do
curso ou desambiguação online/presencial); texto livre com typo no menu não prende o
lead; resposta legítima a pergunta continua NÃO trocando caminho (fix #9 verificado por
teste); nenhum bloco repetido verbatim em turnos consecutivos; perfil preservado na
troca (não re-pergunta o que já sabe); golden set estendido verde; suíte + ruff verdes;
validação real WhatsApp (#reset) em PT + 1 caso EN/ES cobrindo: menu texto livre, troca
mid-jornada, e fix #9.
```

---

## Notas de engenharia (para o plan da feature)

- **Ponto de inserção**: o detector de troca (RF-1b/c) deve viver no despacho central
  (`_process_core`/`_despachar_caminho`) ou como helper chamado pelos handlers no lugar
  do `_reformular_ou_handoff` direto — decisão de design do plan. O contrato é: resolver
  do nó → detector de troca → reformulação. O overflow-resume (#16) permanece ANTES de
  tudo em `process()`.
- **Léxico compartilhado**: menu (RF-2) e detector (RF-1b) devem usar a MESMA constante
  de keywords/produtos normalizados (fonte única, testável unitariamente).
- **fix #9 por construção**: a supressão `_ETAPAS_AGUARDANDO_RESPOSTA` pode ser mantida
  no passo de classificação global (flow.py:1300) — a troca passa a acontecer no novo
  detector, que só roda APÓS o resolver do nó falhar; assim "sou médico" (reconhecido
  pelo resolver) nunca chega ao detector.
- **Telemetria primeiro**: usar log_turno para medir taxa de troca fastpath vs llm e
  falsos positivos antes de afrouxar o limiar.
- **Rollout**: merge por PR → deploy 2.2.0 pelo operador → validação WhatsApp com o
  transcript real como roteiro.
