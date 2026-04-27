from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db import ApiKey
from app.security import verify_secret


bearer_scheme = HTTPBearer(auto_error=False)


def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> ApiKey:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")

    raw_key = credentials.credentials
    prefix = raw_key[:12]
    keys = db.scalars(select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.is_active.is_(True))).all()
    for api_key in keys:
        if verify_secret(raw_key, api_key.key_hash):
            api_key.last_used_at = datetime.now(timezone.utc)
            db.commit()
            return api_key
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
