"""
Mapper explicito DB (snake_case) <-> DTO (camelCase).

Previne drift de nomes entre colunas Postgres e payloads da API.
Sem auto-mapping via ORM na borda externa (risco de exposicao).

Implementacao completa: FASE 2, task 2.1.
"""
from __future__ import annotations


def curso_model_to_dto(model) -> dict:
    """
    Converte modelo Curso (snake_case) para DTO (camelCase).
    STUB: implementacao completa em FASE 2.
    """
    # TODO (FASE 2): mapear todos os campos do Curso
    return {
        "id": model.id,
        "slug": model.slug,
        "nome": model.nome,
        "tipo": model.tipo,
        "caminhoMapaMestre": model.caminho_mapa_mestre,
        "elegibilidade": model.elegibilidade,
        "ativo": model.ativo,
        "createdAt": model.created_at.isoformat() if model.created_at else None,
        "updatedAt": model.updated_at.isoformat() if model.updated_at else None,
    }


def dto_to_curso_dict(dto: dict) -> dict:
    """
    Converte DTO (camelCase) para dict de colunas (snake_case) para INSERT/UPDATE.
    Anti mass-assignment: apenas campos explicitamente mapeados sao aceitos (SEC-ADM-4).
    """
    # Apenas campos escritaveis; id/created_at sao excluidos
    allowed = {
        "slug", "nome", "tipo", "caminhoMapaMestre", "elegibilidade", "ativo"
    }
    unknown = set(dto.keys()) - allowed
    if unknown:
        raise ValueError(f"Campos nao permitidos no payload: {unknown}")

    result = {}
    if "slug" in dto:
        result["slug"] = dto["slug"]
    if "nome" in dto:
        result["nome"] = dto["nome"]
    if "tipo" in dto:
        result["tipo"] = dto["tipo"]
    if "caminhoMapaMestre" in dto:
        result["caminho_mapa_mestre"] = dto["caminhoMapaMestre"]
    if "elegibilidade" in dto:
        result["elegibilidade"] = dto["elegibilidade"]
    if "ativo" in dto:
        result["ativo"] = dto["ativo"]
    return result
