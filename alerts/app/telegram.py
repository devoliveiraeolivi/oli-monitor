"""Cliente Telegram Bot API. Fire-and-forget via httpx."""

from typing import Optional

import httpx
import structlog

from app.models import AlertLevel

logger = structlog.get_logger(__name__)

EMOJI_MAP = {
    AlertLevel.critical: "\U0001f534",  # 🔴
    AlertLevel.warning: "\U0001f7e1",   # 🟡
    AlertLevel.info: "\U0001f7e2",      # 🟢
}

LEVEL_LABEL = {
    AlertLevel.critical: "CRITICO",
    AlertLevel.warning: "AVISO",
    AlertLevel.info: "INFO",
}


class TelegramError(Exception):
    """Falha ao enviar mensagem via Telegram."""


class TelegramClient:
    """Envia mensagens via Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str):
        self._chat_id = chat_id
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._http = httpx.AsyncClient(timeout=10)

    def _formatar(
        self, app: str, level: AlertLevel, title: str, detail: Optional[str] = None
    ) -> str:
        emoji = EMOJI_MAP.get(level, "\u2139\ufe0f")
        label = LEVEL_LABEL.get(level, level.value.upper())
        texto = f"{emoji} <b>{label}</b> | {app}\n{title}"
        if detail:
            texto += f"\n\n{detail}"
        return texto

    async def enviar(
        self, app: str, level: AlertLevel, title: str, detail: Optional[str] = None
    ) -> int:
        """Envia mensagem para o chat. Retorna message_id ou levanta TelegramError."""
        texto = self._formatar(app, level, title, detail)

        try:
            resp = await self._http.post(
                f"{self._base_url}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": texto,
                    "parse_mode": "HTML",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            message_id = data.get("result", {}).get("message_id")
            logger.info(
                "telegram_enviado",
                app=app,
                level=level.value,
                message_id=message_id,
            )
            return message_id

        except httpx.HTTPStatusError as e:
            logger.error(
                "telegram_erro_http",
                status=e.response.status_code,
                body=e.response.text[:200],
            )
            raise TelegramError(f"HTTP {e.response.status_code}") from e

        except httpx.RequestError as e:
            logger.error("telegram_erro_rede", error=str(e)[:200])
            raise TelegramError(str(e)[:200]) from e

    async def fechar(self) -> None:
        await self._http.aclose()
