from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api_keys import require_api_key
from app.database import get_db
from app.models.db import ApiKey, LabelTemplate
from app.models.schemas import TemplateOut

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateOut])
def list_templates(_: ApiKey = Depends(require_api_key), db: Session = Depends(get_db)):
    return db.scalars(select(LabelTemplate).order_by(LabelTemplate.name)).all()
