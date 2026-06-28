# Contract — Motor Conversacional (Mapa Mestre) + LLM

**Feature**: `sdr-whatsapp` | Cobre FR-006..FR-015, FR-018..FR-021, Princípios I, II, III, V.
Contrato interno (não exposto externamente) do orquestrador de conversa. Fonte da
verdade de conteúdo: documentos oficiais em `knowledge_base/documentos_agente/`
(Regra 30 — prevalecem sobre qualquer instrução de implementação).

## Pipeline do motor (após debounce + lock)

```
entrada consolidada (texto/transcrição)
  │
  ├─ 1. Carregar contexto: contato (variáveis) + ticket (caminho/etapa) +
  │     resumo rolante + janela quente
  ├─ 2. Detectar idioma (modelo barato) → atualiza contato.idioma
  ├─ 3. Classificar intenção / caminho (modelo barato)
  │       - intenção clara → entra direto no caminho (sem menu) (FR-007)
  │       - não clara (primeira msg) → menu inicial 6 opções
  │       - mudança de assunto → redireciona, preserva variáveis (US2-AS5)
  ├─ 4. Recuperar Base Oficial na hierarquia (FR-008):
  │       Mapa Mestre → Apresentação do produto → Banco Objeções → FAQ
  ├─ 5. Gerar resposta (modelo de raciocínio) com grounding estrito (FR-010,011,013)
  ├─ 6. Determinar ação: responder | enviar link | handoff | encerrar
  ├─ 7. Persistir: mensagens, variáveis, etapa, resumo rolante (se limiar)
  └─ 8. Enviar via OutboundClient (blocos curtos; uma pergunta/msg) (FR-015)
```

## Caminhos do Mapa Mestre (FR-006)

| Caminho | Tema | Qualificação | Saída |
|---------|------|--------------|-------|
| 1 | Curso Online HG | é médico? | apresentação + link inscrição (auto-atend., encerra) |
| 2 | Cursos Presenciais | médico → experiência → especialidade | HG Módulo 1 (iniciante) ou HG360 (SP/Barcelona) + handoff consultor |
| 3 | Sistema GoldIncision | esclarece "não é curso avulso" → Licenciamento/Franquia | reunião + handoff especialista |
| 4 | Aluno/suporte | categoria de necessidade | handoff equipe (sem resolver) |
| 5 | Paciente modelo | — | encaminha SÓ Nídia (+55 21 97423-9844), encerra |
| 6 | Outro | — | tratar / handoff conforme caso |

**Regra de elegibilidade (FR-009, Princípio V — inflexível)**:
- Caminhos 1, 2 e Licenciamento exigem médico com registro ativo. Não médico →
  informa exclusividade, agradece, encerra (US1-AS4).
- Experiência exclusivamente facial = NÃO elegível ao HG360 → indica HG Módulo 1
  (US1-AS5). Dermatologia/Cirurgia Plástica/Cirurgia Vascular → HG360 (US1-AS6).

## Contrato do LLM — classificação (modelo barato)

**Input**: entrada consolidada + variáveis conhecidas + etapa atual.
**Output** (structured / JSON):
```jsonc
{ "idioma": "pt|en|es",
  "caminho": 1, "intencaoClara": true,
  "mudancaDeAssunto": false,
  "pedeHumano": false }
```

## Contrato do LLM — resposta (modelo de raciocínio)

**Input (grounding estrito, hierarquia fixa)**:
- trecho do Mapa Mestre da etapa corrente,
- apresentação oficial do produto (idioma, verbatim) — quando aplicável,
- banco de objeções do produto (idioma),
- FAQ,
- variáveis do contato + resumo rolante + janela quente.

**Regras MUST no prompt**:
- Responder SOMENTE com base no material fornecido (Princípio II). Fora da Base →
  "não possuo essa informação" + handoff (FR-008, SC-008).
- NÃO reescrever/resumir apresentações oficiais — enviar na íntegra (FR-010).
- Objeção comercial → SOMENTE entradas do banco de objeções carregado (FR-011).
- Não repetir perguntas já respondidas (variáveis já preenchidas) (FR-021).
- Identidade: "Consultor Virtual Oficial da GoldIncision" (FR-013); nunca afirma
  ser humano.
- Idioma de resposta = idioma do lead; links/apresentações na variante correta
  (FR-012).
- Blocos curtos; uma pergunta por mensagem, salvo menu (FR-015, Princípio IV).

**Output**: lista ordenada de blocos a enviar + ação
(`responder|enviar_link|handoff|encerrar`) + (se handoff) destino/motivo.

## Memória (FR-018..FR-021)

- Toda mensagem (in/out) → tabela `mensagem` (durável, append-only).
- Janela quente (Redis) = últimas N para latência.
- Resumo rolante (modelo barato) re-sintetizado ao cruzar limiar de tokens
  (default ~3000), preservando narrativa + variáveis (FR-019, SC-002).
- Variáveis (`idioma`, `eh_medico`, `especialidade`, `experiencia_corporal`,
  `produto_interesse`, `etapa_funil`) persistidas por contato e reutilizadas
  entre tickets/sessões (FR-020, FR-021, US2-AS4).

## Segurança LLM/Agentic (OWASP LLM01/05/07, ASI01/02/06)

- **SEC-LLM-1 (prompt injection — LLM01/ASI01)**: o conteúdo do lead (texto e
  transcrição de áudio) é NÃO confiável e pode conter tentativa de injeção
  indireta. No prompt, separar estruturalmente a instrução de sistema do conteúdo
  do usuário (mensagens em papel `user`, nunca concatenadas à instrução). Não
  confiar em marcadores apenas; a instrução de sistema reforça que conteúdo do
  usuário nunca altera regras de fluxo/elegibilidade/anti-alucinação.
- **SEC-LLM-2 (output handling — LLM05) + ação restrita (ASI06)**: a ação do LLM
  é um enum fechado (`responder|enviar_link|handoff|encerrar`); nenhum texto livre
  vira comando. O texto gerado é apenas enviado como mensagem (não executado).
- **SEC-LLM-3 (handoff target allowlist — ASI02 tool misuse) — MEDIUM**: o
  `destino` do handoff NÃO é texto livre do LLM. Deve ser resolvido contra uma
  allowlist de filas/conexões conhecidas (config). O LLM indica a INTENÇÃO de
  handoff; o código mapeia para o destino válido. Nunca transferir para destino
  não presente na allowlist.
- **SEC-LLM-4 (system prompt leakage — LLM07)**: a instrução de sistema não
  contém secrets; tentativas do lead de extrair o prompt recebem a resposta
  padrão de escopo (não revelar instruções internas).
- **SEC-LLM-5 (logging/PII — A09/LLM02)**: logs estruturados nunca registram
  tokens/secrets; número de contato é PII de uso operacional, com acesso
  restrito.

## Anti-alucinação — guarda de cobertura (Princípio II, SC-008)

Antes de enviar, validar que a resposta não introduz fatos do produto ausentes
do material carregado. Pergunta fora do escopo (ex. "CNPJ?") → resposta fixa de
indisponibilidade + handoff. Nunca estima valores/políticas/datas não presentes
na Base Oficial.
