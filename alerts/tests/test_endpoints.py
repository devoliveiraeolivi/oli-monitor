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
    # FastAPI returns 401 when X-API-Key header is absent (APIKeyHeader behaviour)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["slack_connected"] is True


@pytest.mark.asyncio
async def test_health_heartbeats(client, mock_slack):
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
