# Alerting — OLI Monitoring

> **Audiência:** desenvolvedor de qualquer app OLI que precise disparar alerta ou monitorar cron.
> **Status:** documenta estado real em 2026-05-18 (incidente Vault) + design de alvo (alerts bot designado em 2026-03-18 mas não deployado).

---

## Overview

OLI Monitoring tem **2 camadas de alerting** independentes mas complementares:

| Camada | Quando dispara | Tecnologia | Caso de uso |
|---|---|---|---|
| **Dead-man's switch** | Cron MORREU ou não rodou no horário esperado | [Healthchecks.io](https://healthchecks.io) | Scripts agendados (cron) que precisam confirmar execução |
| **Notificações ativas** | App detectou falha e quer avisar | Slack (webhook ou bot) | Qualquer app, qualquer falha runtime |

Idealmente toda automação usa as duas: Healthchecks pega "script silenciosamente parou", Slack pega "script rodou mas algo deu errado".

---

## Estado atual (2026-05-18)

Hoje **2 caminhos diferentes** estão em uso no ecossistema:

### Caminho 1 — Webhook direto (em produção)

```
cron script  ─►  Vault (lê URLs)  ─►  Healthchecks.io (ping)
                                  ─►  Slack Incoming Webhook (POST)
```

**Quem usa:** `vault-snapshot.sh` + `vault-smoke-test.sh` (oli-auth repo, scripts/vault/).

**Como funciona:**
- Scripts leem URLs do path `oli/infra/alerts` no Vault
- Pingam Healthchecks no `/start` e no `/$EXIT_CODE` (trap EXIT)
- Em falha, POSTam JSON no Slack webhook

**Pros:** Simples, sem dependência adicional, funcionando em prod.
**Cons:** Sem routing por severidade, sem threads, sem botões. Cada caller implementa o boilerplate.

### Caminho 2 — Alerts Bot (designado, NÃO deployado)

```
app code  ─►  POST /notify  ─►  Alerts Bot (FastAPI)  ─►  Slack Bot API
                                                       ─►  Block Kit + threads + buttons
```

**Quem usa:** Nada ainda. Bot está em [`alerts/`](../alerts/) com WIP na worktree `slack-alerts`.

**Design completo em** [`docs/superpowers/specs/2026-03-18-slack-alerts-design.md`](superpowers/specs/2026-03-18-slack-alerts-design.md):
- Slack **Bot Token** (não webhook) — suporta múltiplos canais, threads, botões
- Block Kit formatting
- Channel routing por severity (`critical`, `warning`, `info`)
- Thread grouping por `app + thread_key`
- Botões interativos (acknowledge, snooze)

**Por que não está deployado:**
- `oli/infra/alerts` e `oli/infra/telegram` (paths legados do bot Telegram original) **não existem no Vault**
- Bot crashloopa no startup porque não consegue ler credenciais
- Migração Telegram→Slack está parcial (worktree slack-alerts em andamento)

**Pros:** Padrão correto pra alertas estruturados. Apps mandam JSON simples, bot cuida do resto.
**Cons:** Precisa terminar implementação + deployar.

---

## Como adicionar monitoramento a um cron novo (Caminho 1)

### Pré-requisitos

1. **Conta Healthchecks.io** (free tier: 20 checks)
2. **Slack workspace** com webhook configurado (veja "Setup inicial" abaixo)
3. **URLs gravadas no Vault** em `oli/infra/alerts`:
   ```
   healthchecks_<seu_check>_url=https://hc-ping.com/UUID
   slack_webhook_url=https://hooks.slack.com/services/.../...
   ```
4. **Sua AppRole** com `read` em `oli/data/infra/alerts` no policy

### Template de script

```bash
#!/bin/bash
# meu-script.sh — descrição do que faz
set -uo pipefail

LOG=/var/log/meu-script.log
log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

# Lê URLs do Vault (best-effort — script roda mesmo sem alerting)
VAULT_CT=$(docker ps --format '{{.Names}}' | grep '^vault_vault' | head -1)
HC_URL=""
SLACK_URL=""
if [ -n "$VAULT_CT" ]; then
  HC_URL=$(docker exec -e VAULT_ADDR=http://127.0.0.1:8200 "$VAULT_CT" \
    vault kv get -field=healthchecks_<seu_check>_url oli/infra/alerts 2>/dev/null) || HC_URL=""
  SLACK_URL=$(docker exec -e VAULT_ADDR=http://127.0.0.1:8200 "$VAULT_CT" \
    vault kv get -field=slack_webhook_url oli/infra/alerts 2>/dev/null) || SLACK_URL=""
fi

# Reporta exit code pro Healthchecks no fim (trap)
EXIT_CODE=1
finish() {
  [ -n "$HC_URL" ] && curl -fsS -m 10 "$HC_URL/$EXIT_CODE" >/dev/null 2>&1 || true
}
trap finish EXIT

# Posta no Slack se quiser dar contexto rico em falhas
notify_slack() {
  [ -z "$SLACK_URL" ] && return
  local title="$1"
  local detail="$2"
  local payload
  payload=$(printf '{"text":":rotating_light: *%s*","attachments":[{"color":"danger","text":"%s"}]}' \
    "$title" "$detail")
  curl -fsS -m 10 -X POST -H "Content-Type: application/json" \
    --data "$payload" "$SLACK_URL" >/dev/null 2>&1 || true
}

# Ping start (Healthchecks detecta scripts hung que nao terminam)
[ -n "$HC_URL" ] && curl -fsS -m 10 "$HC_URL/start" >/dev/null 2>&1 || true

# === SUA LOGICA AQUI ===
if ! sua_operacao_critica; then
  log "FAIL: operacao falhou"
  notify_slack "Meu Script FAILED" "Detalhes do erro no host $(hostname)"
  exit 1
fi

log "OK: tudo certo"
EXIT_CODE=0
```

### Setup do cron

```bash
sudo install -m 700 meu-script.sh /usr/local/bin/
(crontab -l 2>/dev/null; echo "*/15 * * * * /usr/local/bin/meu-script.sh") | sudo crontab -
```

### Configuração do Healthchecks check

- **Schedule:** mesmo cron do step acima (ex: `*/15 * * * *`)
- **Grace time:** margem de segurança (cron lento dá grace; cron crítico, grace curto)
- **Integrations:** ative Slack (vai postar quando check ficar DOWN ou voltar UP)

### Exemplos reais no ecossistema

| Script | Schedule | Repo |
|---|---|---|
| `vault-snapshot.sh` | 3AM diário | oli-auth (`scripts/vault/`) |
| `vault-smoke-test.sh` | A cada 4h | oli-auth (`scripts/vault/`) |

---

## Setup inicial (uma vez por workspace Slack)

### Slack Incoming Webhook

1. Cria canal `#oli-alerts` (private — só quem precisa ver)
2. https://api.slack.com/apps → **Create New App** → **From scratch** → nome `OLI Monitoring`
3. Sidebar → **Incoming Webhooks** → toggle **ON**
4. **Add New Webhook to Workspace** → escolhe `#oli-alerts` → **Allow**
5. Copia a URL (`https://hooks.slack.com/services/T.../B.../...`)

### Healthchecks.io account

1. https://healthchecks.io → criar conta
2. Project default já basta pra começar
3. Adicione integration Slack: **Settings → Notifications → Slack** → cola webhook URL acima

Cada nova check criada herda esse integration → Healthchecks vai postar no Slack quando DOWN/UP.

### Gravar URLs no Vault

```bash
# Roda no servidor com vault login válido
VAULT_CT=$(docker ps --format '{{.Names}}' | grep '^vault_vault' | head -1)

read -rs -p "Slack WEBHOOK URL: " SLACK_URL; echo
# Adicione health check URLs conforme criar
read -rs -p "Healthchecks SNAPSHOT URL: " HC_SNAP; echo

docker exec -e VAULT_ADDR=http://127.0.0.1:8200 "$VAULT_CT" \
  vault kv put oli/infra/alerts \
    "slack_webhook_url=$SLACK_URL" \
    "healthchecks_snapshot_url=$HC_SNAP"

unset SLACK_URL HC_SNAP
```

**Para adicionar novo check depois:** lê o atual + adiciona campo (KV v2 substitui o secret inteiro):

```bash
# Lê todos campos atuais como JSON, adiciona o novo, grava de volta
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 "$VAULT_CT" \
  vault kv get -format=json oli/infra/alerts > /tmp/current.json

# Edita /tmp/current.json adicionando o novo field em "data.data"
# Depois:
cat /tmp/current.json | jq '.data.data' | \
  docker exec -i -e VAULT_ADDR=http://127.0.0.1:8200 "$VAULT_CT" \
    vault kv put oli/infra/alerts -

rm /tmp/current.json
```

### Atualizar policy de quem vai consumir

Apps que precisam ler essas URLs precisam de `read` no path. Veja [`oli-auth/scripts/run_setup.py:157`](https://github.com/devoliveiraeolivi/oli-auth/blob/main/scripts/run_setup.py#L157) (policy `worker-auth`) como exemplo:

```hcl
path "oli/data/infra/alerts" { capabilities = ["read"] }
```

---

## Como será no futuro (Caminho 2 — alerts bot)

**Quando o bot estiver deployado**, apps mandam JSON simples:

```python
import httpx

await httpx.AsyncClient().post(
    "https://alerts.oliveiraeolivi.cloud/notify",
    json={
        "app": "oli-scraper",
        "level": "critical",
        "title": "Job failed",
        "detail": "Worker crashed at 14:32",
        "thread_key": "daily-job-123",
    },
    headers={"X-API-Key": api_key},
    timeout=10,
)
```

Bot cuida de:
- Routing pra canal certo por `level`
- Block Kit formatting
- Thread grouping por `app + thread_key`
- Botões de acknowledge/snooze

**Setup pra usar o bot:**
- App lê `infra/alerts.api_key` do Vault no startup
- POSTa em `alerts.oliveiraeolivi.cloud/notify` quando precisar alertar
- Bot faz o resto

API contract completo em [`docs/superpowers/specs/2026-03-18-slack-alerts-design.md`](superpowers/specs/2026-03-18-slack-alerts-design.md).

---

## Migration plan: webhook → alerts bot

Quando o bot estiver pronto pra prod:

### 1. Finalizar implementação do bot
- Merge da worktree `slack-alerts`
- Setup `infra/alerts` no Vault com `api_key`, `bot_token`, `signing_secret`, `channel_routing`
- Deploy via Portainer
- Smoke test: `curl POST /notify` retorna `{"ok": true}`

### 2. Atualizar scripts existentes

Para cada cron script usando webhook direto:

**Antes (webhook):**
```bash
SLACK_URL=$(vault kv get -field=slack_webhook_url oli/infra/alerts)
curl -X POST -H "Content-Type: application/json" \
  --data '{"text":"..."}' "$SLACK_URL"
```

**Depois (bot):**
```bash
ALERTS_KEY=$(vault kv get -field=api_key oli/infra/alerts)
curl -X POST -H "Content-Type: application/json" \
  -H "X-API-Key: $ALERTS_KEY" \
  --data '{"app":"vault-monitoring","level":"critical","title":"...","detail":"..."}' \
  https://alerts.oliveiraeolivi.cloud/notify
```

### 3. Deprecar `slack_webhook_url` em Vault

Depois que todos consumers migrarem, remove o field (KV v2 mantém histórico mesmo após delete).

### 4. Healthchecks continua independente

Healthchecks.io fica como está — é dead-man's switch, complementar ao bot.

---

## FAQ

**Q: Posso usar só Slack webhook sem Healthchecks?**
Sim, mas perde detecção de "cron silenciosamente parou". Healthchecks é praticamente grátis (free tier farto) — recomendo usar os dois.

**Q: Posso usar só Healthchecks sem Slack?**
Sim — Healthchecks tem integration com Email, Pushover, Discord, etc. Configura Settings → Notifications.

**Q: O bot futuro vai ter rate limit?**
Sim, conforme design (`POST /notify` retorna 429 se exceder). Pra cron de baixa frequência (até 1/min) sem problema.

**Q: Por que não Telegram?**
Histórico: alerts bot original usava Telegram. Decisão de migrar pra Slack em 2026-03-18 (mais features). Telegram path foi deprecado mas ainda referenciado em alguns docs antigos.

**Q: E se eu não tiver Vault?**
Pra dev local: hardcode URLs em env vars. Pra prod: Vault é o padrão do ecossistema OLI.

---

## Referências

- [Healthchecks.io docs](https://healthchecks.io/docs/)
- [Slack Incoming Webhooks](https://api.slack.com/messaging/webhooks)
- [Slack Block Kit](https://api.slack.com/block-kit) (pra alerts bot)
- Design completo do alerts bot: [`docs/superpowers/specs/2026-03-18-slack-alerts-design.md`](superpowers/specs/2026-03-18-slack-alerts-design.md)
- Implementação atual em uso: [oli-auth scripts/vault/](https://github.com/devoliveiraeolivi/oli-auth/tree/main/scripts/vault)
- Origem dessa doc: incidente Vault 2026-05-18, ver [oli-auth/docs/superpowers/plans/2026-05-18-vault-followup.md](https://github.com/devoliveiraeolivi/oli-auth/blob/main/docs/superpowers/plans/2026-05-18-vault-followup.md)
