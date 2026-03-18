"""Cliente Slack Bot API com Block Kit, threading e channel routing."""

import json
from typing import Optional

import httpx
import structlog
from cachetools import TTLCache

from app.models import AlertLevel

logger = structlog.get_logger(__name__)

EMOJI_MAP = {
    AlertLevel.critical: "\U0001f534",   # red circle
    AlertLevel.warning: "\U0001f7e1",    # yellow circle
    AlertLevel.info: "\U0001f7e2",       # green circle
}

LEVEL_LABEL = {
    AlertLevel.critical: "CRITICAL",
    AlertLevel.warning: "WARNING",
    AlertLevel.info: "INFO",
}

COLOR_MAP = {
    AlertLevel.critical: "#E01E5A",
    AlertLevel.warning: "#ECB22E",
    AlertLevel.info: "#2EB67D",
}

DEFAULT_CHANNELS: dict[str, str] = {
    "critical": "#alerts-critical",
    "warning": "#alerts-warning",
    "info": "#alerts-info",
}


class SlackError(Exception):
    """Falha ao enviar mensagem via Slack."""


class SlackClient:
    """Envia mensagens via Slack Bot API com Block Kit."""

    def __init__(
        self,
        bot_token: str,
        signing_secret: str,
        channel_map: Optional[dict[str, str]] = None,
    ):
        self.signing_secret = signing_secret
        self._token = bot_token
        self._http = httpx.AsyncClient(
            timeout=10,
            headers={"Authorization": f"Bearer {bot_token}"},
        )
        self._channels = {**DEFAULT_CHANNELS, **(channel_map or {})}
        self._threads: TTLCache = TTLCache(maxsize=1000, ttl=7 * 24 * 3600)

    def canal(self, level: AlertLevel) -> str:
        """Retorna o canal Slack para o nivel de alerta."""
        return self._channels.get(level.value, self._channels.get("info", "#alerts-info"))

    def _formatar(
        self,
        app: str,
        level: AlertLevel,
        title: str,
        detail: Optional[str] = None,
        thread_key: Optional[str] = None,
    ) -> tuple[list[dict], str]:
        """Formata mensagem como Block Kit blocks. Retorna (blocks, color)."""
        emoji = EMOJI_MAP.get(level, "\u2139\ufe0f")
        label = LEVEL_LABEL.get(level, level.value.upper())
        color = COLOR_MAP.get(level, "#2EB67D")

        header_block = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{emoji} *{label}* | {app}"},
        }

        body_text = title
        if detail:
            body_text += f"\n\n{detail}"

        body_block = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": body_text},
        }

        button_value = {"app": app, "level": level.value}
        if thread_key:
            button_value["thread_key"] = thread_key
        value_json = json.dumps(button_value)

        actions_block = {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Acknowledge"},
                    "action_id": "acknowledge",
                    "style": "primary",
                    "value": value_json,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Snooze 30m"},
                    "action_id": "snooze_30m",
                    "value": value_json,
                },
            ],
        }

        return [header_block, body_block, actions_block], color

    async def enviar(
        self,
        app: str,
        level: AlertLevel,
        title: str,
        detail: Optional[str] = None,
        thread_key: Optional[str] = None,
        snoozed: bool = False,
    ) -> str:
        """Envia mensagem para o canal. Retorna ts ou levanta SlackError."""
        blocks, color = self._formatar(app, level, title, detail, thread_key)
        channel = self.canal(level)

        payload: dict = {
            "channel": channel,
            "attachments": [{"color": color, "blocks": blocks}],
        }

        # Threading
        cache_key = (app, thread_key) if thread_key else None
        if cache_key and cache_key in self._threads:
            payload["thread_ts"] = self._threads[cache_key]
            if snoozed:
                payload["reply_broadcast"] = False

        ts = await self._post_message(payload)

        # Handle thread_not_found: retry without thread_ts
        if ts is None and cache_key and "thread_ts" in payload:
            del payload["thread_ts"]
            if "reply_broadcast" in payload:
                del payload["reply_broadcast"]
            del self._threads[cache_key]
            ts = await self._post_message(payload)

        if ts is None:
            raise SlackError("Slack API nao retornou ts")

        # Store thread_ts for future grouping
        if cache_key:
            self._threads[cache_key] = ts

        logger.info(
            "slack_enviado",
            app=app, level=level.value, channel=channel, ts=ts,
            threaded=cache_key is not None and "thread_ts" in payload,
        )
        return ts

    async def _post_message(self, payload: dict) -> Optional[str]:
        """POST chat.postMessage. Returns ts or None on thread_not_found."""
        try:
            resp = await self._http.post(
                "https://slack.com/api/chat.postMessage", json=payload,
            )
            if resp.status_code >= 400:
                resp.raise_for_status()
            data = resp.json()

            if not data.get("ok"):
                error = data.get("error", "unknown")
                if error == "thread_not_found":
                    return None
                logger.error("slack_api_erro", error=error)
                raise SlackError(f"Slack API: {error}")

            return data.get("ts")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("slack_rate_limited", retry_after=e.response.headers.get("Retry-After"))
            else:
                logger.error(
                    "slack_erro_http", status=e.response.status_code,
                    body=e.response.text[:200],
                )
            raise SlackError(f"HTTP {e.response.status_code}") from e

        except httpx.RequestError as e:
            logger.error("slack_erro_rede", error=str(e)[:200])
            raise SlackError(str(e)[:200]) from e

    async def atualizar_mensagem(
        self, channel: str, ts: str, blocks: list[dict], color: str,
    ) -> None:
        """Atualiza mensagem existente via chat.update."""
        try:
            resp = await self._http.post(
                "https://slack.com/api/chat.update",
                json={
                    "channel": channel, "ts": ts,
                    "attachments": [{"color": color, "blocks": blocks}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error("slack_update_erro", error=data.get("error"))
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("slack_update_erro", error=str(e)[:200])

    async def fechar(self) -> None:
        """Fecha o cliente HTTP."""
        await self._http.aclose()
