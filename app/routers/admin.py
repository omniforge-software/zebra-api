import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.admin_auth import get_admin_user
from app.config import get_settings
from app.database import get_db
from app.models.db import AdminUser, ApiKey, LabelTemplate, PrintJob, Printer
from app.security import create_admin_token, create_api_key, hash_secret, verify_secret
from app.services.jobs import cleanup_old_jobs, create_job, process_print_job, refresh_printer_status, upsert_printers
from app.services.scanner import scan_all
from app.services.zpl_render import extract_variables, render_zpl, validate_zpl

router = APIRouter(tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.scalars(select(AdminUser).where(AdminUser.username == username)).first()
    if not user or not verify_secret(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid username or password"}, status_code=401)
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        "zebra_admin",
        create_admin_token(username),
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("zebra_admin")
    return response


@router.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request, _: AdminUser = Depends(get_admin_user), db: Session = Depends(get_db)):
    stats = {
        "printers": db.scalar(select(func.count()).select_from(Printer)) or 0,
        "online": db.scalar(select(func.count()).select_from(Printer).where(Printer.is_online.is_(True))) or 0,
        "templates": db.scalar(select(func.count()).select_from(LabelTemplate)) or 0,
        "jobs": db.scalar(select(func.count()).select_from(PrintJob)) or 0,
        "failed": db.scalar(select(func.count()).select_from(PrintJob).where(PrintJob.status == "failed")) or 0,
    }
    recent_jobs = db.scalars(select(PrintJob).order_by(PrintJob.created_at.desc()).limit(10)).all()
    return templates.TemplateResponse(request, "dashboard.html", {"stats": stats, "jobs": recent_jobs})


@router.get("/admin/printers", response_class=HTMLResponse)
def printers_page(request: Request, _: AdminUser = Depends(get_admin_user), db: Session = Depends(get_db)):
    printers = db.scalars(select(Printer).order_by(Printer.alias, Printer.friendly_name, Printer.ip)).all()
    return templates.TemplateResponse(request, "printers.html", {"printers": printers})


@router.post("/admin/printers/scan")
async def admin_scan(_: AdminUser = Depends(get_admin_user)):
    printers = await scan_all()
    upsert_printers(printers)
    return RedirectResponse("/admin/printers", status_code=303)


@router.post("/admin/printers/{printer_id}/alias")
def update_alias(printer_id: str, alias: str = Form(""), _: AdminUser = Depends(get_admin_user), db: Session = Depends(get_db)):
    printer = db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    printer.alias = alias.strip() or None
    db.commit()
    return RedirectResponse("/admin/printers", status_code=303)


@router.post("/admin/printers/{printer_id}/status")
async def admin_refresh_status(printer_id: str, _: AdminUser = Depends(get_admin_user)):
    await refresh_printer_status(printer_id)
    return RedirectResponse("/admin/printers", status_code=303)


@router.get("/admin/templates", response_class=HTMLResponse)
def templates_page(request: Request, _: AdminUser = Depends(get_admin_user), db: Session = Depends(get_db)):
    label_templates = db.scalars(select(LabelTemplate).order_by(LabelTemplate.name)).all()
    printers = db.scalars(select(Printer).order_by(Printer.alias, Printer.friendly_name, Printer.ip)).all()
    return templates.TemplateResponse(
        request, "templates.html", {"templates": label_templates, "printers": printers, "error": None}
    )


@router.post("/admin/templates")
def create_template(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    zpl_body: str = Form(...),
    _: AdminUser = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    try:
        validate_zpl(zpl_body)
    except ValueError as exc:
        label_templates = db.scalars(select(LabelTemplate).order_by(LabelTemplate.name)).all()
        printers = db.scalars(select(Printer).order_by(Printer.alias, Printer.friendly_name, Printer.ip)).all()
        return templates.TemplateResponse(
            request,
            "templates.html",
            {"templates": label_templates, "printers": printers, "error": str(exc)},
            status_code=400,
        )
    db.add(LabelTemplate(name=name.strip(), description=description.strip() or None, zpl_body=zpl_body, variables=extract_variables(zpl_body)))
    db.commit()
    return RedirectResponse("/admin/templates", status_code=303)


@router.post("/admin/templates/{template_id}/delete")
def delete_template(template_id: str, _: AdminUser = Depends(get_admin_user), db: Session = Depends(get_db)):
    template = db.get(LabelTemplate, template_id)
    if template:
        db.delete(template)
        db.commit()
    return RedirectResponse("/admin/templates", status_code=303)


class _PreviewRequest(BaseModel):
    zpl_body: str
    variables: dict[str, str] = {}


@router.post("/admin/templates/preview")
def preview_template(body: _PreviewRequest, _: AdminUser = Depends(get_admin_user)):
    """Render a ZPL template with supplied variables and return the result as JSON.
    Used by the admin UI preview panel."""
    try:
        declared = extract_variables(body.zpl_body)
        # Fill any missing variables with a blank string so preview always renders
        values = {v: body.variables.get(v, "") for v in declared}
        rendered_bytes = render_zpl(body.zpl_body, declared, values, quantity=1)
        return JSONResponse({"rendered": rendered_bytes.decode("utf-8"), "error": None})
    except ValueError as exc:
        return JSONResponse({"rendered": None, "error": str(exc)})


@router.post("/admin/templates/{template_id}/test-print")
def test_print(
    template_id: str,
    printer_id: str = Form(...),
    quantity: int = Form(1),
    _: AdminUser = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    template = db.get(LabelTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    variables = {name: f"TEST {name}" for name in template.variables}
    job = create_job(printer_id, template_id, variables, quantity, None)
    asyncio.create_task(process_print_job(job.id))
    return RedirectResponse("/admin/jobs", status_code=303)


@router.get("/admin/keys", response_class=HTMLResponse)
def keys_page(request: Request, raw_key: str | None = None, _: AdminUser = Depends(get_admin_user), db: Session = Depends(get_db)):
    keys = db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    return templates.TemplateResponse(request, "keys.html", {"keys": keys, "raw_key": raw_key})


@router.post("/admin/keys")
def create_key(name: str = Form(...), _: AdminUser = Depends(get_admin_user), db: Session = Depends(get_db)):
    raw_key, prefix = create_api_key()
    db.add(ApiKey(name=name.strip(), key_hash=hash_secret(raw_key), prefix=prefix))
    db.commit()
    return RedirectResponse(f"/admin/keys?raw_key={raw_key}", status_code=303)


@router.post("/admin/keys/{key_id}/revoke")
def revoke_key(key_id: str, _: AdminUser = Depends(get_admin_user), db: Session = Depends(get_db)):
    api_key = db.get(ApiKey, key_id)
    if api_key:
        api_key.is_active = False
        db.commit()
    return RedirectResponse("/admin/keys", status_code=303)


@router.get("/admin/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, _: AdminUser = Depends(get_admin_user), db: Session = Depends(get_db)):
    jobs = db.scalars(select(PrintJob).order_by(PrintJob.created_at.desc()).limit(100)).all()
    return templates.TemplateResponse(request, "jobs.html", {"jobs": jobs})


@router.post("/admin/jobs/cleanup")
def cleanup_jobs(_: AdminUser = Depends(get_admin_user)):
    cleanup_old_jobs()
    return RedirectResponse("/admin/jobs", status_code=303)


@router.get("/admin/settings", response_class=HTMLResponse)
def settings_page(request: Request, _: AdminUser = Depends(get_admin_user)):
    return templates.TemplateResponse(request, "settings.html", {"settings": get_settings()})
