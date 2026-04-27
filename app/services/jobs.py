import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.config import get_settings
from app.database import SessionLocal
from app.models.db import LabelTemplate, PrintJob, Printer
from app.services.printer_io import query_sgd, send_zpl
from app.services.zpl_render import render_zpl


async def process_print_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(PrintJob, job_id)
        if not job:
            return
        printer = db.get(Printer, job.printer_id)
        template = db.get(LabelTemplate, job.template_id)
        if not printer or not template:
            job.status = "failed"
            job.error_message = "Printer or template no longer exists"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return
        job.status = "sending"
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        try:
            zpl = render_zpl(template.zpl_body, template.variables, job.variables, job.quantity)
            await send_zpl(printer.ip, zpl)
            job.status = "sent"
            job.error_message = None
            printer.is_online = True
            printer.last_seen_at = datetime.now(timezone.utc)
        except Exception as exc:
            job.status = "failed"
            job.error_message = str(exc)
            printer.is_online = False
        finally:
            job.completed_at = datetime.now(timezone.utc)
            db.commit()


async def refresh_printer_status(printer_id: str) -> dict:
    with SessionLocal() as db:
        printer = db.get(Printer, printer_id)
        if not printer:
            raise ValueError("Printer not found")
        status: dict[str, str | bool] = {"online": False}
        try:
            sgd_map = {
                "device.product_name": "product_name",
                "device.friendly_name": "friendly_name",
                "device.status": None,
                "media.status": None,
                "ezpl.print_width": "print_width",
                "ezpl.label_length": "label_length",
                "media.type": "media_type",
                "media.out": None,
                "odometer.total_label_count": "odometer",
            }
            for var, field in sgd_map.items():
                value = await query_sgd(printer.ip, var)
                if value:
                    cleaned = value.strip().strip('"')
                    status[var] = cleaned
                    if field:
                        setattr(printer, field, cleaned)
            # Normalise media_out to bool and store
            raw_out = status.get("media.out", "")
            printer.media_out = raw_out.lower() == "yes"
            status["media_out"] = printer.media_out
            status["online"] = True
            printer.is_online = True
            printer.last_seen_at = datetime.now(timezone.utc)
        except Exception as exc:
            status["error"] = str(exc)
            printer.is_online = False
        printer.last_status = status
        printer.last_status_at = datetime.now(timezone.utc)
        db.commit()
        return status


async def cleanup_old_jobs_forever() -> None:
    settings = get_settings()
    while True:
        cleanup_old_jobs()
        await asyncio.sleep(settings.cleanup_interval_minutes * 60)


def cleanup_old_jobs() -> int:
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.job_retention_days)
    with SessionLocal() as db:
        result = db.execute(delete(PrintJob).where(PrintJob.created_at < cutoff))
        db.commit()
        return result.rowcount or 0


def create_job(printer_id: str, template_id: str, variables: dict, quantity: int, api_key_id: str | None) -> PrintJob:
    settings = get_settings()
    if quantity > settings.max_print_quantity:
        raise ValueError(f"Quantity cannot exceed {settings.max_print_quantity}")
    with SessionLocal() as db:
        if not db.get(Printer, printer_id):
            raise ValueError("Printer not found")
        if not db.get(LabelTemplate, template_id):
            raise ValueError("Template not found")
        job = PrintJob(
            printer_id=printer_id,
            template_id=template_id,
            variables=variables,
            quantity=quantity,
            api_key_id=api_key_id,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job


def upsert_printers(printers: list[dict]) -> int:
    with SessionLocal() as db:
        count = 0
        for printer_info in printers:
            printer = db.scalars(select(Printer).where(Printer.ip == printer_info["ip"])).first()
            if printer is None:
                printer = Printer(ip=printer_info["ip"])
                db.add(printer)
            for field in [
                "friendly_name",
                "product_name",
                "firmware",
                "print_width",
                "ports_open",
                "is_online",
                "last_seen_at",
            ]:
                if field in printer_info:
                    setattr(printer, field, printer_info[field])
            count += 1
        db.commit()
        return count
