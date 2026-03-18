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
