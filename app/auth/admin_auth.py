from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db import AdminUser
from app.security import decode_admin_token


def get_admin_user(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("zebra_admin")
    username = decode_admin_token(token) if token else None
    if not username:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    user = db.scalars(select(AdminUser).where(AdminUser.username == username)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user
