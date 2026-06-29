# Plano — Perfil do lead persistente + anti-redundância (não repetir perguntas)

> Para executar numa sessão limpa. Origem: feedback do operador (2026-06-29) —
> "incomodado com a segunda pergunta sobre ser médico; a informação já existe".
> Projeto em `/root/sdr-goldincision`. Stack em produção: `sdr-whatsapp_app`
> (`registry.todo-tips.com/sdr-whatsapp`), Docker Swarm.

## 1. Sintoma observado

Lead pergunta sobre HG360 presencial (Caminho 2), **confirma ser médico**, depois muda
de assunto para o **Sistema GoldIncision / Licenciamento** (Caminho 3) — e o bot
**pergunta de novo se é médico**. A informação já estava conhecida e persistida.

**Requisito do operador:** a IA não deve ser redundante/repetitiva. Se uma característica
do lead (médico, especialidade, experiência, idioma, interesse) já é conhecida, ela deve
ser **reusada**, não re-perguntada — em todos os caminhos e também nas dúvidas em texto
livre (LLM).

## 2. Estado atual (auditoria, com refs)

### 2.1 A "memória" do lead JÁ EXISTE (e é durável)
- `app/repository/models.py:46-77` — modelo `Contato` persiste em SQL (sobrevive entre
  tickets): `nome`, `idioma`, `eh_medico`, `especialidade`, `experiencia_corporal`,
  `produto_interesse`, `etapa_funil` (este último é só contador de tentativas em JSON,
  `{"et": "...", "n": N}`, não dados livres).
- `app/core/memory.py` — `SessionContext` (espelho em memória) carrega todos esses campos
  via `load_context` (≈ linhas 168-177). `update_qualification_variables` persiste de volta
  no `Contato` com whitelist: `idioma`, `eh_medico`, `especialidade`,
  `experiencia_corporal`, `produto_interesse`, `etapa_funil`.
- `resumo_rolante` (`SessaoConversa.resumo_rolante`, texto livre via LLM) existe e até é
  recuperado de tickets anteriores (`_recover_previous_summary`), **mas** só é gerado a
  partir de **50+ mensagens** (`_SUMMARIZE_THRESHOLD`, memory.py ≈ linha 33). Para um lead
  típico de WhatsApp, **nunca dispara** — logo, na prática o perfil real é só os campos
  estruturados do `Contato`.

### 2.2 O problema NÃO é falta de memória — é consulta inconsistente
- **C1 (Curso Online)** consulta antes de perguntar: `flow.py:1142` `if context.eh_medico is None:` → só então pergunta.
- **C2 (Presenciais)** idem: `flow.py:1303` `if context.eh_medico is None:` → só então pergunta.
- **C3 (Sistema/Licenciamento)** — **FURO**: `flow.py:955-964`, quando `objetivo == "incorporar"`,
  **sempre** gera a pergunta de médico e vai para `ETAPA_SISTEMA_LICENCIAMENTO`, **sem
  checar `context.eh_medico`**. É a origem direta da redundância.
- Há ainda re-qualificação no gate de fechamento do C1 (`flow.py:1096-1109`) que re-detecta
  médico sem checar `context.eh_medico` primeiro (risco menor, mas mesmo padrão).

### 2.3 O LLM não conhece o que já sabemos
- `app/core/responder.py` — `GroundedResponder.generate` monta o system prompt com base/
  caminho/etapa/conhecimento, mas **não injeta os fatos já conhecidos do lead** (médico,
  especialidade, nome, interesse). Então, nas fases de DÚVIDAS, o LLM pode re-perguntar
  coisas já respondidas — a redundância não é só dos caminhos determinísticos.

## 3. Objetivo

1. **Nunca re-perguntar** uma característica já conhecida — em qualquer caminho.
2. **Surfacar o perfil ao LLM**, para coerência também nas dúvidas em texto livre.
3. Tornar o **perfil acessível e extensível** (características/preferências), sem
   depender do resumo rolante de 50 mensagens.

## 4. Mudanças propostas (faseadas)

### Fase 1 — Fix imediato do C3 (quick win, sem migration)
Em `flow.py` `_handle_sistema_goldincision`, no ramo `objetivo == "incorporar"`
(`:955-964`), **consultar `context.eh_medico` antes de perguntar**, alinhando ao C1/C2:
- `context.eh_medico is True` → **pular a pergunta** e ir direto ao resumo do Licenciamento
  + abertura de dúvidas (a lógica que hoje vive no ramo `ETAPA_SISTEMA_LICENCIAMENTO` após
  confirmação). Reaproveitar via um helper interno (ex. `_abrir_licenciamento(context, updates)`)
  para não duplicar texto.
- `context.eh_medico is False` → ir direto ao handoff de Franquia (não médico), como já faz
  o ramo de dúvidas.
- `context.eh_medico is None` → comportamento atual (perguntar).

**Critério:** lead que já é médico e entra no C3 NÃO recebe a pergunta de médico.

### Fase 2 — Guarda central "não pergunte o que já sabemos" (sem migration)
Criar um helper único de qualificação que todos os caminhos usam, eliminando o padrão
copiado/divergente:
- `_ja_conhecido(context, campo) -> bool` e/ou `_qualificar_ou_pular(...)` que, dado o campo
  (`eh_medico`, `especialidade`, `experiencia_corporal`, `idioma`, `produto_interesse`),
  retorna o valor já conhecido sem perguntar, ou conduz à pergunta só quando `None`.
- Aplicar em C1 (incl. gate de fechamento `:1096-1109`), C2 e C3, removendo as checagens
  ad-hoc. Mantém detecção/atualização quando o lead realmente responde.

**Critério:** auditoria garante que toda pergunta de qualificação passa pela guarda.

### Fase 3 — Injeção do "perfil conhecido" no prompt do LLM
- Montar um bloco compacto `FATOS JÁ CONHECIDOS DO LEAD` a partir do `SessionContext`
  (nome, médico sim/não, especialidade, experiência corporal, interesse/produto, idioma,
  e — se existir — resumo rolante). Função pura, ex. `perfil_conhecido(context) -> str`
  em `memory.py` ou um util.
- Passar esse bloco a `GroundedResponder.generate(..., known_facts=...)` e inseri-lo no
  `system_content` com instrução: *"Estes fatos já são conhecidos — NÃO pergunte novamente;
  use-os para personalizar. Pergunte apenas o que ainda não está aqui."*
- Threading no `flow.py` em todas as chamadas `self._responder.generate(...)`.

**Critério:** nas dúvidas, o LLM cumprimenta usando o que sabe e não re-pergunta médico/
especialidade já conhecidos.

### Fase 4 — Perfil incremental e extensível (decisão de arquitetura)
Para "características e preferências" além da qualificação fixa (ex.: "tem clínica própria",
"prefere São Paulo", "foco estético vs. reconstrutivo", objeções recorrentes), duas opções:

- **4a (recomendada, leve, requer migration):** adicionar `Contato.perfil` como `JSONB`
  (dict livre) + helper `merge_perfil(context, novos_fatos)` que acumula incrementalmente a
  cada turno (sem LLM, a partir dos detectores já existentes e de chaves novas conforme
  necessário). Persistir via `update_qualification_variables` (adicionar `perfil` à
  whitelist). Injetar no prompt junto da Fase 3. Migration Alembic simples (1 coluna
  `JSONB DEFAULT '{}'`).
- **4b (sem migration):** baixar o `_SUMMARIZE_THRESHOLD` (ex.: 50 → ~6-8 mensagens) e/ou
  gerar o resumo rolante sob demanda ao trocar de caminho, para que o resumo em texto livre
  realmente exista para leads curtos. Mais barato de implementar, porém o perfil fica como
  texto não estruturado (menos confiável para decisões determinísticas) e custa chamadas LLM.

> Recomendação: **4a** para dados estruturados/decisões + manter 4b opcional só para o
> contexto narrativo do LLM. Confirmar com o operador antes de implementar a Fase 4
> (impacta schema/DB). Fases 1-3 não exigem migration e já resolvem o incômodo relatado.

## 5. Testes (pytest)
- **flow C3 (Fase 1):** lead com `eh_medico=True` entrando no C3 (`objetivo=incorporar`) →
  resposta é o resumo do Licenciamento + dúvidas, **sem** a pergunta de médico; com
  `eh_medico=False` → handoff Franquia; com `None` → pergunta (comportamento atual).
- **guarda central (Fase 2):** para cada caminho, característica já conhecida não é
  re-perguntada; característica `None` ainda é perguntada.
- **perfil no prompt (Fase 3):** `GroundedResponder.generate` recebe `known_facts` e o
  system prompt contém os fatos + a instrução de não re-perguntar (assert no conteúdo
  passado ao `chat_reasoning`).
- **perfil incremental (Fase 4a, se aprovada):** `merge_perfil` acumula chaves; persistência
  inclui `perfil`; `load_context` reidrata; migration aplica/reverte limpa.
- `ruff` limpo; suíte inteira verde.

## 6. Validação real (WhatsApp, número de teste, com `#reset`)
- Reproduzir: HG360 presencial → confirmar médico → mudar para Sistema/Licenciamento →
  **conferir que NÃO pergunta médico de novo** e segue direto ao resumo + dúvidas.
- Conferir que o LLM, nas dúvidas, usa nome/contexto sem repetir perguntas já respondidas.
- Repetir 1 caminho em EN/ES.

## 7. Deploy
- Build/push/`service update` de nova tag (ex.: 1.8.0). Se a Fase 4a entrar, rodar a
  migration Alembic no startup/manualmente antes do switch de tráfego.

## 8. Critérios de aceite
- Nenhuma pergunta de qualificação já conhecida é repetida, em qualquer caminho.
- O LLM recebe o perfil conhecido e não re-pergunta nas dúvidas.
- (Fase 4a) Características/preferências arbitrárias são acumuladas e reusadas entre tickets.
- Suíte verde + lint limpo + validação real confirmada.

## 9. Pendências / decisões
- **Fase 4 (schema):** aprovar 4a (coluna `JSONB perfil` + migration) vs. 4b (resumo rolante
  antecipado, sem migration). Decisão do operador.
- **Escopo de "preferências":** quais características além da qualificação fixa interessam
  capturar (clínica própria, cidade, foco clínico, orçamento, objeções) — definir as chaves.
</content>
</invoke>
