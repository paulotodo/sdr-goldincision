# Plano — Camada de interpretação agêntica das respostas do lead

> Para executar numa sessão limpa. Origem: feedback do operador (2026-06-30) —
> "siga o script, mas as respostas do lead têm que ser interpretadas de maneira
> agêntica e o entendimento tem que ser fluido". Projeto em `/root/sdr-goldincision`.
> Stack em produção: `sdr-whatsapp_app`, Docker Swarm.

## 1. Objetivo

Manter o **script determinístico** (Mapa Mestre: caminhos/etapas/handoffs/regras
anti-alucinação) como **trilho**, mas tornar o **entendimento da resposta do lead
fluido e robusto** em cada etapa — interpretando intenção/conteúdo de forma agêntica
em vez de depender de regex frágil ou de um classificador global de confiança baixa.

> Princípio: a **decisão de fluxo continua determinística**; só a **extração do que o
> lead quis dizer** passa a ser assistida por LLM, com fallback seguro. O LLM nunca
> inventa preço/política nem escolhe destino de handoff (SEC-LLM-3 permanece).

## 2. Estado atual (auditoria, com refs)

- **Menu principal**: já corrigido (PR #10) — opção numérica determinística
  (`_opcao_numerica`) com prioridade sobre o LLM (`flow.py`, bloco "2.bis Menu").
- **Classificador de intenção** (`app/core/intent.py`): LLM classifica a mensagem
  em 6 intenções + idioma + confiança. Confiança baixa é rebaixada a `ambigua`
  (visto nos logs: "rebaixando OUTRO_ASSUNTO → ambigua"). Bom para frases, fraco
  para respostas curtas/ambíguas dentro de uma etapa.
- **Detectores por etapa** (`flow.py`, regex/keywords): `_detectar_confirmacao`
  (sim/não → médico), `_detectar_objetivo_sistema`, `_detectar_experiencia_corporal`,
  `_detectar_especialidade`, `_detectar_escolha_turma`, `_detectar_fechamento`,
  `_detectar_medico_investidor`, `_sem_mais_duvidas`. São **rígidos**: dependem de
  palavras-chave previstas; frases naturais ("ah sim, atuo com estética corporal há
  anos") podem não casar, e o lead cai em "não entendi → reformula".
- **Responder** (`app/core/responder.py`): já recebe `known_facts` (perfil) e gera as
  respostas de dúvidas com grounding. Não participa da **extração de slots** das etapas.

**Diagnóstico**: a rigidez vem de cada etapa esperar um formato específico de resposta.
O lead responde em linguagem natural; o detector regex não cobre as variações.

## 3. Abordagem proposta — "slot-filling agêntico por etapa"

Para cada etapa que hoje usa um detector regex, criar um **interpretador híbrido**:

1. **Fast-path determinístico** (mantido): o regex/keyword atual roda primeiro. Se
   casar com alta certeza (ex.: "sim", número de opção), decide na hora — barato, 0 LLM.
2. **Fallback agêntico (LLM estruturado)**: se o fast-path não resolver, chamar um
   **extrator de slot** via LLM com **saída estruturada** (JSON), específico da etapa:
   - Entrada: a mensagem do lead + o que se espera naquela etapa + contexto/perfil.
   - Saída validada (ex.: `{"eh_medico": true|false|null, "confianca": 0..1}` ou
     `{"objetivo": "incorporar|abrir|nao_sei|null"}`, `{"experiencia_corporal": ...}`,
     `{"especialidade": "<str>|null"}`, `{"escolha_turma": "sp|barcelona|null"}`).
   - Decisão determinística sobre o slot: se `confianca >= limiar` → preenche e segue
     o script; senão → reformula (mas agora reformula menos, pois o LLM cobre as
     variações naturais).
3. **Guardas mantidas**: elegibilidade (só médicos), verbatim das apresentações,
   destino de handoff sempre da allowlist/config (LLM nunca fornece queueId), 1
   pergunta por mensagem, anti-rajada/pacing.

### 3.1 Componente novo — `SlotExtractor` (`app/core/interpret.py`)
- Método genérico `extract(slot_schema, user_message, contexto) -> dict` que monta um
  prompt curto e força `response_format=json_schema` (ou tool-call) no modelo barato
  (`openai_model_cheap`), retornando o slot validado (Pydantic) + confiança.
- Reusa o `OpenAIClient` já existente; idioma-aware; trata a mensagem como dado
  não-confiável (SEC-LLM-1).
- Cache/curto-circuito: se o fast-path resolveu, NÃO chama o LLM (custo/latência).

### 3.2 Integração no `flow.py`
- Introduzir um helper por etapa, ex. `_resolver_eh_medico(context, msg)`,
  `_resolver_objetivo_sistema(...)`, etc., que encapsula: fast-path → fallback agêntico
  → decisão. Os handlers de caminho passam a chamar esses resolvers no lugar dos
  `_detectar_*` diretos.
- Manter o número/letra de opção sempre como fast-path (determinístico e barato).

### 3.3 Reaproveitar o que já existe
- O **perfil** (`_perfil_conhecido`) e o histórico entram no contexto do extrator,
  para desambiguar ("já é médico" evita re-perguntar; "mencionou São Paulo antes").
- O **classificador de intenção** continua para troca de caminho macro; o slot-filling
  agêntico cobre o entendimento DENTRO da etapa.

## 4. Faseamento

- **Fase A (alto impacto, baixo risco)** — etapas de qualificação mais sensíveis:
  `eh_medico` (sim/não), `objetivo_sistema` (incorporar/abrir/não sei),
  `experiencia_corporal`. São as que mais geram "não entendi". Fast-path + fallback LLM.
- **Fase B** — `especialidade`, `escolha_turma`, `fechamento`/`sem_mais_duvidas`.
- **Fase C** — afinar limiares de confiança, telemetria (logar quando o LLM "salvou"
  uma resposta que o regex não pegou) e reduzir reformulações.

## 5. Riscos e mitigação
- **Latência/custo**: fallback só quando o fast-path falha; modelo barato; saída curta.
- **Alucinação de slot**: saída estruturada + validação Pydantic + limiar de confiança;
  slot inválido → trata como "não entendido" (reformula), nunca inventa.
- **Segurança**: destino de handoff e queueId continuam da config (SEC-LLM-3); mensagem
  do lead tratada como dado (SEC-LLM-1); apresentações verbatim inalteradas (FR-010).
- **Regressão de fluxo**: o script/máquina de estados não muda; só a extração do slot.
  Cobertura por testes com fast-path E fallback mockado.

## 6. Testes
- Fast-path: respostas óbvias ("sim", "2") não chamam o LLM (assert no mock).
- Fallback: frases naturais que o regex NÃO pega ("atuo com corporal há anos" →
  `experiencia_corporal=true`) preenchem o slot via extrator mockado.
- Confiança baixa → reformula (não preenche slot errado).
- Segurança: extrator nunca decide destino de handoff; elegibilidade preservada.
- Suíte verde + `ruff` limpo.

## 7. Validação real (WhatsApp, `#reset`)
- Responder as etapas com **linguagem natural variada** (não só "sim"/números) e
  confirmar que o agente entende sem cair em "não entendi".
- Conferir que continua seguindo o script (qualifica, conduz, faz handoff correto) e
  que a conversa está fluida (sem repetição — já entregue em 2.0.0).
- Repetir 1 caminho em EN/ES.

## 8. Critérios de aceite
- Respostas naturais do lead são entendidas na maioria das etapas sem reformular.
- O script determinístico e as guardas de segurança permanecem intactos.
- Latência aceitável (fallback LLM só quando necessário).
- Suíte verde + lint limpo + validação real confirmada.

## 9. Pendências / decisões
- **Limiar de confiança** por etapa (começar conservador, ~0.6, e ajustar com telemetria).
- **Modelo do extrator**: `openai_model_cheap` (recomendado) vs. o de raciocínio.
- **Escopo da Fase A**: confirmar quais etapas priorizar (sugestão: eh_medico,
  objetivo_sistema, experiencia_corporal).
</content>
</invoke>
