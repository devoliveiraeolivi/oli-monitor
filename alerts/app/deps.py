"""FastAPI dependencies — API key validation."""

import hmac

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

_header = APIKeyHeader(name="X-API-Key")


async def verificar_api_key(
    request: Request,
    api_key: str = Security(_header),
) -> str:
    """Valida X-API-Key header. Constant-time comparison."""
    expected: str = getattr(request.app.state, "api_key", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Servico nao inicializado")
    if not hmac.compare_digest(api_key, expected):
        raise HTTPException(status_code=401, detail="API key invalida")
    return api_key
