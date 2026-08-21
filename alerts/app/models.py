"""Modelos request/response do alerts bot."""

from enum import Enum

from pydantic import BaseModel, Field


class AlertLevel(str, Enum):
    critical = "critical"
    warning = "warning"
    info = "info"


class NotifyRequest(BaseModel):
    app: str = Field(..., max_length=50, description="Nome do app que envia")
    level: AlertLevel = Field(..., description="Nivel de severidade")
    title: str = Field(..., max_length=200, description="Titulo curto do alerta")
    detail: str | None = Field(None, max_length=1000, description="Detalhe adicional")


class NotifyResponse(BaseModel):
    ok: bool
    message_id: int | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: str
    telegram_connected: bool
    last_heartbeats: dict[str, str] = {}
    patch_watch_enabled: bool = False
    patch_watch_last_success_at: str | None = None
    patch_watch_last_error: str | None = None
