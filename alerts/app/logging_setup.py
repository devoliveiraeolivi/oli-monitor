"""Configuracao de logging — structlog JSON (prod) ou console (dev)."""

import os

import structlog


def configurar_logging() -> None:
    """Configura structlog. Chamar uma vez no startup."""
    log_format = os.environ.get("LOG_FORMAT", "json")

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
    ]

    if log_format == "console":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(processors=processors)
