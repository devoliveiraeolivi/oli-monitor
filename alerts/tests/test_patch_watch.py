import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.models import AlertLevel
from app.patch_watch import PatchRunWatcher, find_patch_run_alerts

NOW = datetime(2026, 8, 21, 13, 0, tzinfo=timezone.utc)


class FindPatchRunAlertsTests(unittest.TestCase):
    def test_detecta_fila_lease_e_falha_parcial(self) -> None:
        rows = [
            {
                "id": "queued-run",
                "job_id": "job-1",
                "attempt": 1,
                "status": "queued",
                "queued_at": (NOW - timedelta(seconds=61)).isoformat(),
            },
            {
                "id": "running-run",
                "job_id": "job-2",
                "attempt": 2,
                "status": "running",
                "current_step": "readback",
                "lease_expires_at": (NOW - timedelta(seconds=5)).isoformat(),
            },
            {
                "id": "partial-run",
                "job_id": "job-3",
                "attempt": 3,
                "status": "failed_partial",
                "current_step": "applying",
                "error": {"code": "RUNNER_ERROR"},
            },
            {
                "id": "lost-run",
                "job_id": "job-4",
                "attempt": 1,
                "status": "failed",
                "current_step": "worker_lost",
                "error": {"code": "WORKER_LOST"},
            },
        ]

        alerts = find_patch_run_alerts(
            rows,
            {
                "job-1": "CNJ-1",
                "job-2": "CNJ-2",
                "job-3": "CNJ-3",
                "job-4": "CNJ-4",
            },
            now=NOW,
            queued_seconds=60,
        )

        self.assertEqual(4, len(alerts))
        self.assertEqual(
            [
                AlertLevel.critical,
                AlertLevel.critical,
                AlertLevel.critical,
                AlertLevel.warning,
            ],
            [alert.level for alert in alerts],
        )
        self.assertEqual(
            {
                "lost-run:worker_lost",
                "partial-run:failed_partial",
                "running-run:lease_expired",
                "queued-run:queued",
            },
            {alert.key for alert in alerts},
        )

    def test_ignora_fila_recente_e_lease_valido(self) -> None:
        rows = [
            {
                "id": "queued-run",
                "job_id": "job-1",
                "status": "queued",
                "queued_at": (NOW - timedelta(seconds=60)).isoformat(),
            },
            {
                "id": "running-run",
                "job_id": "job-2",
                "status": "running",
                "lease_expires_at": (NOW + timedelta(seconds=1)).isoformat(),
            },
        ]

        self.assertEqual(
            [],
            find_patch_run_alerts(
                rows,
                {},
                now=NOW,
                queued_seconds=60,
            ),
        )

    def test_escapa_conteudo_do_banco_antes_do_html_do_telegram(self) -> None:
        alerts = find_patch_run_alerts(
            [
                {
                    "id": "partial-run",
                    "job_id": "job-1",
                    "status": "failed_partial",
                    "error": {"code": "<boom>"},
                }
            ],
            {"job-1": "<processo>"},
            now=NOW,
            queued_seconds=60,
        )

        self.assertIn("&lt;processo&gt;", alerts[0].title)
        self.assertIn("&lt;boom&gt;", alerts[0].detail)


class PatchRunWatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_nao_repete_o_mesmo_alerta_durante_o_cooldown(self) -> None:
        telegram = AsyncMock()
        watcher = PatchRunWatcher(
            supabase_url="https://ops.example.test",
            service_role_key="service-role-test",
            telegram=telegram,
            alert_cooldown_seconds=300,
        )
        row = {
            "id": "partial-run",
            "job_id": "job-1",
            "attempt": 1,
            "status": "failed_partial",
            "error": {"code": "RUNNER_ERROR"},
        }

        async def fetch_rows(**kwargs):
            return [row] if kwargs["status"] == "failed_partial" else []

        watcher._fetch_rows = AsyncMock(side_effect=fetch_rows)
        watcher._fetch_latest_attempts = AsyncMock(return_value={})
        watcher._fetch_job_numbers = AsyncMock(return_value={"job-1": "CNJ-1"})

        try:
            self.assertEqual(1, await watcher.check_once())
            self.assertEqual(0, await watcher.check_once())
            self.assertEqual(1, telegram.enviar.await_count)
        finally:
            await watcher.close()

    async def test_ignora_falha_parcial_superada_por_tentativa_posterior(self) -> None:
        telegram = AsyncMock()
        watcher = PatchRunWatcher(
            supabase_url="https://ops.example.test",
            service_role_key="service-role-test",
            telegram=telegram,
        )
        old_partial = {
            "id": "partial-run",
            "job_id": "job-1",
            "patch_id": "patch-1",
            "attempt": 1,
            "status": "failed_partial",
            "error": {"code": "RUNNER_ERROR"},
        }

        async def fetch_rows(**kwargs):
            return [old_partial] if kwargs["status"] == "failed_partial" else []

        watcher._fetch_rows = AsyncMock(side_effect=fetch_rows)
        watcher._fetch_latest_attempts = AsyncMock(return_value={"patch-1": 2})
        watcher._fetch_job_numbers = AsyncMock(return_value={})

        try:
            self.assertEqual(0, await watcher.check_once())
            telegram.enviar.assert_not_awaited()
        finally:
            await watcher.close()


if __name__ == "__main__":
    unittest.main()
