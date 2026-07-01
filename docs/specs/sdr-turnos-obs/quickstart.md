# Quickstart / Cenários de Teste: sdr-turnos-obs

Cenários críticos que validam a feature. Todos usam o **FlowEngine REAL**
(via `StubFlowEngine` que stuba só I/O de DB) e Redis (real ou fakeredis
conforme a suíte existente). Não mockar o motor.

> Feature single-layer de backend (Python/FastAPI + Redis). Sem borda
> backend↔frontend — sem cenário de roundtrip HTTP/JSON de UI. Ver
> plano.md §Convenções de Borda ("N/A — single-layer backend").

## Cenário 1 — Nudge por teto de nó (US1 / FR-003)

1. Estado inicial: sessão na etapa X, `turnos_no_no:X = MAX_TURNOS_NO_NO - 1`.
2. Lead envia mais um turno reconhecido → `_process_consolidated_messages`.
3. Contador incrementa para `MAX_TURNOS_NO_NO`.
   **Expected**: resposta contém nudge cordial (oferta de especialista, texto
   i18n de `_T`), `acao="nudge"`, `motivo="turnos_no_no"` no evento; a sessão
   NÃO é encerrada nem transferida (lead pode continuar).

## Cenário 2 — Handoff por teto de sessão (US1 / FR-004)

1. Estado inicial: `turnos_sessao = MAX_TURNOS_SESSAO - 1`, caminho corrente
   com destino lógico conhecido.
2. Lead envia mais um turno.
   **Expected**: `acao="handoff"`, `handoff_destino` = destino lógico da
   allowlist (nunca vazio, nunca do LLM), `motivo="turnos_sessao"`; o teto
   de sessão prevalece mesmo se o teto de nó também for atingido.

## Cenário 3 — ETAPA_DUVIDAS tolera mais turnos (US1 / FR-005)

1. Estado inicial: etapa de dúvidas, `turnos_no_no:DUVIDAS` entre
   `MAX_TURNOS_NO_NO` e `MAX_TURNOS_DUVIDAS`.
2. Lead envia mais uma dúvida.
   **Expected**: nenhum nudge disparado (abaixo do limiar elevado de dúvidas);
   fluxo normal, `acao="resposta"`.

## Cenário 4 — Coexistência com anti-loop (US1 / FR-019)

1. Estado inicial: etapa X com `Contato.etapa_funil = {"et":"X","n":1}`
   (anti-loop existente) e `turnos_no_no:X = 2`.
2. Lead envia resposta reconhecida.
   **Expected**: `turnos_no_no:X` incrementa para 3; o contador anti-loop
   NÃO é alterado por esta feature; `_reformular_ou_handoff` continua
   governado só por `_MAX_TENTATIVAS`.

## Cenário 5 — Retomada por inatividade (US2 / FR-009)

1. Estado inicial: sessão em fluxo, `ultima_interacao` =
   agora - (REENGAJAMENTO_HORAS + 1h).
2. Lead envia nova mensagem → detecção lazy no início de `engine.process`.
   **Expected**: resposta abre com retomada cordial reconhecendo a pausa,
   sem reiniciar a jornada; etapa preservada; `acao="retomada"`.

## Cenário 6 — Expiração preservando perfil (US2 / FR-010)

1. Estado inicial: `ultima_interacao` = agora - (EXPIRA_SESSAO_HORAS + 1h);
   Contato com `medico=True`, `idioma=pt`, `especialidade=X`.
2. Lead envia nova mensagem.
   **Expected**: etapa volta à saudação inicial; `acao="sessao_nova"`;
   perfil (`medico`, `idioma`, `especialidade`, `experiencia`) preservado —
   nenhuma dessas perguntas é re-feita.

## Cenário 7 — Durabilidade do debounce em restart (US3 / FR-011/012)

1. Estado inicial: lista `debounce:{id}` pré-existente no Redis (rajada não
   flushada).
2. Instanciar novo `DebounceManager` + rodar recovery de startup.
   **Expected**: a rajada é processada exatamente uma vez (flush atômico
   `LRANGE`+`DEL`); rodar o recovery 2x não duplica o processamento.

## Cenário 8 — Lock cobre turno lento (US4 / FR-013)

1. Estado inicial: turno simulado com duração próxima do pior caso
   (LLM lento + 4 envios paced + retries).
2. Processar o turno com `LOCK_TTL_MS = ~90_000`.
   **Expected**: o lock permanece válido do início ao fim; nenhum
   re-processamento por expiração; `duracao_ms` registrado no evento < TTL.

## Cenário 9 — Evento de turno sempre emitido (US5 / FR-015/016)

1. Estado inicial: turno normal.
   **Expected**: exatamente 1 evento `"turno"` com todos os campos de
   FR-015; número mascarado; sem conteúdo bruto do lead.
2. Estado inicial: turno que lança exceção no meio do processamento.
   **Expected**: ainda emite 1 evento com `acao="erro"` (via `finally`).

## Cenário 10 — Golden set roda e reporta (US6 / FR-017/018)

1. Executar `python3 -m pytest tests/golden -m golden`.
   **Expected**: relatório por dimensão (fluxo correto, abstenção correta,
   zero preço inventado); a suíte é independente do CI obrigatório; um caso
   que re-pergunta slot preenchido falha; um caso fora da Base que responde
   com preço inventado falha.

**Nota (CHK011 / research.md Decision 9)**: nesta Onda 1 o relatório do
golden set é **informativo** — não há patamar mínimo de taxa de acerto que
bloqueie merge/CI. Um threshold por dimensão pode ser adicionado depois,
como mudança isolada, quando houver histórico de execuções suficiente para
calibrá-lo.

## Verificação global

- `python3 -m pytest -q` → suíte inteira verde (~320 + novos testes).
- `ruff check app/ tests/` → limpo.
- Validação real (WhatsApp `#reset`, número autorizado): nudge→handoff em
  PT; retomada por inatividade; não-perda de turno em restart; repetir 1
  caminho em EN ou ES.
