"""Alerts Bot — servico generico de notificacao Telegram para o ecossistema OLI."""

import os
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import structlog

from app.deps import verificar_api_key
from app.logging_setup import configurar_logging
from app.models import AlertLevel, HealthResponse, NotifyRequest, NotifyResponse
from app.telegram import TelegramClient, TelegramError

from fastapi import Depends, FastAPI, HTTPException

configurar_logging()
logger = structlog.get_logger(__name__)

MAX_HEARTBEATS = 50


async def _buscar_segredos_vault() -> dict[str, str]:
    """Busca segredos do Vault via oli-auth batch endpoint."""
    vault_addr = os.environ.get("VAULT_ADDR", "")
    role_id = os.environ.get("VAULT_ROLE_ID", "")
    secret_id = os.environ.get("VAULT_SECRET_ID", "")

    if not all([vault_addr, role_id, secret_id]):
        raise RuntimeError(
            "VAULT_ADDR, VAULT_ROLE_ID e VAULT_SECRET_ID obrigatorios"
        )

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{vault_addr.rstrip('/')}/v1/secrets/batch",
            json={"paths": ["infra/telegram", "infra/alerts"]},
            headers={
                "X-Vault-Role-Id": role_id,
                "X-Vault-Secret-Id": secret_id,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    secrets = data.get("secrets", {})
    errors = data.get("errors", {})

    if errors:
        raise RuntimeError(f"Vault: segredos nao encontrados: {list(errors.keys())}")

    telegram = secrets.get("infra/telegram", {})
    alerts = secrets.get("infra/alerts", {})

    bot_token = telegram.get("bot_token", "")
    chat_id = telegram.get("chat_id", "")
    api_key = alerts.get("api_key", "")

    if not all([bot_token, chat_id, api_key]):
        raise RuntimeError(
            "Vault: bot_token, chat_id ou api_key ausente em infra/telegram ou infra/alerts"
        )

    return {"bot_token": bot_token, "chat_id": chat_id, "api_key": api_key}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: busca credenciais do Vault e inicializa cliente Telegram."""
    logger.info("alerts_startup_iniciando")

    segredos = await _buscar_segredos_vault()

    app.state.api_key = segredos["api_key"]
    app.state.telegram = TelegramClient(segredos["bot_token"], segredos["chat_id"])
    app.state.heartbeats = OrderedDict()

    logger.info("alerts_startup_ok")

    yield

    await app.state.telegram.fechar()
    logger.info("alerts_shutdown")


app = FastAPI(title="OLI Alerts Bot", lifespan=lifespan)


@app.post("/notify", response_model=NotifyResponse)
async def notify(
    req: NotifyRequest,
    _: str = Depends(verificar_api_key),
) -> NotifyResponse:
    """Envia notificacao via Telegram."""
    telegram: TelegramClient = app.state.telegram

    if req.level == AlertLevel.info:
        heartbeats: OrderedDict = app.state.heartbeats
        heartbeats[req.app] = datetime.now(timezone.utc).isoformat()
        while len(heartbeats) > MAX_HEARTBEATS:
            heartbeats.popitem(last=False)

    try:
        message_id = await telegram.enviar(
            app=req.app,
            level=req.level,
            title=req.title,
            detail=req.detail,
        )
    except TelegramError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return NotifyResponse(ok=True, message_id=message_id)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check — sem auth."""
    telegram = getattr(app.state, "telegram", None)
    return HealthResponse(
        status="ok" if telegram else "degraded",
        telegram_connected=telegram is not None,
        last_heartbeats=dict(getattr(app.state, "heartbeats", {})),
    )
