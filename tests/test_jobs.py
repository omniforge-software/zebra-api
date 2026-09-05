"""Tests for job service: create_job, process_print_job, upsert_printers, cleanup."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models.db import LabelTemplate, PrintJob, Printer
from app.services.jobs import DIRECT_ZPL_TEMPLATE_ID, cleanup_old_jobs, create_job, process_print_job, upsert_printers
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


class TestDirectZplJobs:
    def test_concurrent_first_submissions(self, tmp_path, monkeypatch):
        engine = create_engine(f"sqlite:///{tmp_path / 'concurrent.db'}")

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        monkeypatch.setattr("app.services.jobs.SessionLocal", sessions)
        with sessions() as db:
            printer = Printer(ip="10.0.0.1")
            db.add(printer)
            db.commit()
            printer_id = printer.id

        ready = Barrier(2)

        @event.listens_for(engine, "before_cursor_execute")
        def synchronize_first_insert(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO label_templates"):
                ready.wait(timeout=10)

        def submit(_):
            return create_job(printer_id, DIRECT_ZPL_TEMPLATE_ID, {"zpl": "^XA^XZ"}, 1, None).id

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                job_ids = list(executor.map(submit, range(2)))
            assert len(set(job_ids)) == 2
            with sessions() as db:
                assert db.query(LabelTemplate).count() == 1
                assert db.query(PrintJob).count() == 2
        finally:
            engine.dispose()

    @pytest.mark.asyncio
    async def test_reuses_template_and_existing_worker(self, db_session, sample_printer):
        zpl = "^XA\n^FO20,20^FDItem 1: café {{literal}}^FS\n^FO20,60^FDItem 2^FS\n^PQ99\n^XZ"
        first = create_job(sample_printer.id, DIRECT_ZPL_TEMPLATE_ID, {"zpl": zpl}, 2, None)
        second = create_job(sample_printer.id, DIRECT_ZPL_TEMPLATE_ID, {"zpl": "^XA^XZ"}, 1, None)
        assert first.template_id == second.template_id == DIRECT_ZPL_TEMPLATE_ID
        assert db_session.query(LabelTemplate).count() == 1
        assert db_session.get(PrintJob, first.id).variables == {"zpl": zpl}

        with patch("app.services.jobs.send_zpl", new_callable=AsyncMock) as send:
            await process_print_job(first.id)
        send.assert_awaited_once_with(sample_printer.ip, zpl.replace("^PQ99", "^PQ2").encode("utf-8"))
        db_session.expire_all()
        job = db_session.get(PrintJob, first.id)
        assert job.status == "sent"
        assert job.started_at is not None
        assert job.completed_at is not None

    @pytest.mark.parametrize("zpl,quantity,error", [
        ("", 1, "end with"),
        ("hello^XZ", 1, "start with"),
        ("^XAhello", 1, "end with"),
        ("^XA^XZ^XA^XZ", 1, "exactly one"),
        ("^XA^PQ2^PQ3^XZ", 1, "at most one"),
        ("^XA^PQoops^XZ", 1, "numeric quantity"),
        ("^XA^PQ2oops^XZ", 1, "numeric quantity"),
        ("^XA^XZ", 0, "at least 1"),
        ("^XA^XZ", 101, "cannot exceed"),
        ("^XA^FD" + "é" * 131_072 + "^FS^XZ", 1, "too large"),
    ])
    def test_invalid_payload_creates_no_rows(self, db_session, sample_printer, zpl, quantity, error):
        with pytest.raises(ValueError, match=error):
            create_job(sample_printer.id, DIRECT_ZPL_TEMPLATE_ID, {"zpl": zpl}, quantity, None)
        assert db_session.query(PrintJob).count() == 0
        assert db_session.query(LabelTemplate).count() == 0

    def test_name_collision_preserves_existing_template(self, db_session, sample_printer):
        template = LabelTemplate(name="Direct ZPL (system)", zpl_body="^XA^XZ", variables=[])
        db_session.add(template)
        db_session.commit()
        with pytest.raises(ValueError, match="reserved"):
            create_job(sample_printer.id, DIRECT_ZPL_TEMPLATE_ID, {"zpl": "^XA^XZ"}, 1, None)
        db_session.expire_all()
        assert db_session.get(LabelTemplate, template.id).zpl_body == "^XA^XZ"
        assert db_session.query(PrintJob).count() == 0

    @pytest.mark.asyncio
    async def test_send_failure_updates_direct_job(self, db_session, sample_printer):
        job = create_job(sample_printer.id, DIRECT_ZPL_TEMPLATE_ID, {"zpl": "^XA^XZ"}, 1, None)
        with patch("app.services.jobs.send_zpl", new_callable=AsyncMock, side_effect=RuntimeError("timeout")):
            await process_print_job(job.id)
        db_session.expire_all()
        job = db_session.get(PrintJob, job.id)
        assert job.status == "failed"
        assert "timeout" in job.error_message
        assert job.completed_at is not None


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
