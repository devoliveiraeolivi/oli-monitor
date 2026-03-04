# CLAUDE.md

## Project Overview

OLI Monitor é a stack de monitoramento centralizada do ecossistema OLI. Roda Grafana + Loki + Alloy via Docker Swarm (Portainer) com Traefik na frente.

## Architecture

```
Hosts remotos (Alloy agent) ──HTTPS──► loki.oliveiraeolivi.cloud ──► Loki
Host local   (Alloy local)  ──HTTP───► loki:3100 (rede interna)  ──► Loki
                                                                      │
                                                              Grafana ◄─┘
                                                  grafana.oliveiraeolivi.cloud
```

### Components

- **Loki** — agregador de logs. Recebe de agents locais e remotos.
- **Grafana** — dashboards. Acesso via `grafana.oliveiraeolivi.cloud`.
- **Alloy (local)** — coleta logs de containers no mesmo host via Docker socket.
- **Alloy (remoto)** — template em `alloy/remote/` para agents em outros hosts.

### Labels (Loki)

Todos os logs chegam com estas labels:
- `project` — nome do compose project (oli-scraper, oli-api, etc)
- `service` — nome do service no compose (worker, api, etc)
- `container` — nome do container
- `instance` — hostname do host
- `level` — nível de log (info, warning, error)

### Directory Layout

```
├── docker-compose.yml          # Stack principal (Swarm/Portainer)
├── loki/
│   └── loki-config.yaml        # Config do Loki
├── grafana/
│   ├── datasources.yaml        # Provisioning datasources
│   └── dashboards/
│       ├── dashboards.yaml     # Provisioning provider
│       └── oli-scraper.json    # Dashboard oli-scraper
├── alloy/
│   ├── config.alloy            # Agent local (mesma stack)
│   └── remote/
│       └── config.alloy        # Agent remoto (template)
└── .env.example                # Variáveis de ambiente
```

## Conventions

- Dashboards: um JSON por aplicação em `grafana/dashboards/`
- Novos serviços: basta ter Alloy coletando — logs aparecem filtráveis por `project`
- Retenção padrão: 30 dias (ajustável em `loki-config.yaml`)

## Ecosystem

Parte do ecossistema OLI: oli-scraper, oli-ml, oli-api, oli-llm, oli-index.
