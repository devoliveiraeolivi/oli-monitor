"""Alerts Bot — servico de notificacao Slack para o ecossistema OLI."""

import json
import os
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import structlog
from cachetools import TTLCache
from fastapi import Depends, FastAPI, HTTPException, Request, Response

from app.deps import verificar_api_key
from app.interactions import parse_interaction_payload, verificar_assinatura
from app.logging_setup import configurar_logging
from app.models import AlertLevel, HealthResponse, NotifyRequest, NotifyResponse
from app.slack import SlackClient, SlackError

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
            json={"paths": ["infra/slack", "infra/alerts"]},
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

    slack = secrets.get("infra/slack", {})
    alerts = secrets.get("infra/alerts", {})

    bot_token = slack.get("bot_token", "")
    signing_secret = slack.get("signing_secret", "")
    api_key = alerts.get("api_key", "")

    if not all([bot_token, signing_secret, api_key]):
        raise RuntimeError(
            "Vault: bot_token, signing_secret ou api_key ausente em infra/slack ou infra/alerts"
        )

    channel_map_raw = slack.get("channel_map", "")
    channel_map = json.loads(channel_map_raw) if channel_map_raw else None

    return {
        "bot_token": bot_token,
        "signing_secret": signing_secret,
        "api_key": api_key,
        "channel_map": channel_map,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: busca credenciais do Vault e inicializa cliente Slack."""
    logger.info("alerts_startup_iniciando")

    segredos = await _buscar_segredos_vault()

    app.state.api_key = segredos["api_key"]
    app.state.slack = SlackClient(
        bot_token=segredos["bot_token"],
        signing_secret=segredos["signing_secret"],
        channel_map=segredos["channel_map"],
    )
    app.state.heartbeats = OrderedDict()
    app.state.snooze_cache = TTLCache(maxsize=500, ttl=30 * 60)

    logger.info("alerts_startup_ok")

    yield

    await app.state.slack.fechar()
    logger.info("alerts_shutdown")


app = FastAPI(title="OLI Alerts Bot", lifespan=lifespan)


@app.post("/notify", response_model=NotifyResponse)
async def notify(
    req: NotifyRequest,
    _: str = Depends(verificar_api_key),
) -> NotifyResponse:
    """Envia notificacao via Slack."""
    slack: SlackClient = app.state.slack

    if req.level == AlertLevel.info:
        heartbeats: OrderedDict = app.state.heartbeats
        heartbeats[req.app] = datetime.now(timezone.utc).isoformat()
        while len(heartbeats) > MAX_HEARTBEATS:
            heartbeats.popitem(last=False)

    # Check snooze — if snoozed, still post to thread but silently (no channel broadcast)
    snooze_cache: TTLCache = app.state.snooze_cache
    is_snoozed = req.thread_key and (req.app, req.thread_key) in snooze_cache

    try:
        ts = await slack.enviar(
            app=req.app,
            level=req.level,
            title=req.title,
            detail=req.detail,
            thread_key=req.thread_key,
            snoozed=is_snoozed,
        )
    except SlackError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return NotifyResponse(ok=True, ts=ts)


@app.post("/slack/interactions")
async def slack_interactions(request: Request) -> Response:
    """Recebe interacoes do Slack (botoes)."""
    body = (await request.body()).decode("utf-8")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    slack: SlackClient = app.state.slack

    try:
        verificar_assinatura(body, timestamp, signature, slack.signing_secret)
    except ValueError as e:
        logger.warning("slack_interacao_invalida", error=str(e))
        raise HTTPException(status_code=401, detail=str(e)) from e

    form = await request.form()
    payload = json.loads(form.get("payload", "{}"))

    try:
        dados = parse_interaction_payload(payload)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail="Payload invalido") from e

    action = dados["action"]
    user = dados["user"]
    message_ts = dados["message_ts"]
    channel_id = dados["channel_id"]
    alert_app = dados["app"]
    now = datetime.now(timezone.utc).strftime("%H:%M")

    if action == "acknowledge":
        ack_block = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"\u2705 Acknowledged by @{user} at {now}",
            },
        }
        original_blocks = payload.get("message", {}).get("attachments", [{}])[0].get("blocks", [])
        updated_blocks = [b for b in original_blocks if b.get("type") != "actions"] + [ack_block]
        color = payload.get("message", {}).get("attachments", [{}])[0].get("color", "#2EB67D")
        await slack.atualizar_mensagem(channel_id, message_ts, updated_blocks, color)
        logger.info("slack_ack", user=user, app=alert_app, ts=message_ts)

    elif action == "snooze_30m":
        snooze_cache: TTLCache = app.state.snooze_cache
        thread_key = dados["thread_key"]
        if thread_key:
            snooze_cache[(alert_app, thread_key)] = True
        snooze_block = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"\U0001f634 Snoozed 30m by @{user}",
            },
        }
        original_blocks = payload.get("message", {}).get("attachments", [{}])[0].get("blocks", [])
        updated_blocks = [b for b in original_blocks if b.get("type") != "actions"] + [snooze_block]
        color = payload.get("message", {}).get("attachments", [{}])[0].get("color", "#2EB67D")
        await slack.atualizar_mensagem(channel_id, message_ts, updated_blocks, color)
        logger.info("slack_snooze", user=user, app=alert_app, ts=message_ts)

    return Response(status_code=200)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check — sem auth."""
    slack = getattr(app.state, "slack", None)
    return HealthResponse(
        status="ok" if slack else "degraded",
        slack_connected=slack is not None,
        last_heartbeats=dict(getattr(app.state, "heartbeats", {})),
    )
