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
