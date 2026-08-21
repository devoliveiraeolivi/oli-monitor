"""Vigia somente leitura da fila de patches de pré-aprovação."""

import asyncio
import html
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from app.models import AlertLevel
from app.telegram import TelegramClient

logger = structlog.get_logger(__name__)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _duration_label(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes}min"
    hours, remainder = divmod(minutes, 60)
    return f"{hours}h{remainder:02d}"


@dataclass(frozen=True)
class PatchRunAlert:
    key: str
    level: AlertLevel
    title: str
    detail: str


def find_patch_run_alerts(
    rows: list[dict[str, Any]],
    jobs: dict[str, str],
    *,
    now: datetime,
    queued_seconds: int,
) -> list[PatchRunAlert]:
    """Revalida e formata as anomalias retornadas pelo Supabase OPS."""
    now = now.astimezone(timezone.utc)
    alerts: list[PatchRunAlert] = []

    for row in rows:
        run_id = str(row.get("id") or "")
        job_id = str(row.get("job_id") or "")
        status = str(row.get("status") or "")
        if not run_id or not job_id:
            continue

        process_number = html.escape(jobs.get(job_id, job_id))
        attempt = int(row.get("attempt") or 0)
        step = html.escape(str(row.get("current_step") or "não informado"))
        safe_run_id = html.escape(run_id)

        if status == "queued":
            queued_at = _parse_timestamp(row.get("queued_at"))
            if queued_at is None or (now - queued_at).total_seconds() <= queued_seconds:
                continue
            age = _duration_label((now - queued_at).total_seconds())
            alerts.append(
                PatchRunAlert(
                    key=f"{run_id}:queued",
                    level=AlertLevel.warning,
                    title=f"Patch parado na fila há {age} — {process_number}",
                    detail=f"Tentativa {attempt} · run <code>{safe_run_id}</code>",
                )
            )
            continue

        if status == "running":
            lease_expires_at = _parse_timestamp(row.get("lease_expires_at"))
            if lease_expires_at is None or lease_expires_at > now:
                continue
            age = _duration_label((now - lease_expires_at).total_seconds())
            alerts.append(
                PatchRunAlert(
                    key=f"{run_id}:lease_expired",
                    level=AlertLevel.critical,
                    title=f"Lease do patch expirou há {age} — {process_number}",
                    detail=(
                        f"Tentativa {attempt} · etapa {step} · "
                        f"run <code>{safe_run_id}</code>"
                    ),
                )
            )
            continue

        if status == "failed_partial":
            error = row.get("error") if isinstance(row.get("error"), dict) else {}
            code = html.escape(str(error.get("code") or "sem código"))
            alerts.append(
                PatchRunAlert(
                    key=f"{run_id}:failed_partial",
                    level=AlertLevel.critical,
                    title=f"Patch terminou com falha parcial — {process_number}",
                    detail=(
                        f"Tentativa {attempt} · etapa {step} · erro {code} · "
                        f"run <code>{safe_run_id}</code>"
                    ),
                )
            )

        if status == "failed":
            error = row.get("error") if isinstance(row.get("error"), dict) else {}
            if error.get("code") != "WORKER_LOST":
                continue
            alerts.append(
                PatchRunAlert(
                    key=f"{run_id}:worker_lost",
                    level=AlertLevel.critical,
                    title=f"Worker perdeu o lease do patch — {process_number}",
                    detail=(
                        f"Tentativa {attempt} · etapa {step} · "
                        f"run <code>{safe_run_id}</code>"
                    ),
                )
            )

    priority = {AlertLevel.critical: 0, AlertLevel.warning: 1, AlertLevel.info: 2}
    return sorted(alerts, key=lambda item: (priority[item.level], item.key))


class PatchRunWatcher:
    """Consulta o OPS e entrega anomalias novas ao Telegram."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        telegram: TelegramClient,
        poll_seconds: int = 30,
        queued_seconds: int = 60,
        failed_lookback_seconds: int = 86_400,
        alert_cooldown_seconds: int = 21_600,
    ) -> None:
        self._base_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self._telegram = telegram
        self._poll_seconds = max(5, poll_seconds)
        self._queued_seconds = max(30, queued_seconds)
        self._failed_lookback_seconds = max(300, failed_lookback_seconds)
        self._alert_cooldown_seconds = max(300, alert_cooldown_seconds)
        self._http = httpx.AsyncClient(
            timeout=15,
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
            },
        )
        self._notified_at: dict[str, datetime] = {}
        self.last_success_at: str | None = None
        self.last_error: str | None = None

    async def _fetch_rows(
        self,
        *,
        status: str,
        timestamp_field: str,
        operator: str,
        timestamp: datetime,
    ) -> list[dict[str, Any]]:
        response = await self._http.get(
            f"{self._base_url}/indexing_review_patch_runs",
            params={
                "select": (
                    "id,job_id,patch_id,attempt,status,current_step,queued_at,"
                    "lease_expires_at,finished_at,error"
                ),
                "status": f"eq.{status}",
                timestamp_field: f"{operator}.{timestamp.isoformat()}",
                "order": "queued_at.asc",
                "limit": "100",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise TypeError("Supabase OPS retornou payload inesperado para patch runs")
        return [row for row in payload if isinstance(row, dict)]

    async def _fetch_latest_attempts(self, patch_ids: set[str]) -> dict[str, int]:
        if not patch_ids:
            return {}
        response = await self._http.get(
            f"{self._base_url}/indexing_review_patch_runs",
            params={
                "select": "patch_id,attempt",
                "patch_id": f"in.({','.join(sorted(patch_ids))})",
                "order": "attempt.desc",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return {}
        latest: dict[str, int] = {}
        for row in payload:
            if not isinstance(row, dict) or not row.get("patch_id"):
                continue
            patch_id = str(row["patch_id"])
            latest[patch_id] = max(
                latest.get(patch_id, 0), int(row.get("attempt") or 0)
            )
        return latest

    async def _fetch_job_numbers(self, job_ids: set[str]) -> dict[str, str]:
        if not job_ids:
            return {}
        response = await self._http.get(
            f"{self._base_url}/jobs",
            params={
                "select": "id,search_key",
                "id": f"in.({','.join(sorted(job_ids))})",
                "limit": str(len(job_ids)),
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return {}
        return {
            str(row["id"]): str(row.get("search_key") or row["id"])
            for row in payload
            if isinstance(row, dict) and row.get("id")
        }

    async def check_once(self) -> int:
        now = datetime.now(timezone.utc)
        queued_cutoff = now - timedelta(seconds=self._queued_seconds)
        failed_cutoff = now - timedelta(seconds=self._failed_lookback_seconds)
        queued, running, partial, worker_lost = await asyncio.gather(
            self._fetch_rows(
                status="queued",
                timestamp_field="queued_at",
                operator="lt",
                timestamp=queued_cutoff,
            ),
            self._fetch_rows(
                status="running",
                timestamp_field="lease_expires_at",
                operator="lte",
                timestamp=now,
            ),
            self._fetch_rows(
                status="failed_partial",
                timestamp_field="finished_at",
                operator="gte",
                timestamp=failed_cutoff,
            ),
            self._fetch_rows(
                status="failed",
                timestamp_field="finished_at",
                operator="gte",
                timestamp=failed_cutoff,
            ),
        )
        rows = queued + running + partial + worker_lost
        latest_attempts = await self._fetch_latest_attempts(
            {str(row.get("patch_id")) for row in rows if row.get("patch_id")}
        )
        rows = [
            row
            for row in rows
            if int(row.get("attempt") or 0)
            >= latest_attempts.get(
                str(row.get("patch_id")), int(row.get("attempt") or 0)
            )
        ]
        jobs = await self._fetch_job_numbers({str(row.get("job_id")) for row in rows})
        alerts = find_patch_run_alerts(
            rows,
            jobs,
            now=now,
            queued_seconds=self._queued_seconds,
        )

        sent = 0
        cooldown = timedelta(seconds=self._alert_cooldown_seconds)
        for alert in alerts:
            last_notified = self._notified_at.get(alert.key)
            if last_notified and now - last_notified < cooldown:
                continue
            await self._telegram.enviar(
                app="oli-indexer-patch",
                level=alert.level,
                title=alert.title,
                detail=alert.detail,
            )
            self._notified_at[alert.key] = now
            sent += 1

        active_keys = {alert.key for alert in alerts}
        self._notified_at = {
            key: notified
            for key, notified in self._notified_at.items()
            if key in active_keys or now - notified < cooldown
        }
        self.last_success_at = now.isoformat()
        self.last_error = None
        return sent

    async def run(self) -> None:
        logger.info(
            "patch_watch_ready",
            poll_seconds=self._poll_seconds,
            queued_seconds=self._queued_seconds,
        )
        while True:
            try:
                sent = await self.check_once()
                if sent:
                    logger.info("patch_watch_alerts_sent", alerts_sent=sent)
                else:
                    logger.debug("patch_watch_checked")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — watcher deve sobreviver a falha externa
                self.last_error = str(exc)[:300]
                logger.error("patch_watch_failed", error=self.last_error)
            await asyncio.sleep(self._poll_seconds)

    async def close(self) -> None:
        await self._http.aclose()
