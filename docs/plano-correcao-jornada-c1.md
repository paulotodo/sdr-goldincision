# Plano de Correção — Fidelidade da Jornada (Caminho 1 + tom consultivo)

> Origem: teste de lead real relatado pelo operador (2026-06-29). Documento
> autocontido para execução numa sessão limpa. Fonte da verdade:
> `knowledge_base/documentos_agente/MAPA MESTRE DO ATENDIMENTO.docx` e
> `REGRAS GERAIS DO AGENTE COMERCIAL GOLDINCISION.docx`.

## 0. Jornada relatada vs. Mapa Mestre

**Relato do operador:** (1) saudação → agente respondeu "de forma rígida" enviando
a lista de cursos (menu); (2) perguntou "quais são os cursos online" → o agente já
questionou "é médico com registro?"; (3) ao confirmar ser médico → recebeu os dados
do curso e o preço.

**Veredito:** a *estrutura* segue o esqueleto do Caminho 1 (menu quando a intenção
não é clara → qualificação médica → apresentação oficial). O que **diverge** do Mapa
é (a) a **REGRA "pergunta direta → resposta direta"**, que não foi honrada, e (b) o
**tom/execução**, que está rígido e impessoal — distante das Regras 11/12/13 (cordial,
elegante, consultivo) e da humanização pretendida. Importante: o relato bate
exatamente com o comportamento da imagem **antiga (1.1.0)**; mesmo na 1.2.0 já
deployada, persiste o gap (a) abaixo.

## 1. Achados (gap a gap)

### Gap A — REGRA do Caminho 1 não aplicada a perguntas informativas gerais ⚠️ (alta)
O Mapa Mestre, Caminho 1, REGRA: *"Se o usuário... perguntando diretamente sobre
preço, conteúdo, duração, certificado ou qualquer outra informação do Curso Online,
o agente deverá responder normalmente, sem reiniciar o fluxo. Isso evita aquele
comportamento irritante de GPT: Usuário: Quanto custa? GPT: Você é médico?"*

Hoje `_eh_pergunta_informativa` (`app/core/flow.py`) só reconhece palavras-chave
específicas (preço, valor, duração, certificado…). Perguntas **gerais** sobre o
curso não disparam a resposta-direta e caem no gate "é médico?":

| Mensagem | `_eh_pergunta_informativa` | Resultado atual |
|---|---|---|
| "quanto custa?" | True | responde ✅ |
| "quais são os cursos online?" | **False** | pergunta "é médico?" ❌ |
| "me fala sobre o curso online" | **False** | pergunta "é médico?" ❌ |
| "quero saber sobre o curso" | **False** | pergunta "é médico?" ❌ |

É exatamente o anti-padrão que o Mapa manda evitar.

### Gap B — Pergunta de qualificação genérica (não fiel ao texto do Mapa) ⚠️ (média)
O Mapa tem textos de qualificação **específicos por caminho**:
- C1: *"Perfeito! O Curso Online de Harmonização Glútea é uma formação exclusiva
  para médicos. Antes de prosseguirmos, preciso confirmar uma informação: Você é
  médico com registro profissional ativo em seu país?"*
- C2: *"Perfeito! Os Cursos Presenciais de Harmonização Glútea são exclusivos para
  médicos. Antes de prosseguirmos..."*

Hoje `_gerar_pergunta_medico` usa **um texto genérico** para todos os caminhos
("Ótimo! Antes de prosseguirmos, preciso confirmar..."), sem contextualizar o
porquê (o curso ser exclusivo para médicos). Isso torna a pergunta "seca".

### Gap C — Saudação não é reconhecida; menu parece "lista jogada" (baixa)
Numa saudação, enviar o menu é correto (Mapa ETAPA 1). Mas o tom pode reconhecer a
saudação e soar consultivo, não como um dump de opções. O Mapa abre com *"Olá! Seja
bem-vindo(a) à GoldIncision."* — vale alinhar o `generate_menu` a essa abertura.

### Gap D — Verificar fases ETAPA 3/4/5 ao vivo (média)
Confirmar em produção (1.2.0) que após a apresentação o fluxo abre dúvidas (ETAPA 3),
oferece o link de forma determinística (ETAPA 4 "Gostaria de receber o link?") e
envia o link no idioma (ETAPA 5). O código existe; falta validação real por WhatsApp.

> Não é gap: apresentar o preço **após** a confirmação de médico é fiel — o Mapa
> (ETAPA 2) manda enviar o texto oficial do curso, que inclui o preço.

## 2. Mudanças propostas (por arquivo)

### `app/core/flow.py`
1. **Gap A** — ampliar a resposta-direta antes de qualificar (C1):
   - Acrescentar a `_eh_pergunta_informativa` os gatilhos gerais: `quais`, `qual curso`,
     `sobre o curso`, `me fala`, `informacoes`/`informações`, `detalhes`, `quero saber`,
     `o que e/é`, `como funciona`, `tem curso`.
   - Em `_handle_curso_online`, quando `eh_medico is None` e a mensagem for pergunta
     (`_eh_pergunta`), responder via `responder.generate` (grounded) — não disparar o
     gate médico. Manter a qualificação como gate apenas do **fechamento/link**.
   - Teste: "quais são os cursos online", "me fala sobre o curso" → respondem sem
     perguntar "é médico?"; "quero o link" → aí sim qualifica.
2. **Gap B** — qualificação fiel por caminho:
   - Adicionar a `_T` as chaves `qualif_medico_c1` e `qualif_medico_c2` com os textos
     verbatim do Mapa; usar a do caminho correspondente em vez do texto genérico.
3. **Gap C** — alinhar `generate_menu` (em `responder.py`) à abertura do Mapa
   ("Olá! Seja bem-vindo(a) à GoldIncision…"), mantendo as 6 opções.

### `app/core/responder.py`
- **Gap C** — ajustar o texto do menu (acolhimento + consultivo) preservando as 6 opções.
- (Tom) revisar `_SYSTEM_BASE`/prompts de dúvidas para reforçar reconhecimento do que o
  lead disse e uso do nome, sem afrouxar anti-alucinação.

## 3. Testes (pytest, FlowEngine real)
- C1: "quais são os cursos online" / "me fala do curso" (eh_medico=None) → **não**
  pergunta médico; chama o responder (dúvida). "quanto custa" idem (regressão).
- C1: fluxo até o fechamento ainda exige médico antes do link (sem regressão).
- Qualificação C1 e C2 retornam os textos específicos do Mapa.
- `ruff check app/ tests/` limpo; suíte inteira verde.

## 4. Validação real (obrigatória, agora em produção 1.2.0)
- WhatsApp com `#reset` por cenário (nº de teste autorizado):
  - Saudação → menu acolhedor.
  - "quais são os cursos online?" → responde com a Base **sem** perguntar médico de cara.
  - Conduz à qualificação de forma natural → apresentação verbatim → dúvidas → "Gostaria
    de receber o link?" → link no idioma.
  - Repetir 1 cenário em EN e ES.

## 5. Critérios de aceite
- Pergunta informativa (específica **ou** geral) é respondida sem requalificar.
- Qualificação usa o texto fiel ao Mapa por caminho; gateia só o fechamento.
- Menu/abertura com tom consultivo (Regras 11-13) preservando a estrutura.
- Suíte verde + lint limpo + validação real PT/EN/ES.
