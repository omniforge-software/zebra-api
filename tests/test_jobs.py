"""Tests for job service: create_job, process_print_job, upsert_printers, cleanup."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.models.db import LabelTemplate, PrintJob, Printer
from app.services.jobs import cleanup_old_jobs, create_job, process_print_job, upsert_printers
from tests.conftest import _get_test_settings


@pytest.fixture(autouse=True)
def _patch_jobs_module(db_engine, monkeypatch):
    """Redirect SessionLocal and settings inside jobs service to the test DB."""
    TestSession = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr("app.services.jobs.SessionLocal", TestSession)
    monkeypatch.setattr("app.services.jobs.get_settings", _get_test_settings)
    monkeypatch.setattr("app.services.zpl_render.get_settings", _get_test_settings)


class TestCreateJob:
    def test_creates_pending_job(self, db_session: Session, sample_printer: Printer, sample_template: LabelTemplate):
        job = create_job(sample_printer.id, sample_template.id, {"title": "Hi", "message": "World"}, 1, None)
        assert job.status == "pending"
        assert job.quantity == 1

    def test_rejects_missing_printer(self, db_session: Session, sample_template: LabelTemplate):
        with pytest.raises(ValueError, match="Printer not found"):
            create_job("nonexistent", sample_template.id, {}, 1, None)

    def test_rejects_missing_template(self, db_session: Session, sample_printer: Printer):
        with pytest.raises(ValueError, match="Template not found"):
            create_job(sample_printer.id, "nonexistent", {}, 1, None)

    def test_rejects_over_max_quantity(self, db_session: Session, sample_printer: Printer, sample_template: LabelTemplate):
        with pytest.raises(ValueError, match="cannot exceed"):
            create_job(sample_printer.id, sample_template.id, {"title": "a", "message": "b"}, 999, None)


class TestProcessPrintJob:
    @pytest.mark.asyncio
    async def test_successful_job(self, db_session: Session, sample_printer: Printer, sample_template: LabelTemplate):
        job = create_job(sample_printer.id, sample_template.id, {"title": "Hi", "message": "World"}, 1, None)
        with patch("app.services.jobs.send_zpl", new_callable=AsyncMock) as mock_send:
            await process_print_job(job.id)
            mock_send.assert_awaited_once()
        db_session.expire_all()
        refreshed = db_session.get(PrintJob, job.id)
        assert refreshed.status == "sent"
        assert refreshed.completed_at is not None

    @pytest.mark.asyncio
    async def test_failed_job(self, db_session: Session, sample_printer: Printer, sample_template: LabelTemplate):
        job = create_job(sample_printer.id, sample_template.id, {"title": "Hi", "message": "World"}, 1, None)
        with patch("app.services.jobs.send_zpl", new_callable=AsyncMock, side_effect=RuntimeError("timeout")):
            await process_print_job(job.id)
        db_session.expire_all()
        refreshed = db_session.get(PrintJob, job.id)
        assert refreshed.status == "failed"
        assert "timeout" in refreshed.error_message

    @pytest.mark.asyncio
    async def test_missing_job_is_noop(self, db_session: Session):
        await process_print_job("nonexistent-id")  # should not raise


class TestUpsertPrinters:
    def test_inserts_new(self, db_session: Session):
        count = upsert_printers([{"ip": "10.0.0.1", "product_name": "ZT411", "is_online": True}])
        assert count == 1
        p = db_session.query(Printer).filter_by(ip="10.0.0.1").first()
        assert p is not None
        assert p.product_name == "ZT411"

    def test_updates_existing(self, db_session: Session, sample_printer: Printer):
        upsert_printers([{"ip": sample_printer.ip, "firmware": "V99.0.0"}])
        db_session.expire_all()
        refreshed = db_session.get(Printer, sample_printer.id)
        assert refreshed.firmware == "V99.0.0"

    def test_preserves_alias(self, db_session: Session, sample_printer: Printer):
        upsert_printers([{"ip": sample_printer.ip, "product_name": "ZT411-new"}])
        db_session.expire_all()
        refreshed = db_session.get(Printer, sample_printer.id)
        assert refreshed.alias == "Test ZT411"  # alias unchanged


class TestCleanupOldJobs:
    def test_removes_old_jobs(self, db_session: Session, sample_printer: Printer, sample_template: LabelTemplate):
        old_job = PrintJob(
            printer_id=sample_printer.id,
            template_id=sample_template.id,
            quantity=1,
            status="sent",
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        db_session.add(old_job)
        db_session.commit()
        removed = cleanup_old_jobs()
        assert removed >= 1

    def test_keeps_recent_jobs(self, db_session: Session, sample_printer: Printer, sample_template: LabelTemplate):
        recent_job = PrintJob(
            printer_id=sample_printer.id,
            template_id=sample_template.id,
            quantity=1,
            status="sent",
        )
        db_session.add(recent_job)
        db_session.commit()
        removed = cleanup_old_jobs()
        assert removed == 0
