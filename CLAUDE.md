# CLAUDE.md

> **Arquitetura cross-repo:** Este repo faz parte do ecossistema OLI. Para decisões arquiteturais, ADRs, princípios cross-cutting e specs cross-repo, consulte [`oli-platform`](https://github.com/devoliveiraeolivi/oli-platform) — repositório-norte do ecossistema.

## Project Overview

OLI Monitor é a stack de monitoramento centralizada do ecossistema OLI. Roda Loki + Prometheus + Grafana + Alloy + cAdvisor + Blackbox via Docker Swarm (Portainer) com Traefik na frente. Serve como backend de dados para o oli-ops (painel operacional).

## Architecture

```
Hosts remotos (Alloy agent) ──HTTPS──► loki.oliveiraeolivi.cloud ──────► Loki
                            ──HTTPS──► prometheus.oliveiraeolivi.cloud ─► Prometheus
                                       (remote_write, host metrics)       │
Host local   (Alloy local)  ──HTTP───► loki:3100 (rede interna)  ──► Loki│
                             ──HTTP───► prometheus:9090 (remote_write) ─► Prometheus
                                                                          │
Supabase Pro (OPS + Data)  ──scrape──► prometheus:9090 ──────────────────►│
oli-gateway (/metrics)     ──scrape──► prometheus:9090 ──────────────────►│
cAdvisor (containers)      ──scrape──► prometheus:9090 ──────────────────►│
Blackbox (probes HTTP)     ──scrape──► prometheus:9090 ──────────────────►│
                                                                          │
                                                              Grafana ◄───┘ (legado)
                                                              oli-ops ◄───┘ (principal)
```

### Components

- **Loki** — agregador de logs. Recebe de agents locais e remotos via push.
- **Prometheus** — metricas de performance e infra. Scrape do gateway, cAdvisor, Blackbox e Supabase. Recebe remote_write dos Alloy agents.
- **cAdvisor** — metricas de containers Docker (CPU, RAM, rede, disco, restarts por container).
- **Blackbox Exporter** — probes HTTP/HTTPS para verificar disponibilidade end-to-end dos servicos.
- **Grafana** — dashboards legado (sera removido apos migracao para oli-ops).
- **Alloy (local)** — coleta logs de containers + host metrics (CPU, RAM, disco, rede).
- **Alloy (remoto)** — template em `alloy/remote/` para agents em outros hosts (ex: Hostinger).
- **Alerts Bot** — servico FastAPI de notificacao Telegram. `POST /notify` com API key. Qualquer app OLI envia alertas.

### Data Flow

| Fonte | Protocolo | Destino | Tipo |
|---|---|---|---|
| Alloy local (logs) | HTTP push | Loki :3100 | Logs containers |
| Alloy local (host) | HTTP remote_write | Prometheus :9090 | CPU, RAM, disco, rede |
| Alloy remoto (logs) | HTTPS push + basic auth | Loki (Traefik) | Logs containers |
| Alloy remoto (host) | HTTPS remote_write + basic auth | Prometheus (Traefik) | CPU, RAM, disco, rede |
| oli-gateway | Prometheus scrape | Prometheus :9090 | Latencia, throughput, erros |
| cAdvisor | Prometheus scrape | Prometheus :9090 | CPU, RAM, rede, restarts por container |
| Blackbox | Prometheus scrape | Prometheus :9090 | probe_success, latencia, status code |
| Supabase OPS | Prometheus scrape (HTTPS) | Prometheus :9090 | PG connections, cache, queries |
| Supabase Data | Prometheus scrape (HTTPS) | Prometheus :9090 | PG connections, cache, queries |
| Apps OLI (POST /notify) | HTTPS + API key | Alerts Bot :8000 | Telegram notifications |

### Labels (Loki)

Todos os logs chegam com estas labels:
- `project` — nome do compose project (oli-scraper, oli-api, etc)
- `service` — nome do service no compose (worker, api, etc)
- `container` — nome do container
- `instance` — hostname do host
- `level` — nivel de log (info, warning, error)

### Directory Layout

```
├── docker-compose.yml          # Stack principal (Swarm/Portainer)
├── loki/
│   └── loki-config.yaml        # Config do Loki
├── prometheus/
│   └── prometheus.yml          # Config do Prometheus (scrape targets)
├── blackbox/
│   └── config.yml              # Config do Blackbox Exporter (probes)
├── grafana/
│   ├── datasources.yaml        # Provisioning datasources (legado)
│   └── dashboards/
│       ├── dashboards.yaml     # Provisioning provider
│       ├── oli-scraper.json    # Dashboard oli-scraper
│       ├── oli-indexer.json    # Dashboard oli-indexer
│       └── oli-ops.json        # Dashboard oli-ops
├── alerts/
│   ├── Dockerfile              # FastAPI alerts bot
│   ├── requirements.txt        # Dependencias Python
│   └── app/
│       ├── main.py             # App + lifespan + endpoints
│       ├── telegram.py         # Cliente Telegram Bot API
│       ├── models.py           # Pydantic request/response
│       ├── deps.py             # API key validation
│       └── logging_setup.py    # structlog config
├── alloy/
│   ├── config.alloy            # Agent local (logs + host metrics)
│   └── remote/
│       └── config.alloy        # Agent remoto (logs + host metrics + remote_write)
└── .env.example                # Variaveis de ambiente
```

## Conventions

- Grafana dashboards: um JSON por aplicacao em `grafana/dashboards/` (legado, migrando para oli-ops)
- Novos servicos: basta ter Alloy coletando — logs aparecem filtraveis por `project`
- Retencao padrao: 30 dias (Loki em `loki-config.yaml`, Prometheus via `--storage.tsdb.retention.time`)
- Prometheus basic auth via Traefik: setar `PROMETHEUS_BASIC_AUTH_USERS` no env do Portainer
- Supabase metrics: requer plano Pro + service_role key de cada projeto
- Blackbox probes: adicionar URLs em `prometheus.yml` no job `blackbox-http`

### Alerts Bot

FastAPI service em `alerts/`. Recebe `POST /notify` com `X-API-Key` e envia mensagem formatada
para canal Telegram. Primeiro servico Python da stack.

**Endpoints:**
- `POST /notify` — envia notificacao (requer X-API-Key)
- `GET /health` — health check (sem auth)

**Env vars:** VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID, LOG_FORMAT
**Credenciais:** bot_token e chat_id em Vault `infra/telegram`, api_key em `infra/alerts`
**URL:** https://alerts.oliveiraeolivi.cloud

**Status atual (2026-05-18):** Bot NAO esta deployado em prod (paths `infra/telegram` + `infra/alerts.api_key` ausentes no Vault). Migracao Telegram->Slack designada (`docs/superpowers/specs/2026-03-18-slack-alerts-design.md`) e em WIP na worktree `slack-alerts`.

**Pra alerting prod hoje, ver:** [`docs/ALERTING.md`](docs/ALERTING.md) — cobre Healthchecks.io + Slack webhook direto (caminho usado em producao por scripts de cron) + plano de migracao pro alerts bot quando deployado.

## Ecosystem

Parte do ecossistema OLI: oli-scraper, oli-ml, oli-api, oli-llm, oli-index, oli-ops, oli-gateway.
