# Data Model: sdr-fluidez-intencao

> **Sem migration.** Todos os dados abaixo são efêmeros (Redis), constantes
> de código (léxico) ou extensões aditivas de um evento de log estruturado
> já existente (stdout). Nenhuma tabela/coluna Postgres nova. As entidades
> conceituais da spec (`Evento de Troca de Caminho`, `Tentativa de
> Reformulação`, `Perfil do Lead estendido`) mapeiam a: (1) campos aditivos
> do evento de turno já existente (`sdr-turnos-obs/contracts/turno-event.md`),
> (2) um novo campo transiente do hash Redis `estado:{chamadoId}`, e (3)
> constantes de código (léxico compartilhado) — o "Perfil do Lead" já é
> preservado estruturalmente (nenhum campo novo necessário, ver §Relationships).

## Entity: Léxico Compartilhado de Reconhecimento (constante de código)

Não é dado de runtime — vive em `app/core/flow.py` como constantes
`_LEXICO_CAMINHOS` e `_MARCADORES_CORRECAO`, reusadas por dois pontos:
o fast-path de texto livre do menu inicial (FR-011/012/013) e o detector de
troca de caminho no meio da jornada (FR-002/003).

| Field | Type | Notes |
|-------|------|-------|
| `_LEXICO_CAMINHOS` | `dict[int, set[str]]` | Chave = número do caminho (`CaminhoMapaMestre`); valor = variantes normalizadas (via `_norm()`) de nomes de produto/caminho, incluindo erros leves comuns de digitação/acentuação. |
| `_MARCADORES_CORRECAO` | `dict[str, set[str]]` | Chave = idioma (`pt`\|`en`\|`es`); valor = tokens/frases normalizadas que sinalizam correção explícita ("na verdade", "me enganei", "actually", "de hecho", etc.). |

### Regras de matching

- Normalização via `_norm()` já existente (`app/core/flow.py:2805`):
  minúsculas, sem acento, sem pontuação, espaços colapsados.
- Matching por substring/token (mesmo padrão de `_detectar_escolha_turma`/
  `_detectar_especialidade`) — nunca fuzzy-matching probabilístico
  (Research Decision 1).
- **Determinístico por construção**: o mesmo input normalizado sempre
  produz o mesmo resultado; testável byte-a-byte no golden set.

## Entity: Estado de Confirmação/Desambiguação Pendente (campo Redis `estado:{chamadoId}`)

Chave: `estado_key(chamado_id)` (`app/core/redis_keys.py:80`). Hash já
existente (mesmo padrão de `OVERFLOW_BLOCOS_FIELD`/`OVERFLOW_IDIOMA_FIELD`);
adicionamos um campo.

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `troca_pendente` | JSON (string no Redis) ou ausente | ver schema abaixo | Estado transiente da pergunta de confirmação/desambiguação de troca de caminho em aberto. Ausente ⇒ nenhuma pergunta pendente (comportamento normal). |

**Schema do JSON**:

```json
{
  "destinos": [3],
  "origem": 1,
  "metodo": "deterministico",
  "confianca": null,
  "tipo": "confirmacao"
}
```

| Subcampo | Type | Notes |
|----------|------|-------|
| `destinos` | `int[]` | 1 elemento para confirmação (edge case: intenção clara sem marcador explícito); exatamente 2 elementos para desambiguação (FR-008/012). |
| `origem` | `int` | Caminho ativo no momento em que a pergunta foi feita. |
| `metodo` | `"deterministico"\|"assistido"` | Como a troca candidata foi detectada (FR-017). |
| `confianca` | `float\|null` | Grau de confiança quando `metodo="assistido"`; `null` quando `"deterministico"` (léxico não tem confiança fracionária). |
| `tipo` | `"confirmacao"\|"desambiguacao"` | Distingue a pergunta "confirma a troca?" (1 destino) da pergunta "qual dos dois?" (2 destinos). |

**TTL**: herda a política de `estado:{id}` (sessão). Não introduz TTL novo.

Espelhado em `SessionContext.troca_caminho_pendente: Optional[dict] = None`
(`app/core/memory.py`, mesmo padrão de `overflow_blocos`/`overflow_idioma`).

### Regras de manipulação

- **Escrita (edge cases da spec)**: setado quando (a) intenção de troca é
  clara mas SEM marcador explícito de correção (pergunta de confirmação
  curta antes de trocar), ou (b) a correção citada é compatível com
  exatamente 2 caminhos (pergunta de desambiguação, FR-008).
- **Leitura (clarify Q1)**: checado no **início** de `_process_core`, antes
  do resolver do nó. Se presente, a mensagem corrente é interpretada
  EXCLUSIVAMENTE como resposta a essa pergunta pendente (sim/não ou escolha
  entre os `destinos`, por léxico determinístico) — nunca como nova
  detecção de troca.
- **Limpeza**: removido (a) ao confirmar/escolher (a troca é efetivada), ou
  (b) ao negar/não-reconhecer a resposta — neste caso a tentativa conta
  contra `_MAX_TENTATIVAS` da pergunta **original** pendente (etapa
  inalterada), não uma tentativa nova (clarify Q1).
- **Precedência sobre retomada de overflow (clarify Q2 / Decision 5)**:
  enquanto `context.overflow_blocos` não estiver vazio, este campo nunca é
  lido nem escrito — o overflow tem prioridade total (nenhuma mudança de
  ordem em `process()`).

### State transitions

```
sem troca_pendente
  └─ detector encontra candidato claro c/ marcador explícito
       → despacha direto para o caminho-alvo (sem pergunta)
  └─ detector encontra candidato claro SEM marcador explícito
       → troca_pendente = {destinos:[X], tipo:"confirmacao", ...}
  └─ detector encontra 2 candidatos compatíveis
       → troca_pendente = {destinos:[X,Y], tipo:"desambiguacao", ...}

troca_pendente presente (tipo=confirmacao)
  ├─ lead confirma (sim)     → despacha para destinos[0]; troca_pendente = null
  └─ lead nega/não reconhece → troca_pendente = null; _tent_bump na etapa/pergunta ORIGINAL

troca_pendente presente (tipo=desambiguacao)
  ├─ lead escolhe um dos 2   → despacha para o escolhido; troca_pendente = null
  └─ lead não reconhece      → troca_pendente = null; _tent_bump na etapa/pergunta ORIGINAL
```

## Entity: Evento de Turno — extensão aditiva (`log_turno`)

Estende o contrato já existente `sdr-turnos-obs/contracts/turno-event.md`
(`app/observability/log.py::log_turno`). Novos campos, todos **opcionais**
(`Optional`, default `None`) — nenhuma mudança nos campos existentes.

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `troca_caminho_origem` | int, nullable | um dos 6 valores de `CaminhoMapaMestre` | Caminho de origem quando uma troca ocorreu neste turno (FR-017). |
| `troca_caminho_destino` | int, nullable | um dos 6 valores de `CaminhoMapaMestre` | Caminho de destino quando uma troca ocorreu neste turno (FR-017). |
| `troca_metodo` | string, nullable | `"deterministico"\|"assistido"` | Método de detecção (FR-017). |
| `troca_confianca` | float, nullable | `0.0..1.0` quando `troca_metodo="assistido"`; `null` caso contrário | Grau de confiança da classificação assistida (FR-017). |
| `reformulacao_variante` | int, nullable | índice da variante usada (`(n-1) % len(pool)`) | Qual variação de reformulação foi enviada neste turno (FR-018). |

### Invariantes de segurança (herdadas de `sdr-turnos-obs` Decision 8)

- Nenhum campo novo carrega conteúdo bruto da mensagem do lead (SEC-LLM-1);
  apenas metadados estruturados (números de caminho, método, confiança
  numérica, índice de variante).
- `_scrub`/`_mask_number`/`_emit` já existentes continuam cobrindo o evento
  inteiro — nenhuma mudança no pipeline de emissão.
- Evento continua emitido exatamente 1x por turno (C-1/C-2 do contrato
  original preservados).

## Entity: Caso de Referência (golden set — extensão de fixtures de teste)

Reusa a mesma estrutura já modelada em `sdr-turnos-obs/data-model.md`
(`tests/golden/casos/*.json`). Novos casos cobrem as dimensões desta
feature; nenhuma mudança de schema — apenas novos valores possíveis em
`esperado.proxima_acao` (`"troca_caminho"`, `"confirmacao_troca"`,
`"desambiguacao"`, `"reformulacao"`) e um novo campo opcional:

| Field | Type | Notes |
|-------|------|-------|
| `esperado.nao_repetir_texto` | bool (opt) | Assert de que a resposta é textualmente diferente da mensagem anterior enviada na mesma pergunta (FR-014/US3). |
| `esperado.troca_destino` | int (opt) | Caminho de destino esperado após a troca (quando `proxima_acao="troca_caminho"`). |

## Relationships

- `Estado de Confirmação/Desambiguação Pendente` referencia `chamado_id`
  (mesma chave lógica do hash `estado:{chamadoId}`), sem FK física.
- `Evento de Turno` (extensão) referencia `chamado_id` e `turno_sessao`,
  correlacionável com os campos já existentes de `sdr-turnos-obs`
  (`turnos_no_no`, `ultima_interacao`) para análise combinada.
- **`Perfil do Lead` (existente, estendido) — nenhum campo novo**: os
  atributos de qualificação (`eh_medico`, `especialidade`,
  `experiencia_corporal`, `idioma`, `produto_interesse`) já são propriedades
  de `Contato`/`SessionContext`, **independentes de `caminho`/`etapa`**
  (verificado em `app/core/memory.py`). A troca de caminho não precisa
  "migrar" nem copiar esses campos — eles permanecem válidos por construção
  (FR-006 satisfeito sem alteração de schema).
