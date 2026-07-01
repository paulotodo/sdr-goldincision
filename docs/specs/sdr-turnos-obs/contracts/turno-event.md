# Contract: Evento de Turno (observabilidade)

Interface interna: evento JSON estruturado emitido ao stdout via
`app/observability/log.py::log_turno`. Consumido por coletor de logs
externo (fora do escopo desta feature). Não é endpoint HTTP.

## Assinatura proposta (função)

```
log_turno(
    chamado_id: int,
    turno_sessao: int,
    etapa_entrada: str,
    etapa_saida: str,
    idioma: str,
    n_blocos_enviados: int,
    acao: str,                      # resposta|nudge|handoff|retomada|sessao_nova|erro
    duracao_ms: int,
    tentativas: int,
    intencao: str | None = None,
    handoff_destino: str | None = None,
    motivo: str | None = None,      # turnos_no_no|turnos_sessao
) -> None
```

Reusa `_scrub`, `_mask_number`, `_emit` já existentes no módulo.

## Payload emitido (exemplo — turno normal)

```json
{
  "event": "turno",
  "chamado_id": 12345,
  "turno_sessao": 3,
  "etapa_entrada": "QUALIFICACAO_MEDICO",
  "etapa_saida": "APRESENTACAO_CURSO",
  "intencao": "interesse_curso",
  "idioma": "pt",
  "n_blocos_enviados": 2,
  "acao": "resposta",
  "handoff_destino": null,
  "duracao_ms": 4210,
  "tentativas": 0,
  "motivo": null
}
```

## Payload emitido (exemplo — handoff por teto de sessão)

```json
{
  "event": "turno",
  "chamado_id": 12345,
  "turno_sessao": 25,
  "etapa_entrada": "DUVIDAS",
  "etapa_saida": "HANDOFF",
  "intencao": "duvida",
  "idioma": "pt",
  "n_blocos_enviados": 1,
  "acao": "handoff",
  "handoff_destino": "consultores",
  "duracao_ms": 3800,
  "tentativas": 0,
  "motivo": "turnos_sessao"
}
```

## Invariantes do contrato

- **C-1 (FR-015)**: exatamente 1 evento `"turno"` por turno processado.
- **C-2 (FR-016)**: em falha do processamento, ainda emitir com
  `acao="erro"` (via `finally`) — nunca lacuna silenciosa.
- **C-3 (Segurança)**: nenhum conteúdo bruto da mensagem do lead; número
  mascarado; secrets removidos por `_scrub`.
- **C-4 (FR-006)**: `motivo` preenchido sempre que `acao ∈ {nudge, handoff}`
  originado do orçamento de turnos.
- **C-5**: `handoff_destino`, quando presente, é um destino lógico da
  allowlist/config (`handoff_queue_ids_json`), nunca gerado pelo LLM.
