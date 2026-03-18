"""Handler de interacoes do Slack (botoes acknowledge/snooze)."""

import hashlib
import hmac
import json
import time

import structlog

logger = structlog.get_logger(__name__)

MAX_TIMESTAMP_AGE = 300  # 5 minutos


def verificar_assinatura(
    body: str, timestamp: str, signature: str, signing_secret: str,
) -> None:
    """Valida assinatura HMAC-SHA256 do Slack. Levanta ValueError se invalida."""
    ts = int(timestamp)
    if abs(time.time() - ts) > MAX_TIMESTAMP_AGE:
        raise ValueError("Timestamp expirado")

    sig_basestring = f"v0:{timestamp}:{body}"
    expected = "v0=" + hmac.new(
        signing_secret.encode(), sig_basestring.encode(), hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise ValueError("Slack assinatura invalida")


def parse_interaction_payload(payload: dict) -> dict:
    """Extrai dados relevantes do payload de interacao do Slack."""
    action = payload["actions"][0]
    action_id = action["action_id"]
    value = json.loads(action.get("value", "{}"))

    return {
        "action": action_id,
        "user": payload["user"]["username"],
        "user_id": payload["user"]["id"],
        "message_ts": payload["message"]["ts"],
        "channel_id": payload["channel"]["id"],
        "app": value.get("app", ""),
        "level": value.get("level", ""),
        "thread_key": value.get("thread_key", ""),
    }
