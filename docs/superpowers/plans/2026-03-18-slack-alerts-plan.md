# Slack Alerts Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Telegram notification client with a Slack Bot Token integration featuring Block Kit, channel routing, threads, and interactive buttons.

**Architecture:** Refactor in-place — swap `telegram.py` for `slack.py`, add `interactions.py` for button handling, update models and main.py. All state (threads, snooze) is in-memory via `cachetools.TTLCache`.

**Tech Stack:** FastAPI, httpx, cachetools, pydantic, structlog, pytest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-18-slack-alerts-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `alerts/requirements.txt` | Modify | Add `cachetools>=5.0` |
| `alerts/requirements-dev.txt` | Create | Dev deps: pytest, pytest-asyncio |
| `alerts/pyproject.toml` | Create | pytest config: asyncio_mode = auto |
| `alerts/app/models.py` | Modify | Add `thread_key`, change response fields |
| `alerts/app/slack.py` | Create | SlackClient — formatting, posting, threading, channel routing |
| `alerts/app/interactions.py` | Create | Signing secret validation, acknowledge/snooze handlers |
| `alerts/app/main.py` | Modify | Swap Telegram→Slack, update Vault paths, add interactions endpoint |
| `alerts/app/telegram.py` | Delete | Replaced by slack.py |
| `alerts/tests/conftest.py` | Create | Shared fixtures (mock httpx, app client) |
| `alerts/tests/test_models.py` | Create | Model validation tests |
| `alerts/tests/test_slack.py` | Create | SlackClient unit tests |
| `alerts/tests/test_interactions.py` | Create | Interaction handler tests |
| `alerts/tests/test_endpoints.py` | Create | Integration tests for all endpoints |

---

### Task 1: Update models

**Files:**
- Modify: `alerts/app/models.py`
- Create: `alerts/tests/__init__.py`
- Create: `alerts/tests/test_models.py`

- [ ] **Step 1: Write failing tests for new models**

Create `alerts/tests/__init__.py` (empty) and `alerts/tests/test_models.py`:

```python
"""Tests for Pydantic models."""

import pytest
from app.models import AlertLevel, NotifyRequest, NotifyResponse, HealthResponse


def test_notify_request_with_thread_key():
    req = NotifyRequest(
        app="oli-scraper", level=AlertLevel.critical,
        title="Job failed", thread_key="job-123",
    )
    assert req.thread_key == "job-123"


def test_notify_request_without_thread_key():
    req = NotifyRequest(
        app="oli-scraper", level=AlertLevel.critical, title="Job failed",
    )
    assert req.thread_key is None


def test_notify_request_thread_key_max_length():
    with pytest.raises(Exception):
        NotifyRequest(
            app="oli-scraper", level=AlertLevel.critical,
            title="Job failed", thread_key="x" * 101,
        )


def test_notify_response_has_ts_string():
    resp = NotifyResponse(ok=True, ts="1710765432.001234")
    assert resp.ts == "1710765432.001234"
    assert resp.ok is True


def test_notify_response_without_ts():
    resp = NotifyResponse(ok=False, error="slack_unavailable")
    assert resp.ts is None


def test_health_response_slack_connected():
    resp = HealthResponse(status="ok", slack_connected=True)
    assert resp.slack_connected is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd alerts && python -m pytest tests/test_models.py -v`
Expected: FAIL — `thread_key` not a field, `NotifyResponse` has no `ts`, `HealthResponse` has no `slack_connected`

- [ ] **Step 3: Update models.py**

Replace `alerts/app/models.py` with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd alerts && python -m pytest tests/test_models.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add alerts/app/models.py alerts/tests/
git commit -m "feat(alerts): update models for Slack — add thread_key, ts, slack_connected"
```

---

### Task 2: Add cachetools dependency

**Files:**
- Modify: `alerts/requirements.txt`

- [ ] **Step 1: Add cachetools to requirements.txt**

Add `cachetools>=5.0` at the end of `alerts/requirements.txt`:

```
fastapi>=0.115
uvicorn[standard]>=0.34
httpx>=0.28
pydantic>=2.0
structlog>=24.0
cachetools>=5.0
```

- [ ] **Step 2: Create dev dependencies and pytest config**

Create `alerts/requirements-dev.txt`:

```
-r requirements.txt
pytest>=8.0
pytest-asyncio>=0.24
```

Create `alerts/pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3: Install dependencies**

Run: `cd alerts && pip install -r requirements-dev.txt`
Expected: cachetools, pytest, pytest-asyncio install successfully

- [ ] **Step 4: Commit**

```bash
git add alerts/requirements.txt alerts/requirements-dev.txt alerts/pyproject.toml
git commit -m "feat(alerts): add cachetools, pytest, pytest-asyncio dependencies"
```

---

### Task 3: Create SlackClient

**Files:**
- Create: `alerts/app/slack.py`
- Create: `alerts/tests/test_slack.py`

- [ ] **Step 1: Write failing tests for SlackClient**

Create `alerts/tests/test_slack.py`:

```python
"""Tests for SlackClient."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models import AlertLevel
from app.slack import SlackClient, SlackError, DEFAULT_CHANNELS


# --- Formatting ---

def test_formatar_critical_with_detail():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    blocks, color = client._formatar(
        app="oli-scraper", level=AlertLevel.critical,
        title="Job failed", detail="Worker crashed",
    )
    # Check structure
    assert len(blocks) == 3  # header section, detail section, actions
    assert color == "#E01E5A"
    # Header contains app and level
    header_text = blocks[0]["text"]["text"]
    assert "oli-scraper" in header_text
    assert "CRITICAL" in header_text
    # Detail present
    assert blocks[1]["text"]["text"] == "Job failed\n\nWorker crashed"
    # Actions block has 2 buttons
    assert len(blocks[2]["elements"]) == 2


def test_formatar_includes_thread_key_in_button_value():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    blocks, _ = client._formatar(
        app="scraper", level=AlertLevel.critical,
        title="Fail", thread_key="job-1",
    )
    import json
    btn_value = json.loads(blocks[2]["elements"][0]["value"])
    assert btn_value["thread_key"] == "job-1"


def test_formatar_no_thread_key_in_button_value():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    blocks, _ = client._formatar(
        app="scraper", level=AlertLevel.critical, title="Fail",
    )
    import json
    btn_value = json.loads(blocks[2]["elements"][0]["value"])
    assert "thread_key" not in btn_value


def test_formatar_info_without_detail():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    blocks, color = client._formatar(
        app="oli-gateway", level=AlertLevel.info, title="Heartbeat",
    )
    assert color == "#2EB67D"
    assert len(blocks) == 3  # header, body (title only), actions
    assert "Heartbeat" in blocks[1]["text"]["text"]
    assert "\n\n" not in blocks[1]["text"]["text"]


def test_formatar_warning():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    _, color = client._formatar(
        app="oli-auth", level=AlertLevel.warning, title="High latency",
    )
    assert color == "#ECB22E"


# --- Channel routing ---

def test_default_channels():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    assert client.canal(AlertLevel.critical) == "#alerts-critical"
    assert client.canal(AlertLevel.warning) == "#alerts-warning"
    assert client.canal(AlertLevel.info) == "#alerts-info"


def test_channel_override():
    override = {"critical": "#ops-fire"}
    client = SlackClient(
        bot_token="xoxb-test", signing_secret="secret", channel_map=override,
    )
    assert client.canal(AlertLevel.critical) == "#ops-fire"
    assert client.canal(AlertLevel.warning) == "#alerts-warning"  # default


# --- Threading ---

@pytest.mark.asyncio
async def test_enviar_stores_thread_ts():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    mock_response = httpx.Response(
        200, json={"ok": True, "ts": "111.222"},
    )
    with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
        ts = await client.enviar(
            app="scraper", level=AlertLevel.critical,
            title="Fail", thread_key="job-1",
        )
    assert ts == "111.222"
    assert client._threads[("scraper", "job-1")] == "111.222"


@pytest.mark.asyncio
async def test_enviar_reuses_thread_ts():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    client._threads[("scraper", "job-1")] = "111.222"
    mock_response = httpx.Response(
        200, json={"ok": True, "ts": "111.333"},
    )
    with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        await client.enviar(
            app="scraper", level=AlertLevel.critical,
            title="Fail again", thread_key="job-1",
        )
    call_json = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
    assert call_json["thread_ts"] == "111.222"


@pytest.mark.asyncio
async def test_enviar_without_thread_key_no_thread():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    mock_response = httpx.Response(
        200, json={"ok": True, "ts": "111.444"},
    )
    with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        await client.enviar(
            app="scraper", level=AlertLevel.info, title="Heartbeat",
        )
    call_json = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
    assert "thread_ts" not in call_json


@pytest.mark.asyncio
async def test_enviar_thread_not_found_retries():
    """If Slack returns thread_not_found, retry without thread_ts."""
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    client._threads[("scraper", "job-1")] = "stale.ts"

    error_response = httpx.Response(200, json={"ok": False, "error": "thread_not_found"})
    success_response = httpx.Response(200, json={"ok": True, "ts": "new.ts"})

    with patch.object(
        client._http, "post", new_callable=AsyncMock,
        side_effect=[error_response, success_response],
    ):
        ts = await client.enviar(
            app="scraper", level=AlertLevel.critical,
            title="Retry", thread_key="job-1",
        )
    assert ts == "new.ts"
    assert client._threads[("scraper", "job-1")] == "new.ts"


# --- Error handling ---

@pytest.mark.asyncio
async def test_enviar_raises_slack_error_on_http_error():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    error_response = httpx.Response(500, text="Internal Server Error", request=httpx.Request("POST", "http://test"))
    with patch.object(client._http, "post", new_callable=AsyncMock, return_value=error_response):
        with pytest.raises(SlackError):
            await client.enviar(
                app="scraper", level=AlertLevel.critical, title="Fail",
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd alerts && python -m pytest tests/test_slack.py -v`
Expected: FAIL — `app.slack` module does not exist

- [ ] **Step 3: Implement SlackClient**

Create `alerts/app/slack.py`:

```python
"""Cliente Slack Bot API com Block Kit, threading e channel routing."""

import json
from typing import Optional

import httpx
import structlog
from cachetools import TTLCache

from app.models import AlertLevel

logger = structlog.get_logger(__name__)

EMOJI_MAP = {
    AlertLevel.critical: "\U0001f534",   # 🔴
    AlertLevel.warning: "\U0001f7e1",    # 🟡
    AlertLevel.info: "\U0001f7e2",       # 🟢
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
            "text": {
                "type": "mrkdwn",
                "text": f"{emoji} *{label}* | {app}",
            },
        }

        body_text = title
        if detail:
            body_text += f"\n\n{detail}"

        body_block = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": body_text,
            },
        }

        button_value = {"app": app, "level": level.value}
        if thread_key:
            button_value["thread_key"] = thread_key

        actions_block = {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Acknowledge"},
                    "action_id": "acknowledge",
                    "style": "primary",
                    "value": json.dumps(button_value),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Snooze 30m"},
                    "action_id": "snooze_30m",
                    "value": json.dumps(button_value),
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
            del self._threads[cache_key]
            ts = await self._post_message(payload)

        if ts is None:
            raise SlackError("Slack API nao retornou ts")

        # Store thread_ts for future grouping
        if cache_key:
            self._threads[cache_key] = ts

        logger.info(
            "slack_enviado",
            app=app,
            level=level.value,
            channel=channel,
            ts=ts,
            threaded=cache_key is not None and "thread_ts" in payload,
        )
        return ts

    async def _post_message(self, payload: dict) -> Optional[str]:
        """POST chat.postMessage. Returns ts or None on thread_not_found."""
        try:
            resp = await self._http.post(
                "https://slack.com/api/chat.postMessage",
                json=payload,
            )
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
                    "slack_erro_http",
                    status=e.response.status_code,
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
                    "channel": channel,
                    "ts": ts,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd alerts && python -m pytest tests/test_slack.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add alerts/app/slack.py alerts/tests/test_slack.py
git commit -m "feat(alerts): add SlackClient with Block Kit, threading, and channel routing"
```

---

### Task 4: Create interactions handler

**Files:**
- Create: `alerts/app/interactions.py`
- Create: `alerts/tests/test_interactions.py`

- [ ] **Step 1: Write failing tests for interactions**

Create `alerts/tests/test_interactions.py`:

```python
"""Tests for Slack interaction handler."""

import hashlib
import hmac
import json
import time

import pytest

from app.interactions import verificar_assinatura, parse_interaction_payload


# --- Signing secret validation ---

def _sign(body: str, secret: str, timestamp: str) -> str:
    """Generate valid Slack signature."""
    sig_basestring = f"v0:{timestamp}:{body}"
    return "v0=" + hmac.new(
        secret.encode(), sig_basestring.encode(), hashlib.sha256,
    ).hexdigest()


def test_verificar_assinatura_valida():
    secret = "test_secret"
    body = "payload=%7B%22test%22%3A+true%7D"
    ts = str(int(time.time()))
    sig = _sign(body, secret, ts)
    # Should not raise
    verificar_assinatura(body, ts, sig, secret)


def test_verificar_assinatura_invalida():
    secret = "test_secret"
    body = "payload=%7B%22test%22%3A+true%7D"
    ts = str(int(time.time()))
    with pytest.raises(ValueError, match="assinatura"):
        verificar_assinatura(body, ts, "v0=invalid", secret)


def test_verificar_assinatura_expirada():
    secret = "test_secret"
    body = "payload=%7B%22test%22%3A+true%7D"
    ts = str(int(time.time()) - 600)  # 10 min ago
    sig = _sign(body, secret, ts)
    with pytest.raises(ValueError, match="expirad"):
        verificar_assinatura(body, ts, sig, secret)


# --- Payload parsing ---

def test_parse_acknowledge_payload():
    payload = {
        "type": "block_actions",
        "user": {"id": "U123", "username": "joao"},
        "actions": [{"action_id": "acknowledge", "value": '{"app":"scraper","level":"critical","thread_key":"job-1"}'}],
        "message": {"ts": "111.222"},
        "channel": {"id": "C123"},
    }
    result = parse_interaction_payload(payload)
    assert result["action"] == "acknowledge"
    assert result["user"] == "joao"
    assert result["message_ts"] == "111.222"
    assert result["channel_id"] == "C123"
    assert result["app"] == "scraper"
    assert result["thread_key"] == "job-1"


def test_parse_snooze_payload():
    payload = {
        "type": "block_actions",
        "user": {"id": "U123", "username": "maria"},
        "actions": [{"action_id": "snooze_30m", "value": '{"app":"gateway","level":"warning","thread_key":"check-5"}'}],
        "message": {"ts": "222.333"},
        "channel": {"id": "C456"},
    }
    result = parse_interaction_payload(payload)
    assert result["action"] == "snooze_30m"
    assert result["app"] == "gateway"
    assert result["thread_key"] == "check-5"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd alerts && python -m pytest tests/test_interactions.py -v`
Expected: FAIL — `app.interactions` module does not exist

- [ ] **Step 3: Implement interactions.py**

Create `alerts/app/interactions.py`:

```python
"""Handler de interacoes do Slack (botoes acknowledge/snooze)."""

import hashlib
import hmac
import json
import time
from typing import Optional

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd alerts && python -m pytest tests/test_interactions.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add alerts/app/interactions.py alerts/tests/test_interactions.py
git commit -m "feat(alerts): add Slack interactions handler — signing validation and payload parsing"
```

---

### Task 5: Update main.py — swap Telegram for Slack

**Files:**
- Modify: `alerts/app/main.py`
- Delete: `alerts/app/telegram.py`

- [ ] **Step 1: Rewrite main.py**

Replace `alerts/app/main.py` with:

```python
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
        # Update message: add ack line, remove buttons
        ack_block = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"\u2705 Acknowledged by @{user} at {now}",
            },
        }
        original_blocks = payload.get("message", {}).get("attachments", [{}])[0].get("blocks", [])
        # Keep header + body, replace actions with ack
        updated_blocks = [b for b in original_blocks if b.get("type") != "actions"] + [ack_block]
        color = payload.get("message", {}).get("attachments", [{}])[0].get("color", "#2EB67D")
        await slack.atualizar_mensagem(channel_id, message_ts, updated_blocks, color)
        logger.info("slack_ack", user=user, app=alert_app, ts=message_ts)

    elif action == "snooze_30m":
        snooze_cache: TTLCache = app.state.snooze_cache
        thread_key = dados["thread_key"]
        if thread_key:
            snooze_cache[(alert_app, thread_key)] = True
        # Update message: add snooze line, remove buttons
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
```

- [ ] **Step 2: Delete telegram.py**

Run: `rm alerts/app/telegram.py`

- [ ] **Step 3: Verify imports work**

Run: `cd alerts && python -c "from app.main import app; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add alerts/app/main.py alerts/app/telegram.py
git commit -m "feat(alerts): swap Telegram for Slack in main.py, delete telegram.py"
```

---

### Task 6: Create test fixtures and integration tests

**Files:**
- Create: `alerts/tests/conftest.py`
- Create: `alerts/tests/test_endpoints.py`

- [ ] **Step 1: Create conftest.py with shared fixtures**

Create `alerts/tests/conftest.py`:

```python
"""Shared test fixtures."""

import pytest
import pytest_asyncio
from collections import OrderedDict
from unittest.mock import AsyncMock

from cachetools import TTLCache
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.slack import SlackClient


@pytest.fixture
def mock_slack():
    """SlackClient with mocked HTTP."""
    client = SlackClient(bot_token="xoxb-test", signing_secret="test_secret")
    client.enviar = AsyncMock(return_value="111.222")
    client.atualizar_mensagem = AsyncMock()
    return client


@pytest.fixture
def configured_app(mock_slack):
    """FastAPI app with mocked state."""
    app.state.api_key = "test-api-key"
    app.state.slack = mock_slack
    app.state.heartbeats = OrderedDict()
    app.state.snooze_cache = TTLCache(maxsize=500, ttl=30 * 60)
    yield app
    # Cleanup
    app.state.api_key = ""
    app.state.slack = None
    app.state.heartbeats = OrderedDict()


@pytest_asyncio.fixture
async def client(configured_app):
    """Async HTTP test client."""
    transport = ASGITransport(app=configured_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

- [ ] **Step 2: Write integration tests**

Create `alerts/tests/test_endpoints.py`:

```python
"""Integration tests for all endpoints."""

import json
import hashlib
import hmac
import time

import pytest


@pytest.mark.asyncio
async def test_notify_success(client, mock_slack):
    resp = await client.post(
        "/notify",
        json={"app": "scraper", "level": "critical", "title": "Fail"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["ts"] == "111.222"
    mock_slack.enviar.assert_called_once()


@pytest.mark.asyncio
async def test_notify_with_thread_key(client, mock_slack):
    resp = await client.post(
        "/notify",
        json={
            "app": "scraper", "level": "warning",
            "title": "Slow", "thread_key": "job-42",
        },
        headers={"X-API-Key": "test-api-key"},
    )
    assert resp.status_code == 200
    call_kwargs = mock_slack.enviar.call_args.kwargs
    assert call_kwargs["thread_key"] == "job-42"


@pytest.mark.asyncio
async def test_notify_unauthorized(client):
    resp = await client.post(
        "/notify",
        json={"app": "scraper", "level": "critical", "title": "Fail"},
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_notify_no_api_key(client):
    resp = await client.post(
        "/notify",
        json={"app": "scraper", "level": "critical", "title": "Fail"},
    )
    assert resp.status_code in (403, 422)


@pytest.mark.asyncio
async def test_health_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["slack_connected"] is True


@pytest.mark.asyncio
async def test_health_heartbeats(client, mock_slack):
    # Send an info-level alert to register heartbeat
    await client.post(
        "/notify",
        json={"app": "gateway", "level": "info", "title": "Heartbeat"},
        headers={"X-API-Key": "test-api-key"},
    )
    resp = await client.get("/health")
    data = resp.json()
    assert "gateway" in data["last_heartbeats"]


@pytest.mark.asyncio
async def test_slack_interactions_invalid_signature(client):
    resp = await client.post(
        "/slack/interactions",
        content="payload=%7B%7D",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=invalid",
        },
    )
    assert resp.status_code == 401
```

- [ ] **Step 3: Run all tests**

Run: `cd alerts && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add alerts/tests/
git commit -m "test(alerts): add conftest fixtures and integration tests for all endpoints"
```

---

### Task 7: Final cleanup and verification

**Files:**
- Verify all files

- [ ] **Step 1: Run full test suite**

Run: `cd alerts && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS (models, slack, interactions, endpoints)

- [ ] **Step 2: Verify the app starts locally**

Run: `cd alerts && VAULT_ADDR=test VAULT_ROLE_ID=test VAULT_SECRET_ID=test python -c "from app.main import app; print(app.title)"`
Expected: `OLI Alerts Bot`

- [ ] **Step 3: Verify telegram.py is deleted**

Run: `ls alerts/app/telegram.py 2>/dev/null && echo "EXISTS" || echo "DELETED"`
Expected: `DELETED`

- [ ] **Step 4: Verify no telegram references remain**

Run: `grep -ri telegram alerts/app/ || echo "CLEAN"`
Expected: `CLEAN`

- [ ] **Step 5: Final commit (if any cleanup needed)**

```bash
git add -A alerts/
git commit -m "chore(alerts): final cleanup — verify no telegram references"
```
