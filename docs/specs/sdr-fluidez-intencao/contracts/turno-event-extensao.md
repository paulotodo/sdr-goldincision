# Contract: Extensão aditiva do Evento de Turno (`log_turno`)

Estende `sdr-turnos-obs/contracts/turno-event.md` (`app/observability/log.py::
log_turno`). Não substitui o contrato original — apenas adiciona campos
opcionais. Todos os invariantes C-1..C-5 do contrato original permanecem
válidos e não são repetidos aqui.

## Novos parâmetros da assinatura (todos opcionais, default `None`)

```python
log_turno(
    # ... parametros existentes (ver contrato original) ...
    troca_caminho_origem: int | None = None,
    troca_caminho_destino: int | None = None,
    troca_metodo: str | None = None,        # deterministico|assistido
    troca_confianca: float | None = None,   # 0.0..1.0, so quando metodo=assistido
    reformulacao_variante: int | None = None,  # indice da variante usada
) -> None
```

## Payload emitido (exemplo — troca de caminho detectada)

```json
{
  "event": "turno",
  "chamado_id": 12345,
  "turno_sessao": 7,
  "etapa_entrada": "qualif_especialidade",
  "etapa_saida": "sistema_objetivo",
  "intencao": null,
  "idioma": "pt",
  "n_blocos_enviados": 1,
  "acao": "resposta",
  "handoff_destino": null,
  "duracao_ms": 2100,
  "tentativas": 0,
  "motivo": null,
  "troca_caminho_origem": 1,
  "troca_caminho_destino": 3,
  "troca_metodo": "deterministico",
  "troca_confianca": null,
  "reformulacao_variante": null
}
```

## Payload emitido (exemplo — reformulação, sem troca)

```json
{
  "event": "turno",
  "chamado_id": 12345,
  "turno_sessao": 4,
  "etapa_entrada": "sistema_objetivo",
  "etapa_saida": "sistema_objetivo",
  "intencao": null,
  "idioma": "pt",
  "n_blocos_enviados": 1,
  "acao": "resposta",
  "handoff_destino": null,
  "duracao_ms": 180,
  "tentativas": 1,
  "motivo": null,
  "troca_caminho_origem": null,
  "troca_caminho_destino": null,
  "troca_metodo": null,
  "troca_confianca": null,
  "reformulacao_variante": 0
}
```

## Invariantes adicionais

- **E-1 (FR-017)**: `troca_caminho_origem`/`troca_caminho_destino` são
  preenchidos SEMPRE juntos (ambos presentes ou ambos `null`), nunca
  parcialmente.
- **E-2 (FR-017)**: `troca_confianca` só é não-nulo quando
  `troca_metodo="assistido"`; quando `"deterministico"`,
  `troca_confianca` é sempre `null` (o léxico não produz confiança
  fracionária).
- **E-3 (FR-018)**: `reformulacao_variante` só é preenchido quando o turno
  resultou em reformulação (`tentativas >= 1` e a resposta não foi
  compreendida); ausente em turnos normais.
- **E-4 (Aditivo puro)**: nenhum campo existente do contrato original
  (`sdr-turnos-obs/contracts/turno-event.md`) muda de tipo, semântica ou
  obrigatoriedade.
