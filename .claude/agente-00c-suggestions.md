# Sugestoes do Agente-00C — feat-sdr-fidelidade-json-20260701T131559Z

Total: 2 sugestoes registradas.

## sug-001 — skill `validate-documentation` — severidade: aviso

**Criada em**: 2026-07-01T13:23:14Z

**Issue aberta**: (nenhuma)

**Diagnostico**:

validate-documentation so tem perfis dedicados para UC-*.md e RB-NNN-*.md; ao validar um spec.md gerado pela skill specify (formato SDD: User Scenarios/FR-NNN/Success Criteria), nao ha checklist estrutural correspondente e o orquestrador precisou adaptar a validacao manualmente (grep de placeholders/duplicatas/secoes).

**Proposta**:

Adicionar um terceiro perfil --sdd-spec (ou deteccao automatica pelo path docs/specs/*/spec.md) cobrindo: secoes obrigatorias do template feature-spec.md, contagem/duplicidade de FR-NNN, maximo de 3 NEEDS CLARIFICATION, ausencia de detalhe de implementacao (linguagens/frameworks) nos FRs.

**Referencias**:

- docs/specs/sdr-fidelidade-json/spec.md
- /root/.claude/skills/specify/templates/feature-spec.md

---

## sug-002 — skill `validate-documentation` — severidade: informativa

**Criada em**: 2026-07-01T13:56:43Z

**Issue aberta**: (nenhuma)

**Diagnostico**:

A skill valida apenas esquemas UC-*.md e runbooks RB-*.md; para artefatos SDD (plan.md/research.md/data-model.md) nao ha perfil, caindo em checagens genericas manuais (placeholders, fences, links). Gate rodou por checks deterministicos externos.

**Proposta**:

Adicionar um perfil --sdd (ou deteccao de plan.md/research.md/data-model.md/spec.md) com checagens: sem TBD/TODO, code fences balanceados, Mermaid parseavel, links internos validos, headings minimos.

**Referencias**:

- docs/specs/sdr-fidelidade-json/plan.md

---

