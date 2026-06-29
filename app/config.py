"""
Configuracoes da aplicacao via Pydantic-settings.
Todos os valores sensiveis sao lidos de env vars ou Docker secrets.
NUNCA hardcodar secrets neste arquivo.

Docker secrets: stack.yml monta secrets em /run/secrets/ e seta
*_FILE env vars apontando para o caminho. model_post_init le esses
arquivos e preenche os campos correspondentes (FR-032).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Banco de dados ---
    database_url: str = "postgresql+asyncpg://sdr:sdr@postgres:5432/sdr"

    # --- Redis ---
    redis_url: str = "redis://redis:6379/0"

    # --- OpenAI ---
    openai_api_key: str = ""
    # Modelo de raciocinio: gera respostas do fluxo conversacional
    openai_model_reasoning: str = "gpt-4o"
    # Modelo barato: classificacao de intencao, idioma, sumarizacao
    openai_model_cheap: str = "gpt-4o-mini"

    # --- ChatMaster ---
    chatmaster_token: str = ""
    # Base da conta/webhook deste deploy. Existe tambem api2.chatmasterveloz.com
    # (outra instancia); usar a errada causa 400 "Cannot read properties of null".
    chatmaster_base_url: str = "https://api.chatmasterveloz.com"
    # Handoff humano via API "Atualizar Ticket" (POST {base}/api/tickets/updateAPI):
    # preenche queueId com o id da fila de atendimento humano, userId=null e
    # status="pending". O id da fila e ESPECIFICO do deploy (operador define no
    # ChatMaster) — por isso vem de config, nunca do LLM (SEC-LLM-3).
    # Fila humana padrao (fallback quando o destino logico nao tem mapeamento):
    handoff_queue_id_default: Optional[int] = None
    # Mapa opcional destino-logico -> queueId, como JSON. Ex.:
    #   {"consultores": 78, "presencial": 80, "licenciamento": 81}
    handoff_queue_ids_json: str = ""

    @property
    def handoff_queue_ids(self) -> dict[str, int]:
        """Mapa destino-logico -> queueId, parseado de handoff_queue_ids_json."""
        if not self.handoff_queue_ids_json.strip():
            return {}
        import json as _json
        try:
            raw = _json.loads(self.handoff_queue_ids_json)
            return {str(k): int(v) for k, v in raw.items()}
        except (ValueError, TypeError):
            return {}

    # --- Admin API ---
    admin_token: str = ""

    # --- Webhook (opcional — defesa em profundidade) ---
    webhook_token: Optional[str] = None

    # --- Reset de jornada (#reset) para numeros de teste autorizados ---
    reset_command: str = "#reset"
    # CSV usado para SEMEAR a tabela numero_teste no startup; gerencia dinamica
    # via admin API (/admin/numeros-teste).
    reset_test_numbers: str = "5511967296849,5511941410998"

    @property
    def reset_test_numbers_list(self) -> list[str]:
        return [n.strip() for n in self.reset_test_numbers.split(",") if n.strip()]

    # --- Debounce ---
    debounce_seconds: int = 8

    # --- SSRF allowlist para download de midia ---
    media_download_allowlist: list[str] = ["object.sp2.eveo.com.br"]

    # --- Numero da Nidia (caminho 5 — paciente modelo) ---
    nidia_phone: str = ""

    # --- Rate limiting ---
    max_requests_per_sender_per_minute: int = 30
    llm_max_tokens_per_hour: int = 500_000

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v:
            raise ValueError("DATABASE_URL e obrigatorio")
        return v

    @field_validator("openai_api_key")
    @classmethod
    def validate_openai_key(cls, v: str) -> str:
        # Permitido vazio apenas em testes (mock); em producao deve estar set
        return v

    @field_validator("admin_token")
    @classmethod
    def validate_admin_token(cls, v: str) -> str:
        return v

    def model_post_init(self, __context: Any) -> None:
        """
        Le secrets via convencao *_FILE (Docker secrets).

        Quando stack.yml seta OPENAI_API_KEY_FILE=/run/secrets/openai_api_key,
        le o arquivo e substitui o campo correspondente (tem prioridade sobre env).
        Campos: admin_token, openai_api_key, chatmaster_token, webhook_token.
        """
        _secret_fields: dict[str, str] = {
            "admin_token": "ADMIN_TOKEN_FILE",
            "openai_api_key": "OPENAI_API_KEY_FILE",
            "chatmaster_token": "CHATMASTER_TOKEN_FILE",
            "webhook_token": "WEBHOOK_TOKEN_FILE",
        }
        for field_name, env_var in _secret_fields.items():
            file_path = os.environ.get(env_var)
            if not file_path:
                continue
            p = Path(file_path)
            if p.is_file():
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    object.__setattr__(self, field_name, content)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Instancia global de configuracao
settings = get_settings()
