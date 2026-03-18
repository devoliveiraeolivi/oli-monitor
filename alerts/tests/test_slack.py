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
    assert len(blocks) == 3
    assert color == "#E01E5A"
    header_text = blocks[0]["text"]["text"]
    assert "oli-scraper" in header_text
    assert "CRITICAL" in header_text
    assert blocks[1]["text"]["text"] == "Job failed\n\nWorker crashed"
    assert len(blocks[2]["elements"]) == 2


def test_formatar_includes_thread_key_in_button_value():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    blocks, _ = client._formatar(
        app="scraper", level=AlertLevel.critical,
        title="Fail", thread_key="job-1",
    )
    btn_value = json.loads(blocks[2]["elements"][0]["value"])
    assert btn_value["thread_key"] == "job-1"


def test_formatar_no_thread_key_in_button_value():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    blocks, _ = client._formatar(
        app="scraper", level=AlertLevel.critical, title="Fail",
    )
    btn_value = json.loads(blocks[2]["elements"][0]["value"])
    assert "thread_key" not in btn_value


def test_formatar_info_without_detail():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    blocks, color = client._formatar(
        app="oli-gateway", level=AlertLevel.info, title="Heartbeat",
    )
    assert color == "#2EB67D"
    assert len(blocks) == 3
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
    assert client.canal(AlertLevel.warning) == "#alerts-warning"


# --- Threading ---

@pytest.mark.asyncio
async def test_enviar_stores_thread_ts():
    client = SlackClient(bot_token="xoxb-test", signing_secret="secret")
    mock_response = httpx.Response(200, json={"ok": True, "ts": "111.222"})
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
    mock_response = httpx.Response(200, json={"ok": True, "ts": "111.333"})
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
    mock_response = httpx.Response(200, json={"ok": True, "ts": "111.444"})
    with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        await client.enviar(app="scraper", level=AlertLevel.info, title="Heartbeat")
    call_json = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
    assert "thread_ts" not in call_json


@pytest.mark.asyncio
async def test_enviar_thread_not_found_retries():
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
            await client.enviar(app="scraper", level=AlertLevel.critical, title="Fail")
