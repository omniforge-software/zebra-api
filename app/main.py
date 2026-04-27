import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.models.db import AdminUser
from app.routers import admin, jobs, printers, templates
from app.services.jobs import cleanup_old_jobs_forever, upsert_printers
from app.services.scanner import scan_all

logger = logging.getLogger("zebra_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    seed_admin_user()
    cleanup_task = asyncio.create_task(cleanup_old_jobs_forever())
    scan_task = asyncio.create_task(startup_scan()) if settings.scan_on_startup else None
    try:
        yield
    finally:
        cleanup_task.cancel()
        if scan_task:
            scan_task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(admin.router)
    app.include_router(printers.router)
    app.include_router(templates.router)
    app.include_router(jobs.router)
    return app


def seed_admin_user() -> None:
    import app.database as _db
    settings = get_settings()
    with _db.SessionLocal() as db:
        if db.query(AdminUser).count() == 0:
            db.add(AdminUser(username=settings.admin_username, password_hash=settings.admin_password_hash))
            db.commit()
            logger.info("Seeded initial admin user %s", settings.admin_username)


async def startup_scan() -> None:
    try:
        printers = await scan_all()
        saved = upsert_printers(printers)
        logger.info("Startup scan found %s device(s), saved %s", len(printers), saved)
    except Exception:
        logger.exception("Startup scan failed")


app = create_app()
