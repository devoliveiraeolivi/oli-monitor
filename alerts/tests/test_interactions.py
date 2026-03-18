"""Tests for Slack interaction handler."""

import hashlib
import hmac
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
    ts = str(int(time.time()) - 600)
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
