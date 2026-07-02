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
    # Fila da IA: o agente SO atende quando o ticket esta nesta fila (queueId).
    # Mensagens vindas de outra fila (ex.: 78 = atendimento humano) sao ignoradas
    # pelo agente — atendimento humano no mesmo numero, sem interferencia.
    # Se None, o gate de fila e desativado (processa todas as filas).
    ai_queue_id: Optional[int] = 77

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
    reset_test_numbers: str = "5511967296849,5511941410998,555195953520"

    @property
    def reset_test_numbers_list(self) -> list[str]:
        return [n.strip() for n in self.reset_test_numbers.split(",") if n.strip()]

    # --- Debounce ---
    debounce_seconds: int = 8

    # --- Controle de turnos (Pilar 7 — G1-G6) ---
    # Teto de turnos consecutivos no MESMO no do mapa-mestre antes de emitir
    # nudge (nao e handoff: apenas reforca a pergunta pendente). Reseta ao
    # detectar mudanca de etapa_mapa_mestre.
    max_turnos_no_no: int = 6
    # Teto de turnos na sessao inteira antes de escalar para handoff cordial
    # ao destino configurado (nunca decidido pelo LLM — SEC-LLM-3).
    max_turnos_sessao: int = 25
    # Limiar de turnos predominantemente classificados como duvida/pergunta
    # (intent.classify) antes de escalar — ver checklists/requirements.md CHK011.
    max_turnos_duvidas: int = 12
    # Janela (horas) apos a qual uma sessao inativa e retomada com mensagem
    # cordial de reengajamento, sem reiniciar a jornada do zero.
    reengajamento_horas: int = 24
    # Janela (horas) apos a qual a sessao e tratada como NOVA, preservando o
    # perfil ja conhecido do Contato (medico, idioma, especialidade) — nunca
    # re-perguntar dados ja capturados.
    expira_sessao_horas: int = 72
    # TTL do lock de exclusividade por ticket (ms). Elevado de 30s -> 90s
    # com base no pior caso observado de duracao de turno (LLM + multiplos
    # envios + retries) — ver research.md Decision 4. Aplicado em
    # app/core/locks.py (FASE 6).
    lock_ttl_ms: int = 90_000

    # --- SSRF allowlist para download de midia ---
    media_download_allowlist: list[str] = ["object.sp2.eveo.com.br"]

    # --- Numero da Nidia (caminho 5 — paciente modelo) ---
    nidia_phone: str = ""

    # --- Rate limiting ---
    max_requests_per_sender_per_minute: int = 30
    llm_max_tokens_per_hour: int = 500_000

    # --- Pacing de envio (WhatsApp Cloud API via ChatMaster) ---
    # Intervalo minimo entre envios consecutivos (por destinatario + global), em ms.
    # Evita rajadas que ferem o rate limit/pacing da API oficial da Meta.
    whatsapp_min_interval_ms: int = 1000
    # Teto de mensagens enviadas por turno do bot (anti-rajada). Se o split gerar
    # mais blocos que isso, os primeiros N-1 sao enviados e o restante e
    # consolidado num unico bloco curto com convite (nunca despeja tudo).
    max_msgs_per_turn: int = 4
    # Pausa entre blocos consecutivos (segundos). Substitui a constante fixa de 0.4s.
    inter_block_delay_seconds: float = 1.0

    # --- Concisao das respostas geradas (responder) ---
    # Limite de tokens da geracao de raciocinio: respostas objetivas e resumidas.
    reasoning_max_tokens: int = 280

    # --- Portao de Fidelidade (Pilar 7, FR-008..FR-012) ---
    # Teto (hard) do FidelityGate.verificar() — timeout/erro/indisponibilidade
    # e SEMPRE tratado como fiel=False (fail-closed), nunca aprovacao por
    # omissao. Alvo interno de latencia real: ~2s (Clarifications Q3/dec-011).
    verify_timeout_seconds: int = 3

    # --- Interpretacao Agentica / Slot-Filling (Pilar 8, FR-013..FR-018) ---
    # Limiar minimo de confianca para aceitar um slot extraido pelo
    # SlotExtractor (app/core/interpret.py) via fallback agentico. Abaixo
    # disso -> tratado como "nao entendido" -> reformula (nunca adivinha).
    slot_confidence_threshold: float = 0.6

    # --- Fluidez de Intencao / Troca de Caminho (FR-002..FR-004) ---
    # Limiar minimo de confianca para aceitar o slot "troca_caminho"
    # extraido pelo fallback agentico confidence-gated (app/core/flow.py,
    # _SLOT_SCHEMA_TROCA_CAMINHO), invocado apenas quando o fast-path
    # deterministico por lexico (_LEXICO_CAMINHOS/_MARCADORES_CORRECAO) nao
    # reconhece a mensagem. Abaixo disso -> nao aceita -> reformula (nunca
    # adivinha), mesmo padrao de slot_confidence_threshold.
    intent_switch_confidence_threshold: float = 0.6

    # --- RAG Hibrido (Onda 3, FR-007..FR-023) ---
    # Modelo de embeddings usado por OpenAIClient.embed() / app/rag_seed.py
    # (data-model.md §4). 1536 dimensoes (Vector(1536) em app.repository.models.Chunk).
    rag_embedding_model: str = "text-embedding-3-small"
    # Patamar minimo de score_combinado para NAO abster (FR-005; data-model.md
    # §4). Calibravel — processo interino documentado em
    # checklists/requirements.md CHK008.
    rag_limiar_abstencao: float = 0.45
    # Candidatos avaliados por busca vetorial (HNSW, cosine) antes da fusao RRF.
    rag_k_vetorial: int = 20
    # Candidatos avaliados por busca textual (GIN, tsquery) antes da fusao RRF.
    rag_k_textual: int = 20
    # Tamanho do conjunto final (top-N por score_combinado desc) usado no
    # grounding (FR-004).
    rag_top_k: int = 5
    # Timeout duro do HybridRetriever.buscar() (FR-021, research.md Decision
    # 6): estoura -> abster=True, motivo_abstencao="indisponivel" (mesmo
    # padrao de `verify_timeout_seconds`).
    rag_retrieval_timeout_seconds: float = 3.0
    # Cache semantico opcional (FASE 8, FR-019 — SHOULD): reaproveita o
    # RESULTADO da busca (nao a resposta final) para consulta identica/
    # normalizada, desligado por padrao (nunca altera comportamento quando
    # False).
    rag_cache_enabled: bool = False

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
