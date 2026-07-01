# Checklist de prontidão para produção

Rode antes de subir o agente. Organizado por pilar. Cada item resolvido remove uma
fonte conhecida de alucinação ou de perda de sessão.

## Fluxo e estado (Pilares 1 e 2)

- [ ] O fluxo de atendimento é uma máquina de estados em código — o LLM não decide a
      próxima etapa em texto livre.
- [ ] Existe uma tabela de estado por conversa, chaveada por id estável do canal.
- [ ] Todo slot de qualificação tem um campo no estado (não fica "implícito" no chat).
- [ ] Antes de qualquer pergunta, o código checa se o slot já está preenchido.
- [ ] "Intenção já clara na 1ª mensagem → entra direto no fluxo" está implementado
      como condição, não como instrução no prompt.
- [ ] Mudança de assunto troca o nó/contexto imediatamente.

## Conteúdo e recuperação (Pilar 4)

- [ ] Preços, datas, condições e links são blocos canônicos enviados verbatim — o LLM
      nunca os redige.
- [ ] Os blocos são versionados (mudou o preço → nova versão).
- [ ] A recuperação é híbrida (semântica + textual) com reranking.
- [ ] Há um limiar de relevância; abaixo dele o agente se abstém e faz handoff.
- [ ] Chunks são por unidade semântica e têm metadados (produto, tipo, idioma).
- [ ] A busca pré-filtra por metadados antes da etapa vetorial.

## Verificação e saída (Pilares 5 e 6)

- [ ] Respostas geradas passam por um portão de verificação de fidelidade antes do envio.
- [ ] O portão é obrigatório para preço/data/elegibilidade/condição comercial.
- [ ] Falha na verificação cai em fallback (bloco canônico ou handoff), nunca envia.
- [ ] O LLM responde em JSON com contrato fixo, validado antes de agir.
- [ ] Temperatura baixa (0–0.2) nas etapas factuais.
- [ ] O idioma da resposta é checado contra o slot de idioma.
- [ ] Há bloqueio de saída para valores/condições fora dos blocos oficiais.

## Sessão longa (Pilares 3 e 7)

- [ ] Memória de curto prazo (janela) + resumo progressivo acima de um limite.
- [ ] Perfil de longo prazo só se houver necessidade real de personalização cross-sessão.
- [ ] Limite máximo de turnos por nó (anti-loop).
- [ ] Timeout de inatividade com reengajamento ou encerramento.
- [ ] Handoff humano explícito sempre que o usuário pedir.
- [ ] Estado persiste a reinício do sistema (execução durável) — a conversa retoma.

## Observabilidade (Pilar 8)

- [ ] Cada turno é logado: intenção, chunks + scores, ação, bloco/resposta, fonte.
- [ ] Existe um golden set (30–50 casos) com resposta esperada.
- [ ] O golden set roda a cada mudança de prompt ou base de conhecimento.
- [ ] Métricas acompanhadas: groundedness, abstenção correta, zero invenção de
      preço/data, fluxo correto (sem repetir slot, sem pular etapa).

## Teste de fogo (faça manualmente antes de subir)

- [ ] Pergunte o preço logo na 1ª mensagem — ele responde sem reiniciar o fluxo?
- [ ] Responda uma qualificação e volte ao assunto — ele evita repetir a pergunta?
- [ ] Pergunte algo que NÃO está na base — ele se abstém em vez de inventar?
- [ ] Force uma objeção comercial — ele usa o banco de objeções, não improvisa?
- [ ] Peça um humano — ele encaminha de imediato?
- [ ] Mande 30+ mensagens — ele mantém o contexto e não se contradiz?
