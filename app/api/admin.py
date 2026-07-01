"""
Admin API — CRUD de cursos como dados (Principio VII, FR-025/026).

Seguranca:
- Token Bearer comparado em tempo constante (SEC-ADM-1).
- Rate limiting por IP nas rotas /admin/* (SEC-ADM-2).
- Deny-by-default: sem token valido → 401 em todas as rotas.
- Mass-assignment prevenido via schemas Pydantic com extra='forbid' (SEC-ADM-4).

Runtime read (FR-026): o catalogo e lido do Postgres em runtime; adicionar/
remover/editar curso reflete em conversas novas SEM redeploy.

Sub-recursos granulares (SHOULD): turmas, objecoes, apresentacoes, links.
"""
from __future__ import annotations

import hmac
import logging
import time
from collections import defaultdict
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.repository.models import (
    Chunk,
    Curso,
    CursoApresentacao,
    CursoLink,
    CursoMidia,
    CursoObjecao,
    CursoTurma,
    NumeroTeste,
)
from app.schemas.curso import (
    CursoCreate,
    CursoUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")

security = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Rate limiting simples em memoria (SEC-ADM-2, anti brute-force)
# ---------------------------------------------------------------------------
# Para producao, substituir por Redis com sliding window (maior robustez).
# Aqui: max 20 tentativas/IP por janela de 60s.
_RATE_LIMIT_MAX = 20
_RATE_LIMIT_WINDOW = 60  # segundos

_rate_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_ip: str) -> None:
    """Verifica rate limit por IP. Levanta 429 se excedido (SEC-ADM-2)."""
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW

    # Remover timestamps antigos
    _rate_store[client_ip] = [
        ts for ts in _rate_store[client_ip] if ts > window_start
    ]

    if len(_rate_store[client_ip]) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente em breve.",
            headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
        )

    _rate_store[client_ip].append(now)


# ---------------------------------------------------------------------------
# Autenticacao (SEC-ADM-1)
# ---------------------------------------------------------------------------

def verify_admin_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """
    Dependency: verifica token de admin em tempo constante (SEC-ADM-1).
    Rate limit por IP (SEC-ADM-2). Deny-by-default.
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticacao ausente",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided = credentials.credentials
    expected = settings.admin_token

    if not expected:
        logger.warning("admin: ADMIN_TOKEN nao configurado — acesso negado")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin API nao configurada",
        )

    # Comparacao em tempo constante (anti-timing — SEC-ADM-1)
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        logger.warning("admin: tentativa com token invalido ip=%s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return provided


# ---------------------------------------------------------------------------
# Helpers de DB
# ---------------------------------------------------------------------------

def _get_session():
    """Obtem factory de sessao do estado da aplicacao."""
    from app.main import get_session_factory
    factory = get_session_factory()
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Banco de dados indisponivel",
        )
    return factory


async def _get_curso_or_404(db: AsyncSession, curso_id: int) -> Curso:
    result = await db.execute(select(Curso).where(Curso.id == curso_id))
    curso = result.scalar_one_or_none()
    if not curso:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curso {curso_id} nao encontrado",
        )
    return curso


def _curso_to_dict(curso: Curso) -> dict:
    """Serializa Curso ORM (com relacionamentos) para dict camelCase."""
    return {
        "id": curso.id,
        "slug": curso.slug,
        "nome": curso.nome,
        "tipo": curso.tipo,
        "caminhoMapaMestre": curso.caminho_mapa_mestre,
        "elegibilidade": curso.elegibilidade or {},
        "ativo": curso.ativo,
        "createdAt": curso.created_at.isoformat() if curso.created_at else None,
        "updatedAt": curso.updated_at.isoformat() if curso.updated_at else None,
        "apresentacoes": [
            {
                "id": a.id,
                "idioma": a.idioma,
                "texto": a.texto,
            }
            for a in (curso.apresentacoes or [])
        ],
        "objecoes": [
            {
                "id": o.id,
                "idioma": o.idioma,
                "objecao": o.objecao,
                "resposta": o.resposta,
            }
            for o in (curso.objecoes or [])
        ],
        "turmas": [
            {
                "id": t.id,
                "cidade": t.cidade,
                "pais": t.pais,
                "dataInicio": t.data_inicio.isoformat() if t.data_inicio else None,
                "capacidade": t.capacidade,
                "vagasDisponiveis": t.vagas_disponiveis,
                "lotePreco": t.lote_preco,
                "ativo": t.ativo,
            }
            for t in (curso.turmas or [])
        ],
        "links": [
            {
                "id": lnk.id,
                "idioma": lnk.idioma,
                "url": lnk.url,
            }
            for lnk in (curso.links or [])
        ],
        "midias": [
            {
                "id": m.id,
                "idioma": m.idioma,
                "tipo": m.tipo,
                "url": m.url,
                "legenda": m.legenda,
            }
            for m in (curso.midias or [])
        ],
    }


async def _load_curso_with_relations(
    db: AsyncSession, curso_id: int
) -> Curso:
    """Carrega curso com eager load de todos os relacionamentos."""
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Curso)
        .options(
            selectinload(Curso.apresentacoes),
            selectinload(Curso.objecoes),
            selectinload(Curso.turmas),
            selectinload(Curso.links),
            selectinload(Curso.midias),
        )
        .where(Curso.id == curso_id)
    )
    curso = result.scalar_one_or_none()
    if not curso:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curso {curso_id} nao encontrado",
        )
    return curso


async def _upsert_sub_entities(
    db: AsyncSession,
    curso: Curso,
    payload: dict,
) -> None:
    """
    Sincroniza sub-entidades (apresentacoes, objecoes, turmas, links, midias)
    a partir de payload bruto. Deleta existentes e re-insere (replace).
    """
    curso_id = curso.id

    # Apresentacoes
    if "apresentacoes" in payload:
        await db.execute(
            CursoApresentacao.__table__.delete().where(
                CursoApresentacao.curso_id == curso_id
            )
        )
        for item in payload["apresentacoes"]:
            db.add(
                CursoApresentacao(
                    curso_id=curso_id,
                    idioma=item["idioma"],
                    texto=item["texto"],
                )
            )

    # Objecoes
    if "objecoes" in payload:
        await db.execute(
            CursoObjecao.__table__.delete().where(
                CursoObjecao.curso_id == curso_id
            )
        )
        for item in payload["objecoes"]:
            db.add(
                CursoObjecao(
                    curso_id=curso_id,
                    idioma=item["idioma"],
                    objecao=item["objecao"],
                    resposta=item["resposta"],
                )
            )

    # Turmas
    if "turmas" in payload:
        await db.execute(
            CursoTurma.__table__.delete().where(
                CursoTurma.curso_id == curso_id
            )
        )
        for item in payload["turmas"]:
            from datetime import date as date_type
            data_inicio = None
            if item.get("dataInicio"):
                raw = item["dataInicio"]
                data_inicio = (
                    date_type.fromisoformat(raw) if isinstance(raw, str) else raw
                )
            db.add(
                CursoTurma(
                    curso_id=curso_id,
                    cidade=item["cidade"],
                    pais=item.get("pais"),
                    data_inicio=data_inicio,
                    capacidade=item.get("capacidade"),
                    vagas_disponiveis=item.get("vagasDisponiveis"),
                    lote_preco=item.get("lotePreco"),
                    ativo=item.get("ativo", True),
                )
            )

    # Links
    if "links" in payload:
        await db.execute(
            CursoLink.__table__.delete().where(CursoLink.curso_id == curso_id)
        )
        for item in payload["links"]:
            db.add(
                CursoLink(
                    curso_id=curso_id,
                    idioma=item["idioma"],
                    url=item["url"],
                )
            )

    # Midias
    if "midias" in payload:
        await db.execute(
            CursoMidia.__table__.delete().where(CursoMidia.curso_id == curso_id)
        )
        for item in payload["midias"]:
            db.add(
                CursoMidia(
                    curso_id=curso_id,
                    idioma=item.get("idioma"),
                    tipo=item["tipo"],
                    url=item["url"],
                    legenda=item.get("legenda"),
                )
            )


# ---------------------------------------------------------------------------
# CRUD principal — /admin/cursos
# ---------------------------------------------------------------------------

@router.get(
    "/cursos",
    summary="Lista todos os cursos (ativos e inativos)",
    response_model=None,
)
async def list_cursos(
    _token: str = Depends(verify_admin_token),
    ativo: Optional[bool] = None,
) -> list[dict]:
    """
    Lista cursos com dados completos (subentidades incluidas).

    Query param opcional: ?ativo=true|false
    (FR-026: leitura em runtime sem redeploy)
    """
    from sqlalchemy.orm import selectinload

    factory = _get_session()
    async with factory() as db:
        q = select(Curso).options(
            selectinload(Curso.apresentacoes),
            selectinload(Curso.objecoes),
            selectinload(Curso.turmas),
            selectinload(Curso.links),
            selectinload(Curso.midias),
        )
        if ativo is not None:
            q = q.where(Curso.ativo == ativo)
        q = q.order_by(Curso.id)

        result = await db.execute(q)
        cursos = result.scalars().all()
        return [_curso_to_dict(c) for c in cursos]


@router.get(
    "/cursos/{curso_id}",
    summary="Detalhe de um curso",
    response_model=None,
)
async def get_curso(
    curso_id: int,
    _token: str = Depends(verify_admin_token),
) -> dict:
    """Retorna dados completos de um curso pelo ID."""
    factory = _get_session()
    async with factory() as db:
        curso = await _load_curso_with_relations(db, curso_id)
        return _curso_to_dict(curso)


@router.post(
    "/cursos",
    status_code=status.HTTP_201_CREATED,
    summary="Cria novo curso completo",
    response_model=None,
)
async def create_curso(
    request: Request,
    _token: str = Depends(verify_admin_token),
) -> dict:
    """
    Cria curso com apresentacoes/objecoes/turmas/links/midias.

    O body e validado via Pydantic estrito (extra='forbid') no nivel
    de campos do Curso; sub-entidades sao validadas inline.
    Anti mass-assignment: id, created_at, updated_at sao ignorados do input.
    """
    body = await request.json()

    # Validar campos do Curso (anti mass-assignment — SEC-ADM-4)
    curso_fields = {
        k: v for k, v in body.items()
        if k in {"slug", "nome", "tipo", "caminhoMapaMestre",
                 "caminho_mapa_mestre", "elegibilidade", "ativo"}
    }
    validated = CursoCreate.model_validate(curso_fields)

    factory = _get_session()
    async with factory() as db:
        # Verificar slug duplicado (409)
        existing = await db.execute(
            select(Curso).where(Curso.slug == validated.slug)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Ja existe curso com slug '{validated.slug}'",
            )

        novo = Curso(
            slug=validated.slug,
            nome=validated.nome,
            tipo=validated.tipo,
            caminho_mapa_mestre=validated.caminho_mapa_mestre,
            elegibilidade=validated.elegibilidade or {},
            ativo=validated.ativo,
        )
        db.add(novo)
        await db.flush()  # obtem novo.id

        await _upsert_sub_entities(db, novo, body)
        await db.commit()

        result = await _load_curso_with_relations(db, novo.id)
        return _curso_to_dict(result)


@router.put(
    "/cursos/{curso_id}",
    summary="Atualiza curso (campos opcionais + sub-entidades)",
    response_model=None,
)
async def update_curso(
    curso_id: int,
    request: Request,
    _token: str = Depends(verify_admin_token),
) -> dict:
    """
    Atualiza campos do Curso e/ou sub-entidades enviadas.

    Campos ausentes no body nao sao alterados (patch semantics).
    Sub-entidades enviadas substituem as existentes (replace).
    (FR-026: mudancas refletem em conversas novas sem redeploy)
    """
    body = await request.json()

    # Validar apenas campos do Curso (sem sub-entidades)
    update_fields = {
        k: v for k, v in body.items()
        if k in {"nome", "tipo", "caminhoMapaMestre", "caminho_mapa_mestre",
                 "elegibilidade", "ativo"}
    }
    validated = CursoUpdate.model_validate(update_fields) if update_fields else None

    factory = _get_session()
    async with factory() as db:
        curso = await _get_curso_or_404(db, curso_id)

        if validated:
            if validated.nome is not None:
                curso.nome = validated.nome
            if validated.tipo is not None:
                curso.tipo = validated.tipo
            if validated.caminho_mapa_mestre is not None:
                curso.caminho_mapa_mestre = validated.caminho_mapa_mestre
            if validated.elegibilidade is not None:
                curso.elegibilidade = validated.elegibilidade
            if validated.ativo is not None:
                curso.ativo = validated.ativo

        await _upsert_sub_entities(db, curso, body)
        await db.commit()

        result = await _load_curso_with_relations(db, curso_id)
        return _curso_to_dict(result)


@router.delete(
    "/cursos/{curso_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete de curso (ativo=false)",
)
async def delete_curso(
    curso_id: int,
    _token: str = Depends(verify_admin_token),
) -> None:
    """
    Soft-delete: marca ativo=false. O curso deixa de aparecer no catalogo
    de runtime (FR-026) sem apagar dados historicos.
    """
    factory = _get_session()
    async with factory() as db:
        curso = await _get_curso_or_404(db, curso_id)
        curso.ativo = False
        await db.commit()
    # 204 No Content: retorno vazio


# ---------------------------------------------------------------------------
# Sub-recursos granulares (SHOULD — mesma autenticacao)
# ---------------------------------------------------------------------------

@router.put(
    "/cursos/{curso_id}/apresentacoes/{idioma}",
    summary="Atualiza apresentacao oficial de um curso por idioma",
    response_model=None,
)
async def update_apresentacao(
    curso_id: int,
    idioma: str,
    request: Request,
    _token: str = Depends(verify_admin_token),
) -> dict:
    """
    Upsert de apresentacao verbatim por idioma (pt/en/es).
    Body: {"texto": "..."}
    """
    if idioma not in ("pt", "en", "es"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idioma deve ser pt, en ou es",
        )
    body = await request.json()
    texto = body.get("texto")
    if not texto or not isinstance(texto, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campo 'texto' obrigatorio",
        )

    factory = _get_session()
    async with factory() as db:
        await _get_curso_or_404(db, curso_id)

        result = await db.execute(
            select(CursoApresentacao).where(
                CursoApresentacao.curso_id == curso_id,
                CursoApresentacao.idioma == idioma,
            )
        )
        apres = result.scalar_one_or_none()
        if apres:
            apres.texto = texto
        else:
            db.add(
                CursoApresentacao(
                    curso_id=curso_id,
                    idioma=idioma,
                    texto=texto,
                )
            )
        await db.commit()
    return {"ok": True, "idioma": idioma}


@router.put(
    "/cursos/{curso_id}/links/{idioma}",
    summary="Atualiza link de inscricao por idioma",
    response_model=None,
)
async def update_link(
    curso_id: int,
    idioma: str,
    request: Request,
    _token: str = Depends(verify_admin_token),
) -> dict:
    """
    Upsert de link de inscricao por idioma.
    Body: {"url": "https://..."}
    """
    if idioma not in ("pt", "en", "es"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idioma deve ser pt, en ou es",
        )
    body = await request.json()
    url = body.get("url")
    if not url or not isinstance(url, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campo 'url' obrigatorio",
        )

    factory = _get_session()
    async with factory() as db:
        await _get_curso_or_404(db, curso_id)

        result = await db.execute(
            select(CursoLink).where(
                CursoLink.curso_id == curso_id,
                CursoLink.idioma == idioma,
            )
        )
        lnk = result.scalar_one_or_none()
        if lnk:
            lnk.url = url
        else:
            db.add(CursoLink(curso_id=curso_id, idioma=idioma, url=url))
        await db.commit()
    return {"ok": True, "idioma": idioma, "url": url}


# ---------------------------------------------------------------------------
# Numeros de teste autorizados ao comando #reset (FR: reset de jornada)
# ---------------------------------------------------------------------------

class NumeroTesteCreate(BaseModel):
    """Corpo para cadastro de numero de teste."""
    model_config = ConfigDict(extra="forbid")
    numero: str = Field(min_length=8, max_length=20)
    descricao: Optional[str] = Field(default=None, max_length=200)


@router.get(
    "/numeros-teste",
    summary="Lista numeros autorizados ao comando #reset",
    response_model=None,
)
async def list_numeros_teste(
    _token: str = Depends(verify_admin_token),
) -> list[dict]:
    factory = _get_session()
    async with factory() as db:
        rows = (
            await db.execute(select(NumeroTeste).order_by(NumeroTeste.id))
        ).scalars().all()
        return [
            {
                "id": r.id,
                "numero": r.numero,
                "descricao": r.descricao,
                "ativo": r.ativo,
            }
            for r in rows
        ]


@router.post(
    "/numeros-teste",
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra um numero de teste (#reset)",
    response_model=None,
)
async def criar_numero_teste(
    payload: NumeroTesteCreate,
    _token: str = Depends(verify_admin_token),
) -> dict:
    numero = "".join(ch for ch in payload.numero if ch.isdigit())
    if not numero:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="numero invalido (apenas digitos, DDI+DDD+numero)",
        )
    factory = _get_session()
    async with factory() as db:
        existe = (
            await db.execute(select(NumeroTeste).where(NumeroTeste.numero == numero))
        ).scalar_one_or_none()
        if existe is not None:
            # reativa se estava inativo (idempotente)
            if not existe.ativo:
                existe.ativo = True
                if payload.descricao:
                    existe.descricao = payload.descricao
                await db.commit()
                return {"id": existe.id, "numero": existe.numero, "ativo": True, "reativado": True}
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="numero ja cadastrado",
            )
        novo = NumeroTeste(numero=numero, descricao=payload.descricao, ativo=True)
        db.add(novo)
        await db.commit()
        await db.refresh(novo)
        return {"id": novo.id, "numero": novo.numero, "descricao": novo.descricao, "ativo": True}


@router.delete(
    "/numeros-teste/{numero}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove (desativa) um numero de teste",
)
async def remover_numero_teste(
    numero: str,
    _token: str = Depends(verify_admin_token),
) -> None:
    digitos = "".join(ch for ch in numero if ch.isdigit())
    factory = _get_session()
    async with factory() as db:
        row = (
            await db.execute(select(NumeroTeste).where(NumeroTeste.numero == digitos))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="numero nao encontrado"
            )
        await db.delete(row)
        await db.commit()
    return None


# ---------------------------------------------------------------------------
# `/admin/chunks` — curadoria de chunk tipo='base' (RAG hibrido, Onda 3)
#
# Seguranca (dec-020 finding #1, API3 BOPLA — checklists/requirements.md
# CHK034): `chunk` tambem e alimentado automaticamente por app/rag_seed.py
# a partir de CursoObjecao (tipo='objecao') e Faq (tipo='faq'). Este
# endpoint SOMENTE cura `tipo='base'` — nunca aceita objecao/faq (422) e
# NUNCA aceita fonte_tabela/fonte_id/embedding/ativo do corpo da
# requisicao (o servidor sempre grava fonte_tabela='admin' e
# fonte_id=<id autoincrementado do proprio chunk>), para impedir que um
# payload malicioso sequestre a linha auto-sincronizada de uma
# objecao/FAQ real (mesma UNIQUE(fonte_tabela, fonte_id, idioma) de
# app.repository.models.Chunk).
# ---------------------------------------------------------------------------

class ChunkCreate(BaseModel):
    """
    Corpo para curadoria de chunk `tipo='base'` (FR-007, dec-020 finding
    #1). Aceita SOMENTE estes 4 campos (`extra='forbid'` — SEC-ADM-4):
    tentativa de enviar `fonte_tabela`/`fonte_id`/`embedding`/`ativo`
    (ou qualquer outro campo) e rejeitada com 422 antes de chegar a
    camada de persistencia.

    `tipo` e travado em `Literal["base"]`: enviar `objecao`/`faq` (as
    2 origens exclusivamente auto-sincronizadas por `app/rag_seed.py`)
    retorna 422 automaticamente (dec-020 finding #1).
    """
    model_config = ConfigDict(extra="forbid")
    curso_id: Optional[int] = None
    tipo: Literal["base"]
    idioma: Literal["pt", "en", "es"]
    conteudo: str = Field(min_length=1, max_length=4000)


def _chunk_to_dict(c: Chunk) -> dict:
    return {
        "id": c.id,
        "curso_id": c.curso_id,
        "tipo": c.tipo,
        "idioma": c.idioma,
        "conteudo": c.conteudo,
        "fonte_tabela": c.fonte_tabela,
        "fonte_id": c.fonte_id,
        "ativo": c.ativo,
    }


@router.post(
    "/chunks",
    status_code=status.HTTP_201_CREATED,
    summary="Cria chunk de curadoria (tipo='base') para o RAG hibrido",
    response_model=None,
)
async def criar_chunk(
    payload: ChunkCreate,
    _token: str = Depends(verify_admin_token),
) -> dict:
    """
    Cria um chunk `tipo='base'` curado manualmente pelo admin.

    O embedding e calculado depois, de forma assincrona, pelo proximo
    ciclo de `app/rag_seed.py` (chunk fica com `embedding IS NULL` ate
    la — mesma semantica de "pendente de embedding" de qualquer outro
    chunk novo).
    """
    factory = _get_session()
    async with factory() as db:
        novo = Chunk(
            curso_id=payload.curso_id,
            tipo="base",
            idioma=payload.idioma,
            conteudo=payload.conteudo,
            fonte_tabela="admin",
            fonte_id=0,  # placeholder — corrigido para o proprio id logo abaixo
            ativo=True,
        )
        db.add(novo)
        await db.flush()  # obtem novo.id (sequence)
        novo.fonte_id = novo.id  # fonte_id = <id autoincrementado do proprio chunk>
        await db.commit()
        await db.refresh(novo)
        return _chunk_to_dict(novo)


@router.get(
    "/chunks",
    summary="Lista chunks de curadoria (tipo='base')",
    response_model=None,
)
async def list_chunks(
    _token: str = Depends(verify_admin_token),
) -> list[dict]:
    """
    Lista chunks `tipo='base'` — objecao/faq NAO aparecem aqui (sao
    curados via seus proprios endpoints de admin ja existentes:
    `/admin/cursos/{id}/objecoes`-equivalente e o catalogo do curso;
    este endpoint e exclusivo da curadoria manual de base de conhecimento).
    """
    factory = _get_session()
    async with factory() as db:
        result = await db.execute(
            select(Chunk).where(Chunk.tipo == "base").order_by(Chunk.id)
        )
        rows = result.scalars().all()
        return [_chunk_to_dict(r) for r in rows]


@router.delete(
    "/chunks/{chunk_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove chunk de curadoria (restrito a tipo='base')",
)
async def deletar_chunk(
    chunk_id: int,
    _token: str = Depends(verify_admin_token),
) -> None:
    """
    Remove um chunk por id — restrito a `tipo='base'` (dec-020 finding
    #1): chunks `objecao`/`faq` sao auto-sincronizados por
    `app/rag_seed.py` e NUNCA removidos manualmente por aqui (a
    fonte-da-verdade continua sendo `curso_objecao`/`faq`).
    """
    factory = _get_session()
    async with factory() as db:
        result = await db.execute(
            select(Chunk).where(Chunk.id == chunk_id, Chunk.tipo == "base")
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="chunk nao encontrado (ou nao e tipo='base')",
            )
        await db.delete(row)
        await db.commit()
    return None
