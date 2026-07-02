# Contract: Campo Redis `troca_pendente` do hash `estado:{chamadoId}`

Interface interna: campo adicionado ao hash de sessão existente
(`app/core/redis_keys.py::estado_key`). Sem migration. Mesmo padrão já
auditado para `overflow_blocos`/`overflow_idioma` (`sdr-turnos-obs`).

## Campo e operações

| Campo | Operação de escrita | Operação de leitura | Semântica |
|-------|---------------------|---------------------|-----------|
| `troca_pendente` | `HSET estado:{id} troca_pendente <json>` | `HGET` | Pergunta de confirmação/desambiguação de troca de caminho em aberto (ver schema em `data-model.md`). |

## Schema do JSON (ver `data-model.md` §Estado de Confirmação/Desambiguação Pendente)

```json
{
  "destinos": [1, 2],
  "origem": 3,
  "metodo": "deterministico",
  "confianca": null,
  "tipo": "desambiguacao"
}
```

## Invariantes

- **P-1 (Precedência de overflow, clarify Q2)**: este campo NUNCA é lido
  nem escrito enquanto `overflow_blocos` (campo existente de
  `sdr-turnos-obs`) não estiver vazio. A ordem de checagem em `process()`
  (overflow antes de `_process_core`) já garante isso por construção —
  nenhuma mudança de ordem introduzida por esta feature.
- **P-2 (Leitura no início de `_process_core`, clarify Q1)**: se presente,
  a mensagem corrente é tratada EXCLUSIVAMENTE como resposta a esta
  pergunta — nunca como nova detecção de troca, mesmo que contenha um
  marcador explícito de correção.
- **P-3 (Limpeza sempre explícita)**: removido (`HDEL`) tanto no caminho de
  sucesso (confirmação/escolha reconhecida) quanto no caminho de falha
  (negação/não-reconhecimento) — nunca fica "pendurado" indefinidamente
  pendente de uma mensagem futura não relacionada.
- **P-4 (Fail-open, mesmo padrão de `sdr-turnos-obs` Decision 2)**: leitura
  ausente/corrompida (`HGET` → nil ou JSON inválido) é tratada como "sem
  pergunta pendente" — nunca bloqueia o atendimento.
- **P-5 (Sem TTL novo)**: o campo herda o TTL do hash `estado:{id}`.
- **P-6 (Não conta como troca de orçamento de turnos)**: a leitura/escrita
  deste campo não interage com `turnos_sessao`/`turnos_no_no`
  (`sdr-turnos-obs`); o orçamento de turnos continua incrementando
  normalmente independente de haver pergunta de troca pendente.
