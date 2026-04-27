import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.api_keys import require_api_key
from app.database import get_db
from app.models.db import ApiKey, PrintJob
from app.models.schemas import PrintJobOut, PrintRequest
from app.services.jobs import create_job, process_print_job

router = APIRouter(tags=["jobs"])


@router.post("/print", response_model=PrintJobOut, status_code=202)
async def submit_print(request: PrintRequest, api_key: ApiKey = Depends(require_api_key)):
    try:
        job = create_job(request.printer_id, request.template_id, request.variables, request.quantity, api_key.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    asyncio.create_task(process_print_job(job.id))
    return job


@router.get("/jobs/{job_id}", response_model=PrintJobOut)
def get_job(job_id: str, _: ApiKey = Depends(require_api_key), db: Session = Depends(get_db)):
    job = db.get(PrintJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
