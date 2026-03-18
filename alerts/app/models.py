"""Modelos request/response do alerts bot."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AlertLevel(str, Enum):
    critical = "critical"
    warning = "warning"
    info = "info"


class NotifyRequest(BaseModel):
    app: str = Field(..., max_length=50, description="Nome do app que envia")
    level: AlertLevel = Field(..., description="Nivel de severidade")
    title: str = Field(..., max_length=200, description="Titulo curto do alerta")
    detail: Optional[str] = Field(None, max_length=1000, description="Detalhe adicional")
    thread_key: Optional[str] = Field(None, max_length=100, description="Chave de agrupamento em thread")


class NotifyResponse(BaseModel):
    ok: bool
    ts: Optional[str] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    slack_connected: bool
    last_heartbeats: dict[str, str] = {}
