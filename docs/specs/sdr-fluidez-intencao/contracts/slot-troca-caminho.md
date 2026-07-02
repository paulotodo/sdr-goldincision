# Contract: Slot Schema de Troca de Caminho (fallback agentico)

Interface interna: novo `slot_schema` consumido por `SlotExtractor.extract()`
já existente (`app/core/interpret.py`). Não é endpoint HTTP — é o contrato
de prompt+Structured-Output usado pelo fallback confidence-gated de FR-004
(Research Decision 2). Reusa `SlotQualificacao` (mesmo shape de
`valor: Optional[str]` + `confianca: float`), sem novo modelo Pydantic.

## `slot_schema` proposto

```python
_SLOT_SCHEMA_TROCA_CAMINHO = {
    "nome": "troca_caminho",
    "descricao": (
        "O lead está corrigindo o rumo da conversa e indicando que quer "
        "outro caminho/produto diferente do caminho ativo. Extraia qual "
        "caminho ele quer, se identificável."
    ),
    "valores_esperados": [
        "curso_online", "cursos_presenciais", "sistema_goldincision",
        "aluno_suporte", "paciente_modelo", "outro_assunto",
    ],
}
```

## Invocação

```python
slot = await self._slot_extractor.extract(
    _SLOT_SCHEMA_TROCA_CAMINHO, user_message, _perfil_conhecido(context),
)
if SlotExtractor.aceitar(slot, settings.intent_switch_confidence_threshold):
    destino = _valor_para_caminho(slot.valor)  # mapeia string -> CaminhoMapaMestre
```

## Payload de saída (exemplo — `SlotQualificacao`, Structured Output)

```json
{
  "valor": "curso_online",
  "confianca": 0.82
}
```

```json
{
  "valor": null,
  "confianca": 0.0
}
```

## Invariantes do contrato

- **S-1 (FR-004)**: só aceito quando `slot.valor is not None AND
  slot.confianca >= settings.intent_switch_confidence_threshold`
  (`SlotExtractor.aceitar`, já existente — sem lógica de aceitação nova).
- **S-2 (FR-004, env)**: `intent_switch_confidence_threshold` é
  configurável via `INTENT_SWITCH_CONFIDENCE_THRESHOLD`, default `0.6`
  (pydantic-settings, `app/config.py`).
- **S-3 (Fail-safe, herdado de `SlotExtractor`)**: qualquer erro de
  parsing/indisponibilidade do LLM retorna `SlotQualificacao(valor=None,
  confianca=0.0)` — nunca propaga exceção; equivalente a "não reconhecido",
  cai no comportamento existente de reformulação (FR-010).
- **S-4 (SEC-LLM-1/SEC-LLM-3)**: o LLM apenas **extrai** o candidato; a
  decisão de trocar (comparar confiança ao limiar, despachar) é 100% código
  determinístico no `FlowEngine`, nunca decidida pelo modelo.
- **S-5 (Só chamado após léxico falhar, FR-002/Decision 3)**: este slot
  schema só é invocado quando `_LEXICO_CAMINHOS`/`_MARCADORES_CORRECAO`
  (reconhecimento determinístico) não encontraram correspondência —
  mesmo padrão de todos os resolvers existentes (`_resolver_especialidade`,
  `_resolver_objetivo_sistema`, etc: fast-path primeiro, LLM só como
  fallback).
- **S-6 (Fail-safe contra valor fora do enum — achado do gate owasp-security)**:
  `SlotQualificacao.valor` é `Optional[str]` **sem** constraint de enum no
  nível do Pydantic (`app/core/interpret.py:32-34` — o campo é string livre;
  `valores_esperados` só orienta o PROMPT, não é validado estruturalmente
  pelo Structured Output). Logo, `slot.valor` PODE, em tese, conter um
  valor fora dos 6 esperados (alucinação, erro de formatação do modelo).
  `_valor_para_caminho(slot.valor)` DEVE ser uma função pura de mapeamento
  fechado (`dict.get(valor, None)` ou `match` exaustivo com `case _: return
  None`) que retorna `None` para qualquer string não reconhecida — **nunca**
  levanta exceção, nunca adivinha o caminho mais "parecido". Um `None` aqui
  é equivalente a `SlotExtractor.aceitar()` ter rejeitado (cai no
  comportamento existente de reformulação, FR-010), mesmo que
  `slot.confianca` tenha passado o limiar. Esta função pura (sem chamada de
  rede) é distinta e adicional a `SlotExtractor.aceitar()` — cobre o caso em
  que a CONFIANÇA é alta mas o VALOR em si é inválido/inesperado.
- **S-7 (Custo/LLM10 — já coberto por `_MAX_TENTATIVAS` existente)**: o
  fallback agentico só é alcançável a partir de `_reformular_ou_handoff`,
  que já é gated por `_MAX_TENTATIVAS=3` (após 3 tentativas sem
  reconhecimento na mesma etapa, encaminha a humano — `app/core/flow.py:1456`).
  Isso limita a no máximo 3 chamadas ao fallback agentico por etapa-visita
  por lead malicioso tentando forçar consumo repetido, sem necessidade de
  rate-limit adicional dedicado a este slot schema.
