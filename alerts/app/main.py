"""Alerts Bot — servico generico de notificacao Telegram para o ecossistema OLI."""

import asyncio
import os
from collections import OrderedDict
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException

from app.deps import verificar_api_key
from app.logging_setup import configurar_logging
from app.models import AlertLevel, HealthResponse, NotifyRequest, NotifyResponse
from app.patch_watch import PatchRunWatcher
from app.telegram import TelegramClient, TelegramError

configurar_logging()
logger = structlog.get_logger(__name__)

MAX_HEARTBEATS = 50


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


async def _buscar_segredos_vault() -> dict[str, str]:
    """Busca segredos do Vault via oli-auth batch endpoint."""
    vault_addr = os.environ.get("VAULT_ADDR", "")
    role_id = os.environ.get("VAULT_ROLE_ID", "")
    secret_id = os.environ.get("VAULT_SECRET_ID", "")

    if not all([vault_addr, role_id, secret_id]):
        raise RuntimeError("VAULT_ADDR, VAULT_ROLE_ID e VAULT_SECRET_ID obrigatorios")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{vault_addr.rstrip('/')}/v1/secrets/batch",
            json={"paths": ["infra/telegram", "infra/alerts", "supabase/ops"]},
            headers={
                "X-Vault-Role-Id": role_id,
                "X-Vault-Secret-Id": secret_id,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    secrets = data.get("secrets", {})
    errors = data.get("errors", {})

    critical_errors = {
        path: error for path, error in errors.items() if path != "supabase/ops"
    }
    if critical_errors:
        raise RuntimeError(
            f"Vault: segredos nao encontrados: {list(critical_errors.keys())}"
        )

    telegram = secrets.get("infra/telegram", {})
    alerts = secrets.get("infra/alerts", {})
    supabase_ops = secrets.get("supabase/ops", {})

    bot_token = telegram.get("bot_token", "")
    chat_id = telegram.get("chat_id", "")
    api_key = alerts.get("api_key", "")

    if not all([bot_token, chat_id, api_key]):
        raise RuntimeError(
            "Vault: bot_token, chat_id ou api_key ausente em infra/telegram ou infra/alerts"
        )

    return {
        "bot_token": bot_token,
        "chat_id": chat_id,
        "api_key": api_key,
        "supabase_ops_url": supabase_ops.get("url", ""),
        "supabase_ops_key": supabase_ops.get("service_role_key", ""),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: busca credenciais do Vault e inicializa cliente Telegram."""
    logger.info("alerts_startup_iniciando")

    segredos = await _buscar_segredos_vault()

    app.state.api_key = segredos["api_key"]
    app.state.telegram = TelegramClient(segredos["bot_token"], segredos["chat_id"])
    app.state.heartbeats = OrderedDict()
    app.state.patch_watcher = None
    app.state.patch_watch_task = None

    if _env_bool("PATCH_WATCH_ENABLED", True):
        if segredos["supabase_ops_url"] and segredos["supabase_ops_key"]:
            watcher = PatchRunWatcher(
                supabase_url=segredos["supabase_ops_url"],
                service_role_key=segredos["supabase_ops_key"],
                telegram=app.state.telegram,
                poll_seconds=_env_int("PATCH_WATCH_POLL_SECONDS", 30),
                queued_seconds=_env_int("PATCH_WATCH_QUEUED_SECONDS", 60),
                failed_lookback_seconds=_env_int(
                    "PATCH_WATCH_FAILED_LOOKBACK_SECONDS", 86_400
                ),
                alert_cooldown_seconds=_env_int(
                    "PATCH_WATCH_ALERT_COOLDOWN_SECONDS", 21_600
                ),
            )
            app.state.patch_watcher = watcher
            app.state.patch_watch_task = asyncio.create_task(watcher.run())
        else:
            logger.error("patch_watch_disabled", reason="supabase_ops_secret_missing")

    logger.info("alerts_startup_ok")

    yield

    if app.state.patch_watch_task:
        app.state.patch_watch_task.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.patch_watch_task
    if app.state.patch_watcher:
        await app.state.patch_watcher.close()
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
    watcher = getattr(app.state, "patch_watcher", None)
    watch_enabled = _env_bool("PATCH_WATCH_ENABLED", True)
    degraded = telegram is None or (
        watch_enabled and (watcher is None or watcher.last_error is not None)
    )
    return HealthResponse(
        status="degraded" if degraded else "ok",
        telegram_connected=telegram is not None,
        last_heartbeats=dict(getattr(app.state, "heartbeats", {})),
        patch_watch_enabled=watch_enabled,
        patch_watch_last_success_at=watcher.last_success_at if watcher else None,
        patch_watch_last_error=watcher.last_error if watcher else None,
    )
