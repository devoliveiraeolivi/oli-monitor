# Prometheus config — scrape targets para o ecossistema OLI
# Retencao: 30 dias (alinhado com Loki)
#
# TEMPLATE: renderizado por scripts/render_configs.py
# Nao edite prometheus.yml diretamente — edite este .tpl
#
# Targets:
#   - Alloy local (host metrics via prometheus.exporter.unix)
#   - oli-gateway (FastAPI instrumentator, rede interna)
#   - cAdvisor (metricas de containers Docker)
#   - Blackbox (probes HTTP/HTTPS de endpoints)
#   - Alloy remoto (host metrics Hostinger, via remote_write)

global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  # ----------------------------------------------------------
  # Alloy local — host metrics (CPU, RAM, disco, rede)
  # prometheus.exporter.unix expoe metricas na porta 12345
  # ----------------------------------------------------------
  - job_name: "alloy-host"
    static_configs:
      - targets: ["alloy:12345"]
        labels:
          instance: "vps-principal"

  # ----------------------------------------------------------
  # oli-gateway — FastAPI APM (latencia, throughput, erros)
  # Requer prometheus-fastapi-instrumentator no gateway
  # ----------------------------------------------------------
  - job_name: "oli-gateway"
    static_configs:
      - targets: ["oli-gateway_oli-gateway:8000"]
        labels:
          instance: "vps-principal"
    metrics_path: /metrics

  # ----------------------------------------------------------
  # cAdvisor — metricas de containers Docker
  # CPU, RAM, rede, disco, restarts por container
  # ----------------------------------------------------------
  - job_name: "cadvisor"
    static_configs:
      - targets: ["cadvisor:8080"]
        labels:
          instance: "vps-principal"

  # ----------------------------------------------------------
  # Blackbox — probes HTTP/HTTPS (disponibilidade end-to-end)
  # Cada target e uma URL a ser verificada
  # ----------------------------------------------------------
  - job_name: "blackbox-http"
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets:
          - https://oliveiraeolivi.cloud
          - https://ops.oliveiraeolivi.cloud
          - https://bi.oliveiraeolivi.cloud
          - https://grafana.oliveiraeolivi.cloud
          - https://gateway.oliveiraeolivi.cloud/health
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: target
      - target_label: __address__
        replacement: blackbox:9115

  # ----------------------------------------------------------
  # Supabase Data (oli-data-prod) — PG metrics
  # connections, cache hit ratio, queries, replication
  # ----------------------------------------------------------
  - job_name: "supabase-data"
    scheme: https
    static_configs:
      - targets: ["velyjzbguhdxaxjuwuob.supabase.co"]
        labels:
          project: "oli-data-prod"
    metrics_path: /customer/v1/privileged/metrics
    scrape_interval: 60s
    basic_auth:
      username: "service_role"
      password: "{{SUPABASE_DATA_SERVICE_ROLE_KEY}}"

  # ----------------------------------------------------------
  # Supabase Orchestrator (oli-orchestrator-prod) — PG metrics
  # filas do scraper/indexer, jobs, scheduling
  # ----------------------------------------------------------
  - job_name: "supabase-orchestrator"
    scheme: https
    static_configs:
      - targets: ["emaftzzoocutppydfpyc.supabase.co"]
        labels:
          project: "oli-orchestrator-prod"
    metrics_path: /customer/v1/privileged/metrics
    scrape_interval: 60s
    basic_auth:
      username: "service_role"
      password: "{{SUPABASE_OPS_SERVICE_ROLE_KEY}}"
