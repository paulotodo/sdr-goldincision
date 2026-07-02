"""
Testes do detector centralizado de troca de caminho mid-jornada (US1,
FASE 2, tasks 2.2.1-2.2.11, 2.3.1-2.3.4).

FlowEngine REAL (via `StubFlowEngine`/`engine()` de `tests/test_flow.py`) —
mock SOMENTE do client OpenAI (fallback agentico do `SlotExtractor`), nunca
do FlowEngine.

Cobertura:
- 2.2.4 quickstart Cenario 1: marcador explicito + candidato claro -> despacha
  direto (sem pergunta intermediaria), preserva perfil, zera tentativas da
  etapa abandonada.
- 2.2.5 quickstart Cenario 9: candidato claro SEM marcador -> confirmacao
  pendente; 2a confirma -> despacha; 2b nega/nao-reconhece -> pendente
  limpo, tentativa conta contra a etapa ORIGINAL (nao uma nova).
- 2.2.6 quickstart Cenario 2: candidato ambiguo entre EXATAMENTE 2 caminhos
  -> UMA pergunta de desambiguacao (nunca o menu completo); turno seguinte
  escolhe -> despacha.
- 2.2.7: sem marcador/candidato nenhum -> cai no comportamento existente
  (reformulacao normal).
- 2.2.9 (regressao, Decision 4): `_ETAPAS_AGUARDANDO_RESPOSTA` (fix #9)
  permanece intocado na classificacao GLOBAL -- o novo detector e mecanismo
  distinto, so alcancado apos o resolver especifico da etapa falhar.
- 2.2.10 quickstart Cenario 10 (regressao, Decision 5): overflow-resume tem
  precedencia total -- o detector nunca e alcancado enquanto ha overflow
  pendente.
- 2.2.11 quickstart Cenario 4 (regressao, FR-009): resposta legitima e
  direta a uma pergunta pendente NUNCA e lida como troca de caminho.
- 2.3.1: nenhum campo de qualificacao e resetado/re-perguntado apos a troca.
- 2.3.2: `_tent_clear` na etapa abandonada durante a troca, sem tocar
  orcamento de turnos.
- 2.3.3 quickstart Cenario 12 (regressao, FR-021): despacho da troca sempre
  entra pela etapa inicial do caminho-alvo -- NUNCA retoma etapa de visita
  anterior (mesmo quando o nome da etapa coincide entre caminhos, ex.:
  ETAPA_QUALIF_MEDICO e compartilhado por Caminho 1 e Caminho 2).
- 2.3.4 quickstart Cenario 11: correcao para o MESMO caminho ja ativo e
  no-op (sem troca, sem pendencia, sem efeito colateral).

FASE 6 (task 6.1.2 — decisao de estrutura): os testes de unidade do
lexico/detector para os cenarios 1-13 do quickstart (golden set, task 6.1.1)
sao ADICIONADOS a este arquivo em vez de criar `tests/test_troca_caminho.py`
novo (alternativa cogitada em `plan.md` §Structure Decision). Justificativa:
este arquivo ja e o dedicado a testes de unidade do detector/lexico desde a
FASE 2 (mesmo escopo tematico), ja importa `engine`/`make_context` de
`tests/test_flow.py` (StubFlowEngine — FlowEngine REAL) e ja cobre boa parte
dos cenarios 1/2/4/9/10/11/12 individualmente; criar um arquivo paralelo
duplicaria fixtures e imports sem necessidade (dec-013, onda-010).
Cobertura adicional desta FASE (task 6.1.3):
- anti-regressao fix #9 em nivel de unidade (alem do golden set): resposta
  legitima de qualificacao ("sou medico") e resposta numerica de menu interno
  ("1" em ETAPA_SISTEMA_OBJETIVO) nunca alcancam o detector.
- variantes EN/ES do Cenario 1 (alem do EN ja existente em
  `test_marcador_explicito_multilingue_en`) e do Cenario 5 (menu texto livre).
"""
from __future__ import annotations

import pytest

from app.core.flow import (
    ETAPA_QUALIF_ESPECIALIDADE,
    ETAPA_QUALIF_MEDICO,
    ETAPA_SISTEMA_LICENCIAMENTO,
    ETAPA_SISTEMA_OBJETIVO,
    CaminhoMapaMestre,
    _detectar_confirmacao,
    _nome_caminho,
    _t,
    _tent_count,
)
from app.core.intent import ClassificacaoIntencao, Idioma
from tests.test_flow import engine, make_context

# ---------------------------------------------------------------------------
# 2.2.4 -- marcador explicito + candidato claro -> despacho direto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_marcador_explicito_despacha_direto_sem_pergunta_intermediaria():
    """Quickstart Cenario 1: 'na verdade quero o curso online' -- marcador +
    lexico especifico reconhecidos -- despacha direto (sem troca_pendente)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_QUALIF_ESPECIALIDADE,
        eh_medico=True, experiencia_corporal=False, etapa_funil='{"et": "qualif_especialidade", "n": 2}',
    )

    r = await eng.process(1, "na verdade quero o curso online", ctx)

    assert r.caminho == CaminhoMapaMestre.CURSO_ONLINE_HG
    assert ctx.caminho == CaminhoMapaMestre.CURSO_ONLINE_HG
    assert ctx.troca_caminho_pendente is None
    assert _nome_caminho(CaminhoMapaMestre.CURSO_ONLINE_HG, "pt") in r.response_text
    # FR-007: tentativas da etapa abandonada zeradas.
    assert ctx.etapa_funil is None
    assert _tent_count(ctx, ETAPA_QUALIF_ESPECIALIDADE) == 0
    # FASE 5 (US4/FR-017): FlowResult carrega origem/destino/metodo da troca
    # para observabilidade aditiva (log_turno.troca_caminho_*). E-1: origem
    # e destino sempre juntos; E-2: confianca None no caminho deterministico.
    assert r.troca_caminho_origem == int(CaminhoMapaMestre.CURSOS_PRESENCIAIS)
    assert r.troca_caminho_destino == int(CaminhoMapaMestre.CURSO_ONLINE_HG)
    assert r.troca_metodo == "deterministico"
    assert r.troca_confianca is None
    assert r.reformulacao_variante is None  # troca detectada, nao reformulacao


@pytest.mark.asyncio
async def test_marcador_explicito_multilingue_en():
    """Quickstart Cenario 13: marcador EN ('actually') + produto EN.

    `engine(idioma=Idioma.EN)` faz o `MockIntent.classify()` retornar EN --
    sem isso, `_process_core` (passo 1) sobrescreveria `context.idioma` de
    volta para PT (mismatch classificador vs `make_context`), fazendo o
    marcador EN nao ser reconhecido (`_MARCADORES_CORRECAO["pt"]`)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA, idioma=Idioma.EN)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSO_ONLINE_HG, etapa=ETAPA_QUALIF_MEDICO,
        idioma="en",
    )

    r = await eng.process(1, "actually I want the presential courses", ctx)

    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS
    assert ctx.troca_caminho_pendente is None


# ---------------------------------------------------------------------------
# 2.2.5 -- candidato claro SEM marcador -> confirmacao pendente
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sem_marcador_seta_confirmacao_pendente():
    """Quickstart Cenario 9 passo 1: intencao clara SEM marcador explicito
    -- NAO troca silenciosamente, seta troca_pendente tipo=confirmacao."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_QUALIF_ESPECIALIDADE,
        eh_medico=True, experiencia_corporal=False,
    )

    r = await eng.process(1, "quero saber mais sobre o curso online", ctx)

    assert r.action == "continue"
    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS  # inalterado
    assert r.etapa == ETAPA_QUALIF_ESPECIALIDADE  # inalterado
    assert ctx.troca_caminho_pendente == {
        "destinos": [int(CaminhoMapaMestre.CURSO_ONLINE_HG)],
        "origem": int(CaminhoMapaMestre.CURSOS_PRESENCIAIS),
        "metodo": "deterministico", "confianca": None, "tipo": "confirmacao",
    }
    assert _nome_caminho(CaminhoMapaMestre.CURSO_ONLINE_HG, "pt") in r.response_text


@pytest.mark.asyncio
async def test_confirmacao_pendente_confirmada_despacha():
    """Quickstart Cenario 9 passo 2a: lead confirma (sim) -> despacha para o
    candidato; troca_pendente limpo."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_QUALIF_ESPECIALIDADE,
        eh_medico=True, experiencia_corporal=False,
    )
    ctx.troca_caminho_pendente = {
        "destinos": [int(CaminhoMapaMestre.CURSO_ONLINE_HG)],
        "origem": int(CaminhoMapaMestre.CURSOS_PRESENCIAIS),
        "metodo": "deterministico", "confianca": None, "tipo": "confirmacao",
    }

    r = await eng.process(1, "sim", ctx)

    assert r.caminho == CaminhoMapaMestre.CURSO_ONLINE_HG
    assert ctx.troca_caminho_pendente is None


@pytest.mark.asyncio
async def test_confirmacao_pendente_negada_conta_tentativa_da_pergunta_original():
    """Quickstart Cenario 9 passo 2b: lead nega -- troca_pendente limpo; a
    tentativa conta contra a pergunta ORIGINAL pendente (nao uma nova),
    etapa original inalterada (clarify Q1/dec-007)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_QUALIF_ESPECIALIDADE,
        eh_medico=True, experiencia_corporal=False,
    )
    ctx.troca_caminho_pendente = {
        "destinos": [int(CaminhoMapaMestre.CURSO_ONLINE_HG)],
        "origem": int(CaminhoMapaMestre.CURSOS_PRESENCIAIS),
        "metodo": "deterministico", "confianca": None, "tipo": "confirmacao",
    }

    r = await eng.process(1, "não", ctx)

    assert ctx.troca_caminho_pendente is None
    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS  # nao trocou
    assert r.etapa == ETAPA_QUALIF_ESPECIALIDADE  # etapa ORIGINAL inalterada
    # Exatamente 1 tentativa contra a pergunta ORIGINAL (nunca 0 nem 2).
    assert _tent_count(ctx, ETAPA_QUALIF_ESPECIALIDADE) == 1


@pytest.mark.asyncio
async def test_confirmacao_pendente_nao_reconhecida_tambem_conta_1_tentativa():
    """Mensagem nao reconhecida (nem sim, nem nao) enquanto ha confirmacao
    pendente -- mesmo tratamento de negacao: limpa e conta 1 tentativa da
    pergunta original (nunca duplicada)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_QUALIF_ESPECIALIDADE,
        eh_medico=True, experiencia_corporal=False,
    )
    ctx.troca_caminho_pendente = {
        "destinos": [int(CaminhoMapaMestre.CURSO_ONLINE_HG)],
        "origem": int(CaminhoMapaMestre.CURSOS_PRESENCIAIS),
        "metodo": "deterministico", "confianca": None, "tipo": "confirmacao",
    }

    r = await eng.process(1, "batata frita", ctx)

    assert ctx.troca_caminho_pendente is None
    assert r.etapa == ETAPA_QUALIF_ESPECIALIDADE
    assert _tent_count(ctx, ETAPA_QUALIF_ESPECIALIDADE) == 1


# ---------------------------------------------------------------------------
# 2.2.6 -- candidato ambiguo entre EXATAMENTE 2 caminhos -> desambiguacao
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_termo_generico_ambiguo_seta_desambiguacao_pendente():
    """Quickstart Cenario 2: termo generico demais ('curso', sem indicar
    modalidade) -- compativel com Caminho 1 E Caminho 2 -- UMA pergunta
    direta de desambiguacao, NUNCA o menu completo de 6 opcoes (FR-008)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.SISTEMA_GOLDINCISION,
        etapa=ETAPA_SISTEMA_LICENCIAMENTO,
    )

    r = await eng.process(1, "curso", ctx)

    assert r.action == "continue"
    assert r.caminho == CaminhoMapaMestre.SISTEMA_GOLDINCISION  # inalterado
    assert ctx.troca_caminho_pendente == {
        "destinos": [
            int(CaminhoMapaMestre.CURSO_ONLINE_HG),
            int(CaminhoMapaMestre.CURSOS_PRESENCIAIS),
        ],
        "origem": int(CaminhoMapaMestre.SISTEMA_GOLDINCISION),
        "metodo": "deterministico", "confianca": None, "tipo": "desambiguacao",
    }
    # NUNCA reapresenta o menu completo (6 opcoes) -- so os 2 candidatos.
    assert _nome_caminho(CaminhoMapaMestre.CURSO_ONLINE_HG, "pt") in r.response_text
    assert _nome_caminho(CaminhoMapaMestre.CURSOS_PRESENCIAIS, "pt") in r.response_text
    assert "outro assunto" not in r.response_text.lower()


@pytest.mark.asyncio
async def test_desambiguacao_pendente_resposta_curta_despacha_candidato_certo():
    """Turno seguinte: resposta CURTA ('online') escolhe entre os 2
    candidatos -- despacha para Caminho 1 sem nova pergunta de
    desambiguacao."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.SISTEMA_GOLDINCISION,
        etapa=ETAPA_SISTEMA_LICENCIAMENTO,
    )
    ctx.troca_caminho_pendente = {
        "destinos": [
            int(CaminhoMapaMestre.CURSO_ONLINE_HG),
            int(CaminhoMapaMestre.CURSOS_PRESENCIAIS),
        ],
        "origem": int(CaminhoMapaMestre.SISTEMA_GOLDINCISION),
        "metodo": "deterministico", "confianca": None, "tipo": "desambiguacao",
    }

    r = await eng.process(1, "online", ctx)

    assert r.caminho == CaminhoMapaMestre.CURSO_ONLINE_HG
    assert ctx.troca_caminho_pendente is None


@pytest.mark.asyncio
async def test_desambiguacao_pendente_resposta_numerica():
    """Resposta numerica (1/2) tambem resolve a desambiguacao (mesmo padrao
    de `_opcao_numerica` ja usado no menu)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.SISTEMA_GOLDINCISION,
        etapa=ETAPA_SISTEMA_LICENCIAMENTO,
    )
    ctx.troca_caminho_pendente = {
        "destinos": [
            int(CaminhoMapaMestre.CURSO_ONLINE_HG),
            int(CaminhoMapaMestre.CURSOS_PRESENCIAIS),
        ],
        "origem": int(CaminhoMapaMestre.SISTEMA_GOLDINCISION),
        "metodo": "deterministico", "confianca": None, "tipo": "desambiguacao",
    }

    r = await eng.process(1, "2", ctx)

    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS
    assert ctx.troca_caminho_pendente is None


@pytest.mark.asyncio
async def test_desambiguacao_pendente_nao_reconhecida_conta_tentativa_original():
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.SISTEMA_GOLDINCISION,
        etapa=ETAPA_SISTEMA_LICENCIAMENTO,
    )
    ctx.troca_caminho_pendente = {
        "destinos": [
            int(CaminhoMapaMestre.CURSO_ONLINE_HG),
            int(CaminhoMapaMestre.CURSOS_PRESENCIAIS),
        ],
        "origem": int(CaminhoMapaMestre.SISTEMA_GOLDINCISION),
        "metodo": "deterministico", "confianca": None, "tipo": "desambiguacao",
    }

    r = await eng.process(1, "sei la", ctx)

    assert ctx.troca_caminho_pendente is None
    assert r.caminho == CaminhoMapaMestre.SISTEMA_GOLDINCISION
    assert r.etapa == ETAPA_SISTEMA_LICENCIAMENTO
    assert _tent_count(ctx, ETAPA_SISTEMA_LICENCIAMENTO) == 1


# ---------------------------------------------------------------------------
# 2.2.7 -- 3+/nenhum candidato -> comportamento existente
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mensagem_sem_marcador_nem_produto_cai_no_comportamento_existente():
    """Sem marcador de correcao E sem produto/caminho reconhecivel -- nao ha
    candidato algum -- cai na reformulacao normal (FR-010), sem
    troca_pendente."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_QUALIF_ESPECIALIDADE,
        eh_medico=True, experiencia_corporal=False,
    )

    r = await eng.process(1, "hummmm nao sei explicar direito", ctx)

    assert r.action == "continue"
    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS
    assert r.etapa == ETAPA_QUALIF_ESPECIALIDADE
    assert ctx.troca_caminho_pendente is None
    assert _tent_count(ctx, ETAPA_QUALIF_ESPECIALIDADE) == 1


# ---------------------------------------------------------------------------
# 2.2.9 (regressao) -- fix #9 (_ETAPAS_AGUARDANDO_RESPOSTA) intocado na
# classificacao GLOBAL; novo detector e mecanismo DISTINTO.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resposta_reconhecida_pelo_resolver_nunca_alcanca_o_detector():
    """2.2.9/Decision 4: quando o resolver ESPECIFICO da etapa reconhece a
    resposta (fast-path), nem a reclassificacao global (fix #9) nem o novo
    detector sao alcancados -- classificacao global continua suprimida em
    `_ETAPAS_AGUARDANDO_RESPOSTA` (comportamento pre-existente, regressao)."""
    eng = engine(ClassificacaoIntencao.SISTEMA_GOLDINCISION)  # LLM "quer" C3
    ctx = make_context(caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_QUALIF_MEDICO)

    r = await eng.process(1, "sim", ctx)

    # fix #9: permanece em C2 (resposta reconhecida), nao salta pro C3.
    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS
    assert ctx.troca_caminho_pendente is None


# ---------------------------------------------------------------------------
# 2.2.10 (regressao) -- overflow-resume tem precedencia total
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overflow_pendente_tem_precedencia_sobre_detector_de_troca():
    """Quickstart Cenario 10: com `overflow_blocos` nao vazio, mesmo uma
    mensagem com marcador explicito + produto claro (que normalmente
    dispararia o detector) e tratada EXCLUSIVAMENTE como resposta ao
    overflow -- o detector nunca e alcancado (Decision 5, P-1)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=CaminhoMapaMestre.SISTEMA_GOLDINCISION, etapa=ETAPA_SISTEMA_LICENCIAMENTO)
    ctx.overflow_blocos = ["Parte 2 do conteudo.", "Parte 3 do conteudo."]
    ctx.overflow_idioma = "pt"

    r = await eng.process(1, "na verdade quero o curso presencial", ctx)

    # Tratado como retomada de overflow (intent "continuar" fastpath do
    # texto), nunca como troca de caminho.
    assert r.caminho == CaminhoMapaMestre.SISTEMA_GOLDINCISION
    assert ctx.troca_caminho_pendente is None
    assert ctx.overflow_blocos == []


# ---------------------------------------------------------------------------
# 2.2.11 (regressao) -- resposta legitima e direta NUNCA e lida como troca
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resposta_legitima_direta_nunca_e_lida_como_troca():
    """Quickstart Cenario 4/FR-009: 'dermatologia' e reconhecida diretamente
    por `_resolver_especialidade` -- `_reformular_ou_handoff`/o detector
    NUNCA sao alcancados (satisfeito por construcao)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_QUALIF_ESPECIALIDADE,
        eh_medico=True, experiencia_corporal=False,
    )

    r = await eng.process(1, "dermatologia", ctx)

    assert ctx.especialidade == "dermatologia"
    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS  # nao mudou de caminho
    assert ctx.troca_caminho_pendente is None


# ---------------------------------------------------------------------------
# 2.3.1 -- preservacao de perfil (FR-006, sem re-perguntar)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_troca_preserva_qualificacao_ja_conhecida():
    """Quickstart Cenario 3: eh_medico/idioma/especialidade preservados apos
    a troca -- nenhum campo depende de caminho/etapa (data-model.md
    §Relationships)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_QUALIF_MEDICO,
        eh_medico=True, especialidade="dermatologia", idioma="pt",
    )

    r = await eng.process(1, "na verdade quero o sistema goldincision", ctx)

    assert r.caminho == CaminhoMapaMestre.SISTEMA_GOLDINCISION
    assert ctx.eh_medico is True
    assert ctx.especialidade == "dermatologia"
    assert ctx.idioma == "pt"


# ---------------------------------------------------------------------------
# 2.3.2 -- _tent_clear na etapa abandonada, orcamento de turnos intocado
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_troca_zera_tentativas_sem_tocar_orcamento_de_turnos():
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_QUALIF_ESPECIALIDADE,
        eh_medico=True, experiencia_corporal=False,
        etapa_funil='{"et": "qualif_especialidade", "n": 2}',
        turnos_sessao=5, turnos_no_no=3,
    )

    await eng.process(1, "na verdade quero o curso online", ctx)

    assert ctx.etapa_funil is None
    assert _tent_count(ctx, ETAPA_QUALIF_ESPECIALIDADE) == 0
    # Orcamento de turnos (contadores de sessao) e ortogonal -- inalterado
    # pela troca (FR-007, P-6 do contrato).
    assert ctx.turnos_sessao == 5


# ---------------------------------------------------------------------------
# 2.3.3 (regressao, FR-021) -- despacho sempre entra pela etapa inicial do
# caminho-alvo, NUNCA retoma etapa de visita anterior.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_troca_reinicia_do_zero_mesmo_com_etapa_de_nome_colidente():
    """FR-021: `ETAPA_QUALIF_MEDICO` e compartilhado por Caminho 1 e Caminho
    2 (mesmo nome de etapa, semantica por-caminho). Se o despacho da troca
    NAO reiniciasse `context.etapa` para None antes de despachar, a
    mensagem de troca em si seria erroneamente re-processada como resposta
    a etapa QUALIF_MEDICO do caminho-alvo (bumping tentativa indevida). Com
    o reset correto, o caminho-alvo comeca do ZERO -- pergunta fresca, ZERO
    tentativas."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSO_ONLINE_HG, etapa=ETAPA_QUALIF_MEDICO,
        eh_medico=None,
    )

    r = await eng.process(1, "na verdade quero cursos presenciais", ctx)

    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS
    assert r.etapa == ETAPA_QUALIF_MEDICO  # etapa inicial fresca do caminho-alvo
    # ZERO tentativas -- a mensagem de troca NUNCA foi reprocessada como
    # resposta (nao-reconhecida) da etapa QUALIF_MEDICO do caminho-alvo.
    assert _tent_count(ctx, ETAPA_QUALIF_MEDICO) == 0
    assert _t("nao_entendi", "pt") not in r.response_text


# ---------------------------------------------------------------------------
# 2.3.4 -- correcao para o MESMO caminho ja ativo -- no-op (Cenario 11)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_correcao_para_o_mesmo_caminho_ativo_e_no_op():
    """Quickstart Cenario 11: mesmo com marcador explicito, se o candidato
    detectado E o caminho ja ativo, nao ha troca/pendencia/efeito colateral
    -- cai no comportamento existente de reformulacao (FR-010)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSO_ONLINE_HG, etapa=ETAPA_QUALIF_ESPECIALIDADE,
        eh_medico=True,
    )
    # ETAPA_QUALIF_ESPECIALIDADE nao existe em `_handle_curso_online` (so em
    # cursos_presenciais) -- forcamos manualmente a chamada ao detector via
    # uma etapa aguardando resposta reconhecida no proprio caminho (fechamento).
    ctx.etapa = "fechamento"

    r = await eng.process(1, "na verdade quero mesmo o curso online", ctx)

    assert r.caminho == CaminhoMapaMestre.CURSO_ONLINE_HG  # mesmo caminho
    assert ctx.troca_caminho_pendente is None  # nenhuma pendencia setada
    assert _tent_count(ctx, "fechamento") == 1  # cai na reformulacao normal


# ---------------------------------------------------------------------------
# Helpers puros (unidade, sem FlowEngine)
# ---------------------------------------------------------------------------

def test_detectar_confirmacao_ainda_funciona_para_consumo_de_pendente():
    """Sanity check do helper reusado por `_consumir_troca_pendente`."""
    assert _detectar_confirmacao("sim") is True
    assert _detectar_confirmacao("não") is False
    assert _detectar_confirmacao("batata") is None


# ---------------------------------------------------------------------------
# FASE 6 (task 6.1.3) -- anti-regressao fix #9 em nivel de unidade: uma
# resposta RECONHECIDA PELO RESOLVER ESPECIFICO da etapa nunca alcanca o
# detector de troca de caminho, mesmo quando o texto da resposta nao tem
# nenhuma relacao com o lexico de produtos/caminhos (satisfeito por
# construcao, Decision 3/4 -- reforca 2.2.9/2.2.11 com exemplos concretos
# adicionais citados no prompt de execucao desta fase).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_antiregressao_fix9_sou_medico_nao_dispara_troca_de_caminho():
    """'sou medico' na etapa de qualificacao medica e reconhecida DIRETO por
    `_detectar_confirmacao`/`_resolver_eh_medico` -- `_reformular_ou_handoff`
    (e portanto o detector) nunca e alcancado; nenhum falso-positivo de
    troca de caminho."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=CaminhoMapaMestre.CURSOS_PRESENCIAIS, etapa=ETAPA_QUALIF_MEDICO)

    r = await eng.process(1, "sou medico", ctx)

    assert ctx.eh_medico is True
    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS  # nao mudou de caminho
    assert ctx.troca_caminho_pendente is None


@pytest.mark.asyncio
async def test_antiregressao_fix9_resposta_numerica_em_sistema_objetivo_nao_dispara_troca():
    """'1' em ETAPA_SISTEMA_OBJETIVO e reconhecida DIRETO por
    `_detectar_objetivo_sistema` (opcao numerica do proprio no) -- segue o
    fluxo normal do Caminho 3, nunca e interpretada como troca de caminho
    (o '1' aqui NAO e o menu inicial de 6 opcoes)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA)
    ctx = make_context(caminho=CaminhoMapaMestre.SISTEMA_GOLDINCISION, etapa=ETAPA_SISTEMA_OBJETIVO)

    r = await eng.process(1, "1", ctx)

    assert r.caminho == CaminhoMapaMestre.SISTEMA_GOLDINCISION  # permanece no Caminho 3
    assert ctx.troca_caminho_pendente is None


# ---------------------------------------------------------------------------
# FASE 6 (task 6.1.3) -- variantes multilingues adicionais do Cenario 1
# (ES -- o EN ja existe em test_marcador_explicito_multilingue_en) e do
# Cenario 5 (menu em texto livre, EN/ES) do quickstart, em nivel de unidade.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_marcador_explicito_multilingue_es():
    """Quickstart Cenario 13: marcador ES ('de hecho') + produto ES."""
    eng = engine(ClassificacaoIntencao.AMBIGUA, idioma=Idioma.ES)
    ctx = make_context(
        caminho=CaminhoMapaMestre.CURSO_ONLINE_HG, etapa=ETAPA_QUALIF_MEDICO,
        idioma="es",
    )

    r = await eng.process(1, "de hecho quiero cursos presenciales", ctx)

    assert r.caminho == CaminhoMapaMestre.CURSOS_PRESENCIAIS
    assert ctx.troca_caminho_pendente is None


@pytest.mark.asyncio
async def test_menu_texto_livre_multilingue_en():
    """Quickstart Cenario 5/13 (EN): texto livre no menu inicial, sem
    numero, reconhecido pelo MESMO lexico compartilhado (FR-011)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA, idioma=Idioma.EN)
    ctx = make_context(caminho=None, etapa="menu", idioma="en")

    r = await eng.process(1, "online course", ctx)

    assert r.caminho == CaminhoMapaMestre.CURSO_ONLINE_HG
    assert ctx.troca_caminho_pendente is None


@pytest.mark.asyncio
async def test_menu_texto_livre_multilingue_es():
    """Quickstart Cenario 5/13 (ES): texto livre no menu inicial, sem
    numero, reconhecido pelo MESMO lexico compartilhado (FR-011)."""
    eng = engine(ClassificacaoIntencao.AMBIGUA, idioma=Idioma.ES)
    ctx = make_context(caminho=None, etapa="menu", idioma="es")

    r = await eng.process(1, "armonizacion glutea", ctx)

    assert r.caminho == CaminhoMapaMestre.CURSO_ONLINE_HG
    assert ctx.troca_caminho_pendente is None
