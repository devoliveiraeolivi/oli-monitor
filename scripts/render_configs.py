#!/usr/bin/env python3
"""
render_configs.py — Busca segredos do Vault via oli-auth e renderiza configs.

Renderiza prometheus/prometheus.yml a partir do template .tpl,
substituindo placeholders {{CHAVE}} pelos valores do Vault.

Uso:
    python scripts/render_configs.py
    python scripts/render_configs.py --vault-addr https://auth.oliveiraeolivi.cloud

Pré-requisitos:
    - pip install httpx  (ou uv pip install httpx)
    - .env com VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID

Resultado:
    - prometheus/prometheus.yml renderizado (gitignored)
    - Pronto para: docker stack deploy
"""

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    print("ERRO: httpx nao instalado. Rode: pip install httpx")
    sys.exit(1)

# Diretorio raiz do projeto (pai de scripts/)
ROOT = Path(__file__).resolve().parent.parent

# Carregar .env se existir
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# Paths do Vault que precisamos
VAULT_PATHS = [
    "supabase/data",
    "supabase/ops",
    "infra/grafana",
    "infra/loki",
]


def buscar_segredos(vault_addr: str, role_id: str, secret_id: str) -> dict[str, dict]:
    """Busca segredos do Vault via oli-auth batch endpoint."""
    url = f"{vault_addr.rstrip('/')}/v1/secrets/batch"
    resp = httpx.post(
        url,
        json={"paths": VAULT_PATHS},
        headers={
            "X-Vault-Role-Id": role_id,
            "X-Vault-Secret-Id": secret_id,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("errors"):
        print(f"AVISO: segredos nao encontrados: {list(data['errors'].keys())}")

    return data.get("secrets", {})


def renderizar_template(template_path: Path, output_path: Path, variaveis: dict[str, str]) -> None:
    """Substitui {{CHAVE}} no template pelos valores."""
    conteudo = template_path.read_text(encoding="utf-8")

    # Encontrar todos os placeholders
    placeholders = set(re.findall(r"\{\{(\w+)\}\}", conteudo))
    faltando = placeholders - set(variaveis.keys())
    if faltando:
        print(f"ERRO: placeholders sem valor: {faltando}")
        sys.exit(1)

    for chave, valor in variaveis.items():
        conteudo = conteudo.replace(f"{{{{{chave}}}}}", valor)

    output_path.write_text(conteudo, encoding="utf-8")
    print(f"  {output_path.relative_to(ROOT)} renderizado")


def main() -> None:
    parser = argparse.ArgumentParser(description="Renderiza configs com segredos do Vault")
    parser.add_argument(
        "--vault-addr",
        default=os.environ.get("VAULT_ADDR", "https://auth.oliveiraeolivi.cloud"),
        help="Endereco do oli-auth (default: VAULT_ADDR do .env)",
    )
    args = parser.parse_args()

    vault_addr = args.vault_addr
    role_id = os.environ.get("VAULT_ROLE_ID")
    secret_id = os.environ.get("VAULT_SECRET_ID")

    if not role_id or not secret_id:
        print("ERRO: VAULT_ROLE_ID e VAULT_SECRET_ID obrigatorios (via .env ou env vars)")
        sys.exit(1)

    print(f"Buscando segredos de {vault_addr} ...")
    segredos = buscar_segredos(vault_addr, role_id, secret_id)
    print(f"  {len(segredos)} paths carregados")

    # Montar variaveis para templates
    variaveis = {}

    # Supabase Data
    if "supabase/data" in segredos:
        variaveis["SUPABASE_DATA_SERVICE_ROLE_KEY"] = segredos["supabase/data"]["service_role_key"]

    # Supabase OPS (orchestrator)
    if "supabase/ops" in segredos:
        variaveis["SUPABASE_OPS_SERVICE_ROLE_KEY"] = segredos["supabase/ops"]["service_role_key"]

    # Renderizar prometheus.yml
    print("Renderizando configs...")
    renderizar_template(
        ROOT / "prometheus" / "prometheus.yml.tpl",
        ROOT / "prometheus" / "prometheus.yml",
        variaveis,
    )

    print("Pronto! Configs renderizados com segredos do Vault.")
    print("Proximo passo: docker stack deploy -c docker-compose.yml oli-monitor")


if __name__ == "__main__":
    main()
