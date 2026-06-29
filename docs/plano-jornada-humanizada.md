# Plano — Jornada Mais Fluida e Humanizada (fiel ao Mapa Mestre)

> **Para executar numa sessão limpa.** Documento autocontido: objetivo, estado
> atual (com refs de arquivo/linha), desenho-alvo por caminho, mudanças de
> código, correção de bugs, testes e deploy. Projeto em `/root/sdr-goldincision`.
> Stack em produção: `sdr-whatsapp` (imagem `registry.todo-tips.com/sdr-whatsapp`).

## 0. Contexto e princípios

- **Fonte da verdade**: `knowledge_base/documentos_agente/MAPA MESTRE DO ATENDIMENTO.docx`
  e `REGRAS GERAIS DO AGENTE COMERCIAL GOLDINCISION.docx`. A jornada deve segui-los
  **fielmente em estrutura e ordem**.
- **Objetivo duplo**: (A) implementar a jornada do Mapa Mestre que hoje está
  simplificada/divergente; (B) deixar a entrega mais **fluida e humanizada** —
  reconhecer o que o lead disse, não repetir perguntas, transições suaves, tom
  consultivo premium. Humanizar a ENTREGA, sem alterar a ESTRUTURA.
- **Anti-alucinação preservada** (Regra 7/Princípio II): responder só com a Base
  Oficial; apresentações verbatim; objeções só do Banco Oficial; lacuna → handoff.
- **Idiomas**: PT/EN/ES (base já multilíngue: apresentações, objeções, FAQ, links).
- **DECISÃO DO OPERADOR (2026-06-29) — REVERTE a anterior**: seguir o Mapa Mestre
  na regra de pergunta direta — *"Se o usuário perguntar preço/conteúdo/duração/
  certificado direto, responder normalmente, sem reiniciar o fluxo"*. Ou seja, a
  qualificação "é médico?" **deixa de ser pré-requisito bloqueante** para responder
  uma pergunta informativa direta. (Substitui a decisão prévia de "qualificar antes
  do preço".)

## 1. Estado atual (auditoria) — o que mudar

Arquivos: `app/core/flow.py` (~1024 l.), `app/core/responder.py` (~350 l.),
`app/core/intent.py`, `app/core/memory.py`.

Gaps e bugs confirmados:

1. **Qualificação bloqueante** (flow.py ~422 e ~529): `if eh_medico is None: pergunta e retorna` — bloqueia QUALQUER resposta de conteúdo até qualificar. Contraria o Mapa Mestre (pergunta direta). **Mudar.**
2. **Caminho 3 (Sistema GoldIncision) stateless** (`_handle_sistema_goldincision` ~364-399): sem ETAPA 1 (explicar "não é curso"), sem ETAPA 2 (objetivo: incorporar/abrir/não sei), sem sub-caminhos (Licenciamento/Franquia/diagnóstico). Tudo delegado ao LLM. **Implementar etapas.**
3. **Caminho 4 (Aluno)** (`_handle_aluno_suporte` ~291-328): sem o submenu de 6 opções do Mapa Mestre; faz handoff direto. **Adicionar submenu → encaminhamento.**
4. **"Trilha" ausente**: quando indica HG Módulo 1, NÃO apresenta HG Módulo 1 + HG360 SP juntos (Mapa Mestre "REGRAS IMPORTANTES"). **Implementar.**
5. **Fechamento não-determinístico**: Caminho 1 não tem etapa de "Gostaria de receber o link?" → envio do link; Caminho 2 não tem "Gostaria que eu encaminhasse para um consultor?" determinístico. Hoje depende do LLM. **Estruturar etapas de fechamento.**
6. **BUG — colisão de índices de prompt** (flow.py ~619-623 + responder.py): sub-cursos mapeados para caminhos numéricos 2/3/4, mas `_CAMINHO_PROMPTS[3]`=Sistema GoldIncision e `[4]`=Aluno. Resultado: **HG360 SP recebe o prompt de "Sistema GoldIncision" e HG360 BCN o de "Aluno suporte"**. Os prompts `_SYSTEM_CAMINHO_2_HG_*` existem mas são código morto. **Corrigir o mapeamento.**
7. **Código morto**: `ETAPA_OBJECAO`, `ETAPA_LINK` declaradas e não usadas. **Usar (link/fechamento) ou remover.**
8. **Parsing frágil** (`_detectar_confirmacao` ~905): positivos curtos ("sou","tenho","crm") → falso positivo; sem contador de tentativas → repete a mesma pergunta para sempre se não reconhecer a resposta. **Endurecer + fallback após N tentativas → handoff/ pergunta reformulada.**
9. **Reclassificação a cada turno** (flow.py ~162) + reset de `etapa=None` na troca de caminho (~198): pode reiniciar jornada por classificação equivocada. **Tornar a troca de caminho conservadora (só com alta confiança / intenção explícita).**
10. **Tom**: prompts curtos e diretivos; guardas determinísticas forçam 1 pergunta de qualificação por turno antes de conteúdo → robótico. **Humanizar prompts + reduzir travas.**

## 2. Desenho-alvo da jornada (fiel ao Mapa Mestre) + humanização

Princípio de fluidez transversal (vale para todos os caminhos):
- **Reconhecer antes de perguntar**: o agente confirma o que entendeu ("Que ótimo que tem interesse no HG360 presencial!") antes da próxima pergunta.
- **Nunca repetir o que já foi respondido**: usar as variáveis do funil persistidas; se o lead já disse que é médico, não perguntar de novo.
- **Pergunta direta = resposta direta**: se a 1ª mensagem (ou qualquer mensagem) traz uma pergunta informativa (preço/conteúdo/duração/certificado/turmas), responder da Base Oficial **na hora**, e só então conduzir a qualificação de forma natural ("Posso já te enviar o link de inscrição — só confirmo: você é médico com registro ativo?").
- **Uma pergunta por mensagem** (exceto menus). Emojis moderados, tom premium.
- **Usar o nome do lead** quando disponível (`Contato.nome`/payload) — ex.: "Perfeito, Dr. Paulo!".
- **Transições suaves entre etapas** (sem "pulos" secos).

### Caminho 1 — Curso Online (etapas: identificação → [resposta direta] → qualificação → apresentação → dúvidas → fechamento → link)
- Se a mensagem é pergunta direta → responder da Base; depois oferecer continuidade.
- Qualificação "é médico?" obrigatória ANTES de **fechar** (enviar link), não antes de responder dúvidas. Não-médico → agradecer + encerrar (mensagem cordial).
- Apresentação oficial **verbatim**; abrir para dúvidas.
- Fechamento: "Gostaria de receber o link para realizar sua inscrição?" → SIM → enviar link no idioma (já em `curso_link`). NÃO → agradecer e se colocar à disposição.
- Etapas a usar: `ETAPA_APRESENTACAO`, `ETAPA_DUVIDAS` (nova), `ETAPA_FECHAMENTO` (nova), `ETAPA_LINK` (passar a usar).

### Caminho 2 — Presenciais (qualif médico → experiência → especialidade → trilha/HG360 turma → apresentação → dúvidas → encaminhar consultor)
- Qualif médico (não-médico → encerra cordial).
- Experiência em Harmonização Corporal? ("só facial" = NÃO — já tratado, manter).
- Sem experiência → especialidade (Derm/Plástica/Vascular → HG360; outra/nenhuma → HG Módulo 1).
- **Trilha (REGRA IMPORTANTE)**: quando o entendimento for HG Módulo 1, **apresentar HG Módulo 1 + HG360 SP juntos** (a "trilha recomendada"). Respeitar a decisão do médico se ele preferir outro, desde que elegível.
- Elegível ao HG360 → escolher turma (SP 28-30/08/2026 ou Barcelona 24-25/07/2026) → apresentação da turma + objeções do HG360.
- Fechamento: "Gostaria que eu encaminhasse seu interesse para um consultor da GoldIncision dar continuidade à sua inscrição?" → SIM → **handoff determinístico** ao consultor.

### Caminho 3 — Sistema GoldIncision (ETAPA 1 → ETAPA 2 → sub-caminhos) — hoje ausente
- **ETAPA 1 — Apresentação do sistema**: explicar que a técnica **não é curso avulso**; existem 2 programas: Licenciamento (médicos, internacional) e Franquia (médicos/investidores). Texto fiel ao Mapa Mestre.
- **ETAPA 2 — Objetivo**: menu de 3 opções (1 incorporar à clínica / 2 abrir clínica completa / 3 não tenho certeza).
- **Sub-caminho 1 (incorporar)**: qualificar médico → SIM: apresentação do **Licenciamento Internacional** (já na base, pt/es/en) + dúvidas → convidar para **reunião com especialista** (handoff). NÃO: explicar que Licenciamento é exclusivo p/ médicos e oferecer migrar p/ Franquia.
- **Sub-caminho 2 (abrir clínica)**: perguntar "médico ou investidor?"; em qualquer caso → apresentação da **Franquia** + dúvidas → convidar reunião (handoff). *(Obs: conteúdo de Franquia ainda não existe na base — ver §6 "Pendências".)*
- **Sub-caminho 3 (não tenho certeza)**: diagnóstico (clínica própria vs nova; Brasil vs exterior; médico vs investidor) → recomendar modelo → conduzir ao especialista.
- Objetivo do caminho: **qualificar e marcar reunião**, nunca "vender"/fechar.
- Novas etapas: `ETAPA_SISTEMA_OBJETIVO`, `ETAPA_SISTEMA_LICENCIAMENTO`, `ETAPA_SISTEMA_FRANQUIA`, `ETAPA_SISTEMA_DIAGNOSTICO`.

### Caminho 4 — Aluno/suporte (submenu 6 opções → encaminhamento) — hoje ausente
- ETAPA 1 — submenu: 1 Plataforma/acesso · 2 Certificado · 3 Grupo de suporte técnico · 4 Pagamento/inscrição · 5 Dúvidas sobre o curso · 6 Outro.
- ETAPA 2 — independentemente da opção: mensagem de encaminhamento (texto do Mapa Mestre) + **handoff** à equipe (registrar a opção escolhida no handoff/observabilidade).
- Nova etapa: `ETAPA_ALUNO_MENU`.

### Caminho 5 — Paciente modelo (Nídia) — manter
- Texto oficial + contato da Nídia (+55 21 97423-9844); `action="end"`. Já fiel.

### Caminho 6 — Outro assunto — manter (handoff cordial), revisar tom.

## 3. Mudanças de código (por arquivo)

### `app/core/flow.py`
- **Etapas**: adicionar constantes novas (DUVIDAS, FECHAMENTO, SISTEMA_OBJETIVO, SISTEMA_LICENCIAMENTO, SISTEMA_FRANQUIA, SISTEMA_DIAGNOSTICO, ALUNO_MENU); passar a usar `ETAPA_LINK`; remover `ETAPA_OBJECAO` se não usada.
- **Resposta direta antes de qualificar (C1/C2)**: introduzir detecção de "pergunta informativa direta" (preço/conteúdo/duração/certificado/turma/datas). Se detectada e ainda não qualificado, **responder da Base** (via `responder.generate` com o conhecimento do caminho) em vez de travar na pergunta de qualificação; manter a qualificação como gate apenas para o **fechamento** (envio de link / encaminhamento). Implementar `_eh_pergunta_informativa(user_message)` (heurística +, se preciso, sinal do intent).
- **Caminho 1**: estruturar etapas apresentação → dúvidas → fechamento (oferecer link) → link. Tornar o envio do link determinístico quando o lead aceitar.
- **Caminho 2**: implementar a **trilha** (HG Módulo 1 + HG360 SP juntos) no ramo Módulo 1; encaminhamento ao consultor determinístico no fechamento.
- **Caminho 3**: reescrever `_handle_sistema_goldincision` como máquina de etapas (ETAPA 1 → ETAPA 2 → sub-caminhos), com textos fiéis ao Mapa Mestre e handoff (reunião) determinístico no fim de cada sub-caminho.
- **Caminho 4**: implementar submenu + encaminhamento (handoff) com a opção registrada.
- **Parsing robusto**: endurecer `_detectar_confirmacao`/`_detectar_*` (remover positivos ambíguos isolados; exigir tokens mais específicos); adicionar **contador de tentativas** por etapa (ex.: em Redis `estado:{chamado}` ou no contexto) — após 2 respostas não reconhecidas, reformular a pergunta de forma mais clara e, na 3ª, encaminhar a humano em vez de repetir infinitamente.
- **Troca de caminho conservadora**: só redirecionar quando a intenção for explícita/alta confiança (usar o score do intent); não resetar `etapa` se a mudança vier de classificação de baixa confiança.

### `app/core/responder.py`
- **Corrigir o mapeamento de prompts** (BUG): sub-cursos HG Módulo 1 / HG360 SP / HG360 BCN devem usar os prompts `_SYSTEM_CAMINHO_2_HG_*` (hoje código morto) — eliminar a colisão com `_SYSTEM_CAMINHO_3/4`. Usar chaves não-numéricas (ex.: por slug) no dispatch.
- **Humanizar `_SYSTEM_BASE`** preservando as regras de anti-alucinação: acrescentar diretrizes de tom (reconhecer o que o lead disse; usar o nome quando houver; transição suave; calor humano sem perder o premium; nunca repetir pergunta já respondida; pergunta direta → resposta direta). Manter "1 pergunta por mensagem", emojis moderados, respostas curtas.
- **Prompts de fechamento**: incluir instrução para a etapa de fechamento (oferta de link / convite ao consultor / reunião) de forma calorosa.
- **Prompts dos sub-caminhos do Caminho 3** (Licenciamento/Franquia/diagnóstico) e do submenu do Caminho 4.

### `app/core/intent.py`
- Expor/retornar um **score/confiança** utilizável pelo flow para a troca de caminho conservadora.
- Sinalizar "pergunta informativa direta" (opcional) para alimentar `_eh_pergunta_informativa`.

### `app/core/memory.py`
- Garantir persistência das variáveis novas necessárias (ex.: objetivo do Caminho 3, opção do submenu do Caminho 4, contador de tentativas). Se forem efêmeras, usar Redis `estado:{chamado}`; se devem durar, avaliar coluna. Preferir Redis para contadores de tentativa (sem migration).

### Migrations
- Provavelmente **nenhuma nova tabela** é necessária (estado de etapa já existe em `ticket.etapa_mapa_mestre`; variáveis em `contato`; efêmeros em Redis). Se decidir persistir "objetivo do sistema" em coluna, criar migration; caso contrário, manter em Redis/`etapa`.

## 4. Diretrizes de humanização (resumo aplicável aos prompts)
1. Abrir reconhecendo a intenção/última fala do lead.
2. Usar o nome quando disponível ("Dr(a). <nome>").
3. Uma pergunta por mensagem; nunca re-perguntar o já respondido.
4. Pergunta direta → resposta direta (depois conduz).
5. Transições suaves ("Perfeito! Agora, para eu te indicar a melhor turma…").
6. Tom cordial/elegante/premium; emojis moderados; respostas curtas.
7. Em handoff, explicar o porquê e o próximo passo ("vou te conectar com um especialista que cuida disso pessoalmente").
8. Sem jargão de bot; sem repetir o menu se a intenção já está clara.

## 5. Testes (obrigatórios antes do deploy)
- **Unit** (pytest): para cada caminho/etapa nova, com `FlowEngine` real (sem MockFlowEngine):
  - C1: pergunta direta de preço → responde sem travar; depois qualifica; fechamento oferece link; SIM → envia link no idioma.
  - C2: médico→experiência→especialidade→trilha (Módulo 1 + HG360 SP juntos); HG360→escolha turma→apresentação→encaminhar consultor (handoff).
  - C3: ETAPA 1 (não é curso) → ETAPA 2 (objetivo) → cada sub-caminho → handoff/reunião.
  - C4: submenu 6 opções → encaminhamento (handoff) com opção registrada.
  - C5: Nídia (end). C6: outro (handoff).
  - Robustez: resposta não reconhecida → reformula → 3ª vez → handoff (não repete infinito).
  - Não-repetição: já-médico não re-pergunta ao trocar de caminho.
  - Multilíngue: PT/EN/ES por caminho-chave.
- **Lint**: `ruff check app/ tests/` limpo.
- **Sem regressão**: suíte inteira verde (hoje 254 testes).

## 6. Deploy e validação real
1. `cd /root/sdr-goldincision && python3 -m pytest -q && python3 -m ruff check app/ tests/`
2. `git add -A && git commit -m "feat(jornada): humanização fiel ao Mapa Mestre (C1-C6)"`
3. Build/push nova tag (ex.: 1.2.0):
   `docker build -t registry.todo-tips.com/sdr-whatsapp:1.2.0 -t registry.todo-tips.com/sdr-whatsapp:latest . && docker push ...:1.2.0 && docker push ...:latest`
4. `docker service update --image registry.todo-tips.com/sdr-whatsapp:1.2.0 --force --with-registry-auth sdr-whatsapp_app` (ou `docker stack deploy` se mudar env). Se houver migration nova, ela roda no startup.
5. **Validação real por WhatsApp** usando `#reset` antes de cada cenário (número de teste 5511967296849):
   - C1 PT (pergunta direta de preço → fluxo → link), C2 (trilha + turma + consultor), C3 (sistema → objetivo → licenciamento → reunião), C4 (submenu → encaminhamento), C5 (Nídia), C6.
   - Repetir 1 cenário em EN e ES.
   - Conferir no banco (sessão/variáveis) e ausência de erro de envio nos logs.

## 7. Pendências / dependências
- **Franquia sem conteúdo**: o Caminho 3 sub-caminho "abrir clínica/Franquia" só fica completo quando o operador fornecer o material de Franquia (texto/docx) para seed. Até lá, esse sub-caminho deve **encaminhar a humano** explicando que um especialista detalhará a Franquia (sem inventar).
- **Decisão de produto já tomada**: pergunta direta → resposta direta (seguir Mapa Mestre).
- **Rotacionar a chave OpenAI** (exposta em sessão anterior) — recomendável.

## 8. Critérios de aceite
- Os 6 caminhos seguem a estrutura/ordem do Mapa Mestre (incluindo ETAPA 1/2 do C3, submenu do C4, trilha do C2, fechamento do C1).
- Pergunta direta é respondida sem requalificar; qualificação só gateia o fechamento.
- Sem repetição de perguntas já respondidas; resposta não reconhecida não entra em loop.
- Tom humanizado (reconhecimento, nome, transição) preservando anti-alucinação.
- BUG de colisão de prompts corrigido; sub-cursos usam o prompt correto.
- Suíte verde + lint limpo; validação real PT/EN/ES por WhatsApp via `#reset`.
