# CLAUDE.md

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

## Ecosystem

Parte do ecossistema OLI: oli-scraper, oli-ml, oli-api, oli-llm, oli-index, oli-ops, oli-gateway.
