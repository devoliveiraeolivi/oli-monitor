# Slack Alerts Bot — Design Spec

> **Date:** 2026-03-18
> **Status:** Approved
> **Scope:** Replace Telegram integration with Slack in the OLI alerts bot

## Summary

Replace the Telegram notification client in the OLI alerts bot with a Slack Bot Token integration. The new implementation adds Block Kit formatting, channel routing by severity level, thread grouping by caller-provided key, and interactive buttons (acknowledge/snooze). This is a breaking change to the `POST /notify` contract.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Slack vs Telegram | Slack replaces Telegram | Richer features: threads, buttons, Block Kit, channel routing |
| Integration method | Bot Token (API) | Supports multiple channels, threads, buttons. Webhook is too limited. |
| Feature scope | Full — Block Kit + routing + threads + buttons | Already switching platform, build it right from day 1 |
| Channel mapping | Default in code, override via Vault | Versioned defaults, flexible override without redeploy |
| Thread grouping | By `app + thread_key` (caller-provided) | Caller knows the semantic context (job ID, etc.), not the bot |
| Button behavior | Visual only (no persistence) | Stateless bot, snooze best-effort in memory. Evolve later if needed. |
| API migration | Breaking change | Only one caller (oli-auth), coordinated deploy. See Migration Plan. |
| Architecture | Refactor in-place | Current codebase is solid. No abstraction layer needed for single provider. |

## API Contract

### `POST /notify`

**Request:**

```json
{
  "app": "oli-scraper",
  "level": "critical",
  "title": "Job failed",
  "detail": "Worker crashed at 2026-03-18T14:32:00Z",
  "thread_key": "daily-job-123"
}
```

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `app` | string | yes | max 50 chars | Name of the sending application |
| `level` | enum | yes | `critical`, `warning`, `info` | Severity level |
| `title` | string | yes | max 200 chars | Short alert title |
| `detail` | string | no | max 1000 chars | Additional detail |
| `thread_key` | string | no | max 100 chars | Thread grouping key. Bot groups by `app + thread_key`. |

**Response:**

```json
{
  "ok": true,
  "ts": "1710765432.001234",
  "error": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `ok` | bool | Success flag |
| `ts` | string (optional) | Slack message timestamp (identifier) |
| `error` | string (optional) | Error message |

### `POST /slack/interactions`

Receives Slack interaction payloads (button clicks). Not called by OLI apps directly.

- Content-Type: `application/x-www-form-urlencoded` with `payload` JSON field
- Validated via Slack signing secret (HMAC-SHA256)
- Returns 200 immediately (Slack requires response within 3s)

### `GET /health`

```json
{
  "status": "ok",
  "slack_connected": true,
  "last_heartbeats": {"oli-scraper": "2026-03-18T14:32:00Z"}
}
```

`slack_connected` is `True` if the SlackClient was successfully instantiated at startup (same pattern as current `telegram_connected`). No live API check — avoids latency and rate limit impact on health probes.

## Migration Plan

**Current callers:** Only `oli-auth` calls `POST /notify`.

**Coordinated deploy:**
1. Deploy alerts bot with Slack integration (new contract live). oli-auth calls will fail temporarily but it uses fire-and-forget (`except Exception` → `return False`), so no impact.
2. Deploy oli-auth with updated client (`thread_key` field, expect `ts` instead of `message_id`)
3. Remove `infra/telegram` secrets from Vault

**Rollback strategy:** If Slack integration fails in production, revert alerts bot image to previous tag (Telegram version still works). oli-auth's notify call will fail with connection/format errors but the app itself is unaffected (fire-and-forget pattern).

## Slack Client (`slack.py`)

### Message Formatting (Block Kit)

| Level | Color (attachment sidebar) | Emoji |
|-------|---------------------------|-------|
| critical | `#E01E5A` (red) | :red_circle: |
| warning | `#ECB22E` (yellow) | :large_yellow_circle: |
| info | `#2EB67D` (green) | :large_green_circle: |

**Note:** Color sidebar requires the legacy `attachments` array. Messages use `attachments` with `blocks` inside for the colored sidebar + Block Kit structured content.

**Message structure:**

```
:red_circle: *CRITICAL* | oli-scraper
Job failed

Worker crashed at 2026-03-18T14:32:00Z

[Acknowledge] [Snooze 30m]
```

- Header: emoji + bold level + app name
- Body: title + detail (if present)
- Actions: two buttons in footer

### Threading

- If `thread_key` present: lookup `thread_ts` in dict by `(app, thread_key)`
- If found: post as reply (`thread_ts` parameter). If Slack returns `thread_not_found`, retry without `thread_ts` (post as new message) and update stored `ts`.
- If not found: post new message, store returned `ts`
- No `thread_key`: always new message
- Storage: `cachetools.TTLCache` keyed by `(app, thread_key)` with 7-day TTL and max 1000 entries. Prevents unbounded growth.

### Channel Routing

Default in code:

```python
DEFAULT_CHANNELS = {
    "critical": "#alerts-critical",
    "warning": "#alerts-warning",
    "info": "#alerts-info",
}
```

Override via Vault `infra/slack` field `channel_map` (JSON string). Merge: Vault values override defaults per level.

## Interactions (`interactions.py`)

### Button Interaction Flow

**Note:** Slack's Interactivity URL does not require a `url_verification` handshake (that applies to the Events API only). The URL is configured in app settings and Slack sends interactions to it directly. The `signing_secret` comes from the app-level settings (Basic Information > App Credentials), not from bot token scopes.

1. Slack sends POST with signed payload
2. Validate signing secret (HMAC-SHA256 of body + timestamp + Slack signing version `v0`)
3. Identify action:
   - **`acknowledge`**: Update original message via `chat.update` — add `"Acknowledged by @user at HH:MM"`, remove buttons
   - **`snooze_30m`**: Add `(app, thread_key)` to `cachetools.TTLCache` with 30min TTL. While snoozed, same-key alerts go silently to thread (post with `reply_broadcast=False`). Update message: `"Snoozed 30m by @user"`
4. Return 200 OK immediately

### Snooze Behavior

- Best-effort in memory. Container restart clears snooze state — acceptable for v1.
- Snoozed alerts still post to thread, just without channel notification.
- Snooze only applies when `thread_key` is present. Alerts without `thread_key` cannot be snoozed (buttons are still rendered, but snooze operates on `(app, thread_key)` — without a key, there's nothing to match against).

## Vault & Config

### Path `infra/slack`

| Field | Type | Description |
|-------|------|-------------|
| `bot_token` | string | `xoxb-...` Slack Bot token |
| `signing_secret` | string | For validating interaction payloads |
| `channel_map` | JSON string (optional) | Channel override, e.g. `{"critical":"#ops-critical"}` |

### Path `infra/alerts`

| Field | Type | Description |
|-------|------|-------------|
| `api_key` | string | No change — same key for `POST /notify` |

### Removed

- `infra/telegram` (bot_token, chat_id) — no longer needed

## File Changes

### Modified

| File | Change |
|------|--------|
| `alerts/app/main.py` | Lifespan swaps TelegramClient for SlackClient. `_buscar_segredos_vault()` fetches `infra/slack` instead of `infra/telegram` (extracts `bot_token`, `signing_secret`, optionally `channel_map`). New `POST /slack/interactions` endpoint. Heartbeats logic unchanged. |
| `alerts/app/models.py` | `NotifyRequest` gains `thread_key`. `NotifyResponse` changes `message_id: int` to `ts: str`. `HealthResponse` changes `telegram_connected` to `slack_connected`. |
| `alerts/app/deps.py` | No change |
| `alerts/app/logging_setup.py` | No change |
| `alerts/requirements.txt` | Add `cachetools>=5.0` (TTLCache for snooze). httpx remains for Slack API calls. |
| `alerts/Dockerfile` | No change |

### Removed

| File | Reason |
|------|--------|
| `alerts/app/telegram.py` | Replaced by slack.py |

### New

| File | Responsibility |
|------|---------------|
| `alerts/app/slack.py` | SlackClient — Block Kit formatting, posting, thread management |
| `alerts/app/interactions.py` | Interaction handler (acknowledge, snooze), signing secret validation |

## Docker Compose

No structural change. Traefik continues routing `alerts.oliveiraeolivi.cloud` to port 8000. The `/slack/interactions` endpoint is exposed automatically.

## Slack App Setup (manual, outside code)

1. Create Slack App in workspace
2. Enable Bot Token Scopes: `chat:write` (covers posting and updating messages)
3. Configure Interactivity URL: `https://alerts.oliveiraeolivi.cloud/slack/interactions`
4. Install in workspace
5. Copy `bot_token` and `signing_secret` to Vault `infra/slack`

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Slack API down | `POST /notify` returns 502 + `{"ok": false, "error": "slack_unavailable"}` |
| Channel not found | Slack returns `channel_not_found` → log error + 502 |
| Invalid thread_ts (container restarted, lost memory) | Slack returns `thread_not_found` → catch error, retry without `thread_ts` (new message), update stored ts |
| Button clicked after container restart (snooze lost) | Acknowledge still works. Snooze resets — acceptable. |
| Invalid signing secret on interaction | Returns 401, logs attempt |
| Malformed interaction payload | Returns 400 |
| Multiple clicks on same button | Idempotent — updates message with same content |
| Slack rate limit (Tier 1: ~1/s per channel) | v1: no retry/backoff. Log warning on 429. Future iteration if needed. |

## Testing

| Type | What |
|------|------|
| Unit | `SlackClient._formatar()` — verify Block Kit JSON for each level, with/without detail, with/without buttons |
| Unit | `SlackClient` threading — same `(app, thread_key)` reuses `thread_ts`, different key creates new message |
| Unit | Channel routing — default, Vault override, partial merge |
| Unit | Interactions — signing secret validation (valid, invalid, expired) |
| Unit | Interactions — acknowledge updates message, snooze adds to set |
| Unit | Models — new `NotifyRequest` validation (with/without `thread_key`, limits) |
| Integration | `POST /notify` end-to-end with mocked Slack API (httpx mock) |
| Integration | `POST /slack/interactions` with simulated Slack payload |
| Integration | `GET /health` returns `slack_connected: true/false` |

**Dev dependencies (new):** `pytest`, `pytest-asyncio`, `httpx` (already in prod deps).

No `tests/` directory exists yet — create `alerts/tests/` with `conftest.py` for shared fixtures (mock SlackClient, mock httpx responses).
