# Data Model — Contrato JSON, Portão de Fidelidade e Interpretação Agêntica (Onda 2)

Feature: `sdr-fidelidade-json` · Stack: Python 3.12 · Pydantic v2 (já em uso via
pydantic-settings). Todos os schemas são **modelos de dados em memória** (não há
tabela nova; Postgres/Redis inalterados). Cada schema mapeia uma Key Entity da
`spec.md`.

Princípios transversais aplicados a TODOS os schemas:
- A mensagem do lead é dado não-confiável (SEC-LLM-1): nenhum campo textual do lead é
  interpretado como instrução.
- O modelo informa, não decide (FR-006): nenhum schema carrega destino de handoff,
  transição de estado ou queueId — apenas `precisa_handoff: bool`.
- Validação estrita: `model_config = ConfigDict(extra="forbid")`; pacote que não valida
  == falha de geração (FR-002), não resposta válida.

---

## 1. `RespostaEstruturada` — Pacote de Resposta Estruturada (Pilar 6, FR-001..FR-007)

Produzido por `GroundedResponder.generate()` via `response_format=json_schema`
(gpt-4o, temp 0–0.2 em fatos). Arquivo proposto: `app/core/contracts.py`.

```python
from pydantic import BaseModel, ConfigDict, Field

class RespostaEstruturada(BaseModel):
    """O que o modelo entendeu e redigiu para uma resposta de duvida.
    NAO decide o proximo passo do atendimento (FR-006)."""
    model_config = ConfigDict(extra="forbid")

    texto: str = Field(..., min_length=1)          # FR-001: texto da resposta
    fontes: list[str] = Field(default_factory=list) # FR-001/FR-007: base/fontes usadas
    precisa_handoff: bool = False                   # FR-001: indica necessidade de humano
    confianca: float = Field(..., ge=0.0, le=1.0)   # FR-001: grau de confianca
    idioma: str = Field(..., pattern="^(pt|en|es)$") # FR-005: idioma da resposta
```

- **FR-002/FR-003**: se o JSON não validar contra este schema, `generate()` faz **1**
  retry de nova geração; falhando de novo → retorna `(handoff=True)` (nunca texto
  improvisado).
- **FR-005**: `idioma` DEVE bater com o idioma já identificado da conversa; divergência
  == pacote inválido.
- **Adapter de compatibilidade**: `generate()` continua devolvendo a 2-tupla
  `(texto: str, handoff: bool)` esperada por `flow.py:1403`; `precisa_handoff` do
  pacote alimenta o `bool` da tupla. A máquina de estados nunca vê o objeto.

---

## 2. `VeredictoFidelidade` — Veredito de Fidelidade (Pilar 7, FR-008..FR-012)

Produzido por `FidelityGate.verificar()` (gpt-4o-mini). Arquivo proposto:
`app/core/fidelity.py`.

```python
class VeredictoFidelidade(BaseModel):
    """Resultado da verificacao que antecede o envio de uma resposta gerada
    sensivel (preco/data/elegibilidade/condicao comercial)."""
    model_config = ConfigDict(extra="forbid")

    fiel: bool                                       # FR-009: sustentado pela base oficial?
    afirmacoes_nao_sustentadas: list[str] = Field(default_factory=list)  # FR-010
```

- **Fail-closed**: qualquer erro de parsing/indisponibilidade/timeout
  (`VERIFY_TIMEOUT_SECONDS=3`) → o gate retorna o equivalente a
  `VeredictoFidelidade(fiel=False, afirmacoes_nao_sustentadas=["<indisponivel>"])`
  (Edge Case da spec: erro == reprovação).
- **Invariante**: `fiel=True` só quando `afirmacoes_nao_sustentadas == []`.
- **Gatilho (Decision 5 / dec-010)**: o gate só é chamado quando a resposta toca
  condição comercial (preço/valor, parcelamento, desconto/promoção, data/prazo,
  disponibilidade de turma/vaga, elegibilidade médica). Verbatim/rapport nunca passam.

---

## 3. `SlotQualificacao` + `slot_schemas` — Slot de Qualificação (FR-013..FR-017)

Produzido por `SlotExtractor.extract()` (gpt-4o-mini, `response_format=json_schema`).
Arquivo proposto: `app/core/interpret.py`. Só é acionado quando o fast-path
determinístico não resolve (FR-013).

```python
from typing import Optional

class SlotQualificacao(BaseModel):
    """Uma informacao especifica capturada numa etapa + grau de confianca."""
    model_config = ConfigDict(extra="forbid")

    valor: Optional[str] = None                      # valor extraido (None se nao entendido)
    confianca: float = Field(..., ge=0.0, le=1.0)    # FR-015: comparado a SLOT_CONFIDENCE_THRESHOLD
```

Regra de aceitação (FR-015): `valor is not None and confianca >= settings.slot_confidence_threshold`
(default 0.6). Caso contrário → "não entendida" → reformular a pergunta (nunca adivinhar).

### slot_schemas por etapa (FR-017 — cobertura mínima de 5 etapas)

Cada etapa define o formato-alvo passado ao LLM (via json_schema) e o mapeamento para o
campo de perfil consolidado. Nenhuma etapa reverte um fato já consolidado sem
qualificação explícita de alta confiança (Edge Case da spec).

| Etapa (flow.py) | slot | valores esperados | destino no perfil |
|-----------------|------|-------------------|-------------------|
| `qualif_medico` (ETAPA_QUALIF_MEDICO) | elegibilidade_medica | `sim` / `nao` | `context.eh_medico` |
| (objetivo com produto) | objetivo | texto curto normalizado | resumo/perfil |
| `qualif_experiencia` (ETAPA_QUALIF_EXPERIENCIA) | experiencia_previa | `sim` / `nao` / grau | perfil |
| `qualif_especialidade` (ETAPA_QUALIF_ESPECIALIDADE) | especialidade | especialidade médica | perfil |
| `escolha_turma` (ETAPA_ESCOLHA_TURMA) | escolha_turma | id/rótulo de turma da config | perfil |

- **FR-016**: o extractor recebe `known_facts` (`_perfil_conhecido(context)`, já usado
  em flow.py:1409) + histórico para desambiguar e evitar reperguntar.
- **Fast-path (FR-013)**: `_detectar_medico_investidor`, `_detectar_fechamento`,
  `_eh_pergunta` etc. rodam antes; o LLM é curto-circuitado quando resolvem.

---

## 4. Configuração (envs novos)

Adicionados a `app/config.py` (`Settings`), `.env.example` e `stack.yml`:

| Env | Campo Settings | Tipo | Default | Uso |
|-----|----------------|------|---------|-----|
| `SLOT_CONFIDENCE_THRESHOLD` | `slot_confidence_threshold` | float | `0.6` | FR-015, limiar global das 5 etapas |
| `VERIFY_TIMEOUT_SECONDS` | `verify_timeout_seconds` | int | `3` | FR (Q3): timeout duro de fidelidade e slot-filling |

Modelos reusam `openai_model_reasoning` (gpt-4o) e `openai_model_cheap` (gpt-4o-mini)
já existentes (config.py:38,40) — sem novo campo de modelo.

---

## 5. Fluxo de dados (resumo)

```
mensagem do lead (DADO nao-confiavel)
   │
   ├─ fast-path deterministico (flow.py) ──resolve──▶ transicao de estado (sem LLM)
   │                                    └─nao resolve─┐
   │                                                  ▼
   │                              SlotExtractor.extract() → SlotQualificacao
   │                              (confianca >= 0.6 ? aceita : reformula)
   │
   └─ etapa DUVIDAS / objecao
         GroundedResponder.generate() → RespostaEstruturada (contrato JSON, 1 retry)
             │ toca condicao comercial?
             ├─ nao ─▶ envia
             └─ sim ─▶ FidelityGate.verificar() → VeredictoFidelidade
                          fiel=True  ─▶ envia
                          fiel=False / timeout / erro ─▶ contingencia (bloco canonico → handoff)
```

Verbatim (apresentações, menus, paciente-modelo) e Banco de Objeções saem direto do DB,
fora deste diagrama (nunca tocam contrato/portão).
