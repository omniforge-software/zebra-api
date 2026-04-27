from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api_keys import require_api_key
from app.database import get_db
from app.models.db import ApiKey, Printer
from app.models.schemas import PrinterOut
from app.services.jobs import refresh_printer_status, upsert_printers
from app.services.scanner import scan_all

router = APIRouter(prefix="/printers", tags=["printers"])


@router.get("", response_model=list[PrinterOut])
def list_printers(_: ApiKey = Depends(require_api_key), db: Session = Depends(get_db)):
    return db.scalars(select(Printer).order_by(Printer.alias, Printer.friendly_name, Printer.ip)).all()


@router.get("/{printer_id}/status")
async def printer_status(printer_id: str, _: ApiKey = Depends(require_api_key)):
    try:
        return await refresh_printer_status(printer_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/scan")
async def scan_printers(_: ApiKey = Depends(require_api_key)):
    printers = await scan_all()
    return {"found": len(printers), "saved": upsert_printers(printers)}
